"""CEN connection enrichment service for GridAssets.

This service builds a preview that crosses normalized CEN connection rows with
existing projects, and applies only safe enrichment rows.

Rules:
- CEN connection files never create projects.
- CNE remains the primary source for project creation.
- CEN connection files can enrich existing projects with NUP and relevant dates.
- Ambiguous or weak matches are previewed but not applied.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from database.db_connection import get_sqlserver_engine
from parsers.cen_connection_files_parse import normalize_project_name
from services.project_status_service import (
    IN_SERVICE_STATUS_NAME,
    PLANNED_STATUS_NAME,
    UNDER_CONSTRUCTION_STATUS_NAME,
    ProjectStatusService,
)


SOURCE_NAME = "CEN - Conexiones"
STATUS_ACTION_CANCELLED = "status_cancelled"
CANCELLED_STATUS_NAME = "Cancelled"

DATE_FIELD_TO_MILESTONE = {
    "commissioning_actual": "Commissioning_Actual",
    "commissioning_estimated": "Commissioning_Estimated",
    "cod_actual": "COD_Actual",
    "cod_estimated": "COD_Estimated",
}

AUTO_PROPOSED_STATUSES = {"matched_by_nup", "matched_by_name"}
SELECTABLE_REVIEW_STATUSES = {"candidate_by_name"}
APPLICABLE_STATUSES = AUTO_PROPOSED_STATUSES | SELECTABLE_REVIEW_STATUSES

TYPE_COMPATIBILITY = {
    "transmission": {"transmission"},
    "bess": {"bess"},
    "pmgd": {"der", "generation"},
    "generation_or_bess": {"generation", "bess", "der"},
    "unknown": set(),
}


@dataclass(frozen=True)
class MatchThresholds:
    """Thresholds used by name matching."""

    safe_name_score: float = 0.98
    review_name_score: float = 0.94
    ambiguity_delta: float = 0.03


class CENConnectionEnrichmentService:
    """Preview and apply CEN connection enrichment over existing projects."""

    def __init__(
        self,
        engine: Optional[Engine] = None,
        thresholds: Optional[MatchThresholds] = None,
    ) -> None:
        self.engine = engine or get_sqlserver_engine()
        self.thresholds = thresholds or MatchThresholds()

    # ------------------------------------------------------------------
    # Public preview API
    # ------------------------------------------------------------------
    def build_match_preview(self, records: pd.DataFrame) -> pd.DataFrame:
        """Return a row-level preview of how normalized records match the DB."""
        if records.empty:
            return pd.DataFrame()

        projects = self._load_projects_dataframe()
        existing_dates = self._load_existing_dates_dataframe()

        preview_rows: list[dict[str, Any]] = []
        for _, record in records.iterrows():
            match = self._match_record(record, projects)
            action = self._build_action_preview(record, match, existing_dates)
            preview_rows.append({**self._record_preview_base(record), **match, **action})

        preview = pd.DataFrame(preview_rows)
        return self._add_ui_fields(preview)

    # ------------------------------------------------------------------
    # Public apply API
    # ------------------------------------------------------------------
    def apply_safe_enrichment(self, preview: pd.DataFrame) -> Dict[str, Any]:
        """Apply enrichment only for selected/applicable preview rows.

        This method never creates projects. It updates Project.NUP only when the
        current value is empty and upserts CEN connection relevant dates using
        SourceName = "CEN - Conexiones".
        """
        summary = {
            "rows_received": int(len(preview)) if preview is not None else 0,
            "rows_safe": 0,
            "rows_applied": 0,
            "rows_skipped": 0,
            "projects_enriched": 0,
            "nup_updated": 0,
            "nup_conflicts": 0,
            "dates_created": 0,
            "dates_updated": 0,
            "dates_unchanged": 0,
            "status_updated_to_in_service": 0,
            "status_updated_to_under_construction": 0,
            "status_updated_to_planned": 0,
            "status_updated_to_cancelled": 0,
            "status_cancelled_conflicts": 0,
            "errors": 0,
        }
        details: list[dict[str, Any]] = []
        enriched_project_ids: set[int] = set()

        if preview is None or preview.empty:
            return {"summary": summary, "details": pd.DataFrame(details)}

        # Last safety gate: only rows with applicable statuses can write.
        safe_rows = preview.loc[preview["match_status"].isin(APPLICABLE_STATUSES)].copy()
        summary["rows_safe"] = int(len(safe_rows))

        if safe_rows.empty:
            summary["rows_skipped"] = int(len(preview))
            return {"summary": summary, "details": pd.DataFrame(details)}

        with self.engine.begin() as conn:
            source_id = self._ensure_source(conn, SOURCE_NAME)
            milestone_ids = {
                name: self._ensure_milestone(conn, name)
                for name in DATE_FIELD_TO_MILESTONE.values()
            }

            for _, row in safe_rows.iterrows():
                try:
                    project_id = _to_int_or_none(row.get("matched_project_id"))
                    if project_id is None:
                        summary["rows_skipped"] += 1
                        details.append(self._detail(row, "omitted", "Fila sin ID de proyecto BD."))
                        continue

                    record_action = _clean_text(row.get("record_action")) or "date_enrichment"

                    if record_action == STATUS_ACTION_CANCELLED:
                        status_result = ProjectStatusService.set_cancelled_if_no_cod_actual(conn, project_id)
                        if status_result.status_changed:
                            summary["status_updated_to_cancelled"] += 1
                            summary["rows_applied"] += 1
                            enriched_project_ids.add(project_id)
                            details.append(self._detail(row, "applied", "Estado actualizado a Cancelled."))
                        elif status_result.blocked_by_cod_actual:
                            summary["status_cancelled_conflicts"] += 1
                            summary["rows_skipped"] += 1
                            details.append(self._detail(row, "conflict", "No se actualizó a Cancelled porque el proyecto tiene COD_Actual."))
                        else:
                            summary["rows_skipped"] += 1
                            details.append(self._detail(row, "unchanged", status_result.message))
                        continue

                    nup_result = self._apply_nup(conn, project_id, row)
                    if nup_result == "updated":
                        summary["nup_updated"] += 1
                    elif nup_result == "conflict":
                        summary["nup_conflicts"] += 1

                    date_result = self._apply_dates(conn, project_id, source_id, milestone_ids, row)
                    summary["dates_created"] += date_result["created"]
                    summary["dates_updated"] += date_result["updated"]
                    summary["dates_unchanged"] += date_result["unchanged"]
                    summary["status_updated_to_in_service"] += date_result.get("status_updated_to_in_service", 0)
                    summary["status_updated_to_under_construction"] += date_result.get("status_updated_to_under_construction", 0)
                    summary["status_updated_to_planned"] += date_result.get("status_updated_to_planned", 0)

                    changed = (
                        nup_result == "updated"
                        or date_result["created"] > 0
                        or date_result["updated"] > 0
                        or date_result.get("status_updated_to_in_service", 0) > 0
                        or date_result.get("status_updated_to_under_construction", 0) > 0
                        or date_result.get("status_updated_to_planned", 0) > 0
                    )
                    if changed:
                        summary["rows_applied"] += 1
                        enriched_project_ids.add(project_id)
                        details.append(self._detail(row, "applied", "Enriquecimiento aplicado."))
                    else:
                        summary["rows_skipped"] += 1
                        details.append(self._detail(row, "unchanged", "Sin cambios requeridos en base de datos."))
                except Exception as exc:
                    summary["errors"] += 1
                    details.append(self._detail(row, "error", str(exc)))

        # Rows not in applicable statuses were received but not safe to write.
        summary["rows_skipped"] += max(0, int(len(preview)) - int(len(safe_rows)))
        summary["projects_enriched"] = int(len(enriched_project_ids))

        return {"summary": summary, "details": pd.DataFrame(details)}


    def apply_selected_enrichment(
        self,
        preview: pd.DataFrame,
        selected_row_ids: list[int],
    ) -> Dict[str, Any]:
        """Apply enrichment only to explicitly selected preview rows.

        Rows can come from automatic proposals or manual validation. Ambiguous rows
        without a matched ProjectID are skipped by apply_safe_enrichment.
        """
        if preview is None or preview.empty:
            return self.apply_safe_enrichment(pd.DataFrame())

        if not selected_row_ids:
            return self.apply_safe_enrichment(preview.iloc[0:0].copy())

        work = preview.copy()
        if "preview_row_id" not in work.columns:
            work = work.reset_index(drop=True)
            work["preview_row_id"] = work.index.astype(int)

        selected = work.loc[work["preview_row_id"].isin(set(selected_row_ids))].copy()
        return self.apply_safe_enrichment(selected)


    @staticmethod
    def _add_ui_fields(preview: pd.DataFrame) -> pd.DataFrame:
        """Add stable row IDs and UI labels used by the Streamlit view."""
        if preview is None or preview.empty:
            return preview

        result = preview.copy().reset_index(drop=True)
        result["preview_row_id"] = result.index.astype(int)
        result["match_group"] = result["match_status"].apply(_match_group)
        result["match_group_label"] = result["match_group"].map(
            {
                "auto_proposed": "Propuestos automáticamente",
                "needs_validation": "Requieren validación",
                "no_candidate": "Sin candidato suficiente",
            }
        ).fillna("Completo")
        result["match_status_label"] = result["match_status"].apply(_match_status_label)
        result["score_display"] = result["name_score"].apply(_score_display)
        result["date_changes_text"] = result.apply(_date_changes_text, axis=1)
        result["action_summary"] = result.apply(_action_summary, axis=1)
        result["can_apply"] = result["match_status"].isin(APPLICABLE_STATUSES) & result[
            "matched_project_id"
        ].notna()
        result["default_apply"] = result["match_status"].isin(AUTO_PROPOSED_STATUSES) & result[
            "matched_project_id"
        ].notna()
        return result

    # ------------------------------------------------------------------
    # DB loaders
    # ------------------------------------------------------------------
    def _load_projects_dataframe(self) -> pd.DataFrame:
        query = text(
            """
            SELECT
                ProjectID AS project_id,
                ProjectName AS project_name_db,
                NUP AS nup_db,
                project_discriminator AS project_type_db
            FROM Project
            ORDER BY ProjectID
            """
        )
        with self.engine.connect() as conn:
            projects = pd.read_sql(query, conn)

        if projects.empty:
            return projects

        projects["nup_db"] = projects["nup_db"].apply(_normalize_nup)
        projects["project_type_db"] = projects["project_type_db"].apply(_clean_text)
        projects["normalized_project_name_db"] = projects["project_name_db"].apply(
            normalize_project_name
        )
        return projects

    def _load_existing_dates_dataframe(self) -> pd.DataFrame:
        query = text(
            """
            SELECT
                rd.ProjectID AS project_id,
                mt.MilestoneName AS milestone_name,
                s.SourceName AS source_name,
                rd.DateValue AS date_value
            FROM RelevantDate rd
            INNER JOIN MilestoneType mt ON rd.MilestoneTypeID = mt.MilestoneTypeID
            LEFT JOIN Source s ON rd.SourceID = s.SourceID
            WHERE s.SourceName = :source_name
            """
        )
        with self.engine.connect() as conn:
            dates = pd.read_sql(query, conn, params={"source_name": SOURCE_NAME})

        if dates.empty:
            return dates
        dates["date_value"] = dates["date_value"].apply(_to_date_or_none)
        return dates

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    def _match_record(self, record: pd.Series, projects: pd.DataFrame) -> Dict[str, Any]:
        if projects.empty:
            return self._empty_match("not_found", "No projects were found in the database.")

        connection_type = _clean_text(record.get("connection_project_type"))
        compatible_types = TYPE_COMPATIBILITY.get(connection_type or "", set())
        compatible_projects = projects
        if compatible_types:
            compatible_projects = projects.loc[
                projects["project_type_db"].isin(compatible_types)
            ].copy()

        nup = _normalize_nup(record.get("nup"))
        if nup:
            by_nup = compatible_projects.loc[compatible_projects["nup_db"] == nup]
            if len(by_nup) == 1:
                return self._project_match(
                    by_nup.iloc[0],
                    "matched_by_nup",
                    1.0,
                    1,
                    "Unique match by NUP and compatible type.",
                )
            if len(by_nup) > 1:
                return self._ambiguous_match(
                    by_nup,
                    "ambiguous_nup",
                    "Multiple compatible projects share the same NUP.",
                )

            global_by_nup = projects.loc[projects["nup_db"] == nup]
            if len(global_by_nup) > 0:
                return self._ambiguous_match(
                    global_by_nup,
                    "nup_type_mismatch",
                    "NUP exists in DB, but not in a compatible project type.",
                )

        normalized_name = _clean_text(record.get("normalized_project_name"))
        if not normalized_name:
            normalized_name = normalize_project_name(record.get("project_name"))
        if not normalized_name:
            return self._empty_match("no_project_name", "No usable project name in connection row.")

        exact = compatible_projects.loc[
            compatible_projects["normalized_project_name_db"] == normalized_name
        ]
        if len(exact) == 1:
            return self._project_match(
                exact.iloc[0],
                "matched_by_name",
                1.0,
                1,
                "Exact normalized-name match and compatible type.",
            )
        if len(exact) > 1:
            return self._ambiguous_match(
                exact,
                "ambiguous_name",
                "Multiple compatible projects match the normalized name exactly.",
            )

        candidates = self._score_name_candidates(normalized_name, compatible_projects)
        if candidates.empty:
            return self._empty_match("not_found", "No compatible name candidates found.")

        best = candidates.iloc[0]
        best_score = float(best["name_score"])
        candidate_count = int(len(candidates))
        if len(candidates) > 1:
            second_score = float(candidates.iloc[1]["name_score"])
            if best_score - second_score <= self.thresholds.ambiguity_delta:
                return self._ambiguous_match(
                    candidates,
                    "ambiguous_name",
                    "Several name candidates are too close to choose automatically.",
                )

        if best_score >= self.thresholds.safe_name_score:
            return self._project_match(
                best,
                "matched_by_name",
                best_score,
                candidate_count,
                "High-confidence normalized-name match and compatible type.",
                candidates,
            )

        if best_score >= self.thresholds.review_name_score:
            return self._project_match(
                best,
                "candidate_by_name",
                best_score,
                candidate_count,
                "Close name candidate. Review manually before applying.",
                candidates,
            )

        return self._empty_match("not_found", "No sufficiently close compatible name candidate found.")

    def _score_name_candidates(self, normalized_name: str, projects: pd.DataFrame) -> pd.DataFrame:
        if projects.empty:
            return pd.DataFrame()
        scored = projects.copy()
        scored["name_score"] = scored["normalized_project_name_db"].apply(
            lambda candidate: _name_similarity(normalized_name, candidate)
        )
        scored = scored.loc[scored["name_score"] >= 0.65]
        return scored.sort_values("name_score", ascending=False).head(5)

    @staticmethod
    def _project_match(
        project_row: pd.Series,
        status: str,
        score: float,
        candidate_count: int,
        comment: str,
        candidates: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        candidate_info = CENConnectionEnrichmentService._candidate_context(candidates)
        return {
            "match_status": status,
            "matched_project_id": project_row.get("project_id"),
            "matched_project_name": project_row.get("project_name_db"),
            "matched_project_type": project_row.get("project_type_db"),
            "matched_project_nup": project_row.get("nup_db"),
            "name_score": round(float(score), 3),
            "candidate_count": candidate_count,
            "top_candidates": _first_non_empty(candidate_info.get("top_candidates"), project_row.get("project_name_db")),
            "second_candidate_name": candidate_info.get("second_candidate_name"),
            "second_candidate_score": candidate_info.get("second_candidate_score"),
            "third_candidate_name": candidate_info.get("third_candidate_name"),
            "third_candidate_score": candidate_info.get("third_candidate_score"),
            "candidate_score_delta": candidate_info.get("candidate_score_delta"),
            "match_comment": comment,
        }

    @staticmethod
    def _candidate_context(candidates: Optional[pd.DataFrame]) -> Dict[str, Any]:
        if candidates is None or candidates.empty:
            return {
                "top_candidates": pd.NA,
                "second_candidate_name": pd.NA,
                "second_candidate_score": pd.NA,
                "third_candidate_name": pd.NA,
                "third_candidate_score": pd.NA,
                "candidate_score_delta": pd.NA,
            }

        safe = candidates.copy().head(3).reset_index(drop=True)
        names = safe.get("project_name_db", pd.Series(dtype=str)).fillna("").astype(str).tolist()
        scores = pd.to_numeric(safe.get("name_score", pd.Series(dtype=float)), errors="coerce").tolist()

        second_name = names[1] if len(names) > 1 else pd.NA
        second_score = round(float(scores[1]), 3) if len(scores) > 1 and pd.notna(scores[1]) else pd.NA
        third_name = names[2] if len(names) > 2 else pd.NA
        third_score = round(float(scores[2]), 3) if len(scores) > 2 and pd.notna(scores[2]) else pd.NA
        delta = pd.NA
        if len(scores) > 1 and pd.notna(scores[0]) and pd.notna(scores[1]):
            delta = round(float(scores[0]) - float(scores[1]), 3)

        ranked = []
        for index, row in safe.iterrows():
            name = row.get("project_name_db")
            score_value = row.get("name_score")
            if pd.notna(name):
                if pd.notna(score_value):
                    ranked.append(f"{index + 1}. {name} ({float(score_value):.3f})")
                else:
                    ranked.append(f"{index + 1}. {name}")

        return {
            "top_candidates": " | ".join(ranked) if ranked else pd.NA,
            "second_candidate_name": second_name,
            "second_candidate_score": second_score,
            "third_candidate_name": third_name,
            "third_candidate_score": third_score,
            "candidate_score_delta": delta,
        }

    @staticmethod
    def _ambiguous_match(projects: pd.DataFrame, status: str, comment: str) -> Dict[str, Any]:
        names = projects.get("project_name_db", pd.Series(dtype=str)).head(5).tolist()
        best_score = pd.NA
        if "name_score" in projects.columns and not projects.empty:
            best_score = round(float(projects.iloc[0]["name_score"]), 3)
        candidate_info = CENConnectionEnrichmentService._candidate_context(projects)
        return {
            "match_status": status,
            "matched_project_id": pd.NA,
            "matched_project_name": pd.NA,
            "matched_project_type": pd.NA,
            "matched_project_nup": pd.NA,
            "name_score": best_score,
            "candidate_count": int(len(projects)),
            "top_candidates": _first_non_empty(candidate_info.get("top_candidates"), " | ".join(str(name) for name in names)),
            "second_candidate_name": candidate_info.get("second_candidate_name"),
            "second_candidate_score": candidate_info.get("second_candidate_score"),
            "third_candidate_name": candidate_info.get("third_candidate_name"),
            "third_candidate_score": candidate_info.get("third_candidate_score"),
            "candidate_score_delta": candidate_info.get("candidate_score_delta"),
            "match_comment": comment,
        }

    @staticmethod
    def _empty_match(status: str, comment: str) -> Dict[str, Any]:
        return {
            "match_status": status,
            "matched_project_id": pd.NA,
            "matched_project_name": pd.NA,
            "matched_project_type": pd.NA,
            "matched_project_nup": pd.NA,
            "name_score": pd.NA,
            "candidate_count": 0,
            "top_candidates": pd.NA,
            "second_candidate_name": pd.NA,
            "second_candidate_score": pd.NA,
            "third_candidate_name": pd.NA,
            "third_candidate_score": pd.NA,
            "candidate_score_delta": pd.NA,
            "match_comment": comment,
        }

    # ------------------------------------------------------------------
    # Action preview
    # ------------------------------------------------------------------
    def _build_action_preview(
        self,
        record: pd.Series,
        match: Dict[str, Any],
        existing_dates: pd.DataFrame,
    ) -> Dict[str, Any]:
        record_action = _clean_text(record.get("record_action")) or "date_enrichment"
        if match["match_status"] not in APPLICABLE_STATUSES:
            return {
                "is_safe_to_apply": False,
                "would_update_nup": False,
                "date_values_in_file": self._count_date_values(record),
                "date_changes_proposed": 0,
                "action_proposed": "review_only",
            }

        if record_action == STATUS_ACTION_CANCELLED:
            return {
                "is_safe_to_apply": True,
                "would_update_nup": False,
                "date_values_in_file": 0,
                "date_changes_proposed": 0,
                "action_proposed": "update_status_cancelled",
            }

        project_id = _to_int_or_none(match.get("matched_project_id"))
        nup_file = _normalize_nup(record.get("nup"))
        nup_db = _normalize_nup(match.get("matched_project_nup"))
        would_update_nup = bool(nup_file and not nup_db)

        date_changes = 0
        if project_id is not None:
            for field, milestone_name in DATE_FIELD_TO_MILESTONE.items():
                incoming = _to_date_or_none(record.get(field))
                if incoming is None:
                    continue
                existing = self._find_existing_date(existing_dates, project_id, milestone_name)
                if existing is None or existing != incoming:
                    date_changes += 1

        actions = []
        if would_update_nup:
            actions.append("update_nup")
        if date_changes:
            actions.append("upsert_dates")

        return {
            "is_safe_to_apply": True,
            "would_update_nup": would_update_nup,
            "date_values_in_file": self._count_date_values(record),
            "date_changes_proposed": date_changes,
            "action_proposed": "+".join(actions) if actions else "no_update_needed",
        }

    @staticmethod
    def _find_existing_date(existing_dates: pd.DataFrame, project_id: int, milestone_name: str) -> Optional[date]:
        if existing_dates.empty:
            return None
        mask = (
            (existing_dates["project_id"] == project_id)
            & (existing_dates["milestone_name"] == milestone_name)
        )
        rows = existing_dates.loc[mask]
        if rows.empty:
            return None
        return _to_date_or_none(rows.iloc[0]["date_value"])

    @staticmethod
    def _count_date_values(record: pd.Series) -> int:
        return sum(
            1
            for field in DATE_FIELD_TO_MILESTONE
            if _to_date_or_none(record.get(field)) is not None
        )

    @staticmethod
    def _record_preview_base(record: pd.Series) -> Dict[str, Any]:
        return {
            "source_detail": record.get("source_detail"),
            "source_sheet": record.get("source_sheet"),
            "row_number": record.get("row_number"),
            "record_action": record.get("record_action"),
            "target_status": record.get("target_status"),
            "connection_project_type": record.get("connection_project_type"),
            "nup": _normalize_nup(record.get("nup")),
            "project_name": record.get("project_name"),
            "company": record.get("company"),
            "region": record.get("region"),
            "commune": record.get("commune"),
            "technology": record.get("technology"),
            "commissioning_actual": record.get("commissioning_actual"),
            "commissioning_estimated": record.get("commissioning_estimated"),
            "cod_actual": record.get("cod_actual"),
            "cod_estimated": record.get("cod_estimated"),
        }

    # ------------------------------------------------------------------
    # Apply helpers
    # ------------------------------------------------------------------
    def _apply_nup(self, conn, project_id: int, row: pd.Series) -> str:
        nup_file = _normalize_nup(row.get("nup"))
        if not nup_file:
            return "empty"

        current = conn.execute(
            text("SELECT NUP FROM Project WHERE ProjectID = :project_id"),
            {"project_id": project_id},
        ).scalar()
        nup_db = _normalize_nup(current)

        if nup_db == nup_file:
            return "unchanged"
        if nup_db:
            return "conflict"

        nup_int = _to_int_or_none(nup_file)
        if nup_int is None:
            return "invalid"

        conn.execute(
            text("UPDATE Project SET NUP = :nup WHERE ProjectID = :project_id"),
            {"nup": nup_int, "project_id": project_id},
        )
        return "updated"

    def _apply_dates(
        self,
        conn,
        project_id: int,
        source_id: int,
        milestone_ids: Dict[str, int],
        row: pd.Series,
    ) -> Dict[str, int]:
        result = {
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "status_updated_to_in_service": 0,
            "status_updated_to_under_construction": 0,
            "status_updated_to_planned": 0,
        }
        now = datetime.now()

        for field, milestone_name in DATE_FIELD_TO_MILESTONE.items():
            incoming = _to_date_or_none(row.get(field))
            if incoming is None:
                continue

            dt_value = datetime.combine(incoming, datetime.min.time())
            milestone_id = milestone_ids[milestone_name]
            existing = conn.execute(
                text(
                    """
                    SELECT RelevantDateID, DateValue
                    FROM RelevantDate
                    WHERE ProjectID = :project_id
                      AND MilestoneTypeID = :milestone_id
                      AND SourceID = :source_id
                    """
                ),
                {
                    "project_id": project_id,
                    "milestone_id": milestone_id,
                    "source_id": source_id,
                },
            ).mappings().first()

            if existing is None:
                conn.execute(
                    text(
                        """
                        INSERT INTO RelevantDate
                            (ProjectID, MilestoneTypeID, SourceID, DateValue, ExtractedAt)
                        VALUES
                            (:project_id, :milestone_id, :source_id, :date_value, :extracted_at)
                        """
                    ),
                    {
                        "project_id": project_id,
                        "milestone_id": milestone_id,
                        "source_id": source_id,
                        "date_value": dt_value,
                        "extracted_at": now,
                    },
                )
                result["created"] += 1
                continue

            existing_date = _to_date_or_none(existing["DateValue"])
            if existing_date == incoming:
                result["unchanged"] += 1
                continue

            conn.execute(
                text(
                    """
                    UPDATE RelevantDate
                    SET DateValue = :date_value,
                        ExtractedAt = :extracted_at
                    WHERE RelevantDateID = :relevant_date_id
                    """
                ),
                {
                    "date_value": dt_value,
                    "extracted_at": now,
                    "relevant_date_id": existing["RelevantDateID"],
                },
            )
            result["updated"] += 1

        # Synchronize project status after any written date change.
        # This keeps status rules centralized and reusable across CEN, PGP, SEO
        # and future enrichment processes.
        if result["created"] > 0 or result["updated"] > 0:
            status_result = ProjectStatusService.sync_project_status_from_dates(conn, project_id)
            if status_result.status_changed:
                if status_result.new_status_name == IN_SERVICE_STATUS_NAME:
                    result["status_updated_to_in_service"] += 1
                elif status_result.new_status_name == UNDER_CONSTRUCTION_STATUS_NAME:
                    result["status_updated_to_under_construction"] += 1
                elif status_result.new_status_name == PLANNED_STATUS_NAME:
                    result["status_updated_to_planned"] += 1

        return result

    @staticmethod
    def _ensure_source(conn, source_name: str) -> int:
        source_id = conn.execute(
            text("SELECT SourceID FROM Source WHERE SourceName = :source_name"),
            {"source_name": source_name},
        ).scalar()
        if source_id is not None:
            return int(source_id)

        conn.execute(
            text("INSERT INTO Source (SourceName) VALUES (:source_name)"),
            {"source_name": source_name},
        )
        source_id = conn.execute(
            text("SELECT SourceID FROM Source WHERE SourceName = :source_name"),
            {"source_name": source_name},
        ).scalar()
        return int(source_id)

    @staticmethod
    def _ensure_milestone(conn, milestone_name: str) -> int:
        milestone_id = conn.execute(
            text("SELECT MilestoneTypeID FROM MilestoneType WHERE MilestoneName = :milestone_name"),
            {"milestone_name": milestone_name},
        ).scalar()
        if milestone_id is not None:
            return int(milestone_id)

        conn.execute(
            text("INSERT INTO MilestoneType (MilestoneName) VALUES (:milestone_name)"),
            {"milestone_name": milestone_name},
        )
        milestone_id = conn.execute(
            text("SELECT MilestoneTypeID FROM MilestoneType WHERE MilestoneName = :milestone_name"),
            {"milestone_name": milestone_name},
        ).scalar()
        return int(milestone_id)

    @staticmethod
    def _detail(row: pd.Series, status: str, message: str) -> Dict[str, Any]:
        return {
            "status": status,
            "message": message,
            "matched_project_id": row.get("matched_project_id"),
            "matched_project_name": row.get("matched_project_name"),
            "source_sheet": row.get("source_sheet"),
            "row_number": row.get("row_number"),
            "record_action": row.get("record_action"),
            "target_status": row.get("target_status"),
            "project_name": row.get("project_name"),
            "nup": row.get("nup"),
        }




def _first_non_empty(*values: Any) -> Optional[str]:
    """Return the first non-empty scalar value without evaluating pd.NA as bool."""
    for value in values:
        cleaned = _clean_text(value)
        if cleaned is not None:
            return cleaned
    return None

def _match_group(status: Any) -> str:
    if status in AUTO_PROPOSED_STATUSES:
        return "auto_proposed"
    if status in {"candidate_by_name", "ambiguous_name", "ambiguous_nup", "nup_type_mismatch"}:
        return "needs_validation"
    return "no_candidate"


def _match_status_label(status: Any) -> str:
    labels = {
        "matched_by_nup": "Propuesto por NUP",
        "matched_by_name": "Propuesto por nombre",
        "candidate_by_name": "Validar candidato",
        "ambiguous_name": "Ambiguo por nombre",
        "ambiguous_nup": "Ambiguo por NUP",
        "nup_type_mismatch": "NUP con tipo distinto",
        "no_project_name": "Sin nombre usable",
        "not_found": "Sin candidato",
    }
    return labels.get(str(status), str(status))


def _score_display(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


def _date_changes_text(row: pd.Series) -> str:
    labels = {
        "commissioning_actual": "PES real",
        "commissioning_estimated": "PES est.",
        "cod_actual": "EO real",
        "cod_estimated": "EO est.",
    }
    parts = []
    for field, label in labels.items():
        value = _to_date_or_none(row.get(field))
        if value is not None:
            parts.append(f"{label}: {value.isoformat()}")
    return "; ".join(parts)


def _as_bool(value: Any) -> bool:
    """Return a safe boolean for pandas/Streamlit values.

    pandas.NA raises ``TypeError: boolean value of NA is ambiguous`` when used
    directly in bool(...). This helper keeps UI-derived boolean fields safe.
    """
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return bool(value)


def _action_summary(row: pd.Series) -> str:
    action = _clean_text(row.get("action_proposed"))
    if action == "update_status_cancelled":
        return "Estado: Cancelled"

    parts = []
    if _as_bool(row.get("would_update_nup", False)):
        parts.append("NUP")
    date_changes = row.get("date_changes_proposed", 0)
    try:
        date_changes = int(date_changes) if not pd.isna(date_changes) else 0
    except (TypeError, ValueError):
        date_changes = 0
    if date_changes:
        parts.append(f"{date_changes} fecha(s)")
    if not parts:
        return "Sin cambios"
    return " + ".join(parts)

def _name_similarity(left: Any, right: Any) -> float:
    left_text = _clean_text(left)
    right_text = _clean_text(right)
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio()


def _clean_text(value: Any) -> Optional[str]:
    """Clean scalar text values without triggering pandas.NA boolean errors."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text_value = str(value).strip()
    return text_value if text_value else None


def _normalize_nup(value: Any) -> Optional[str]:
    text_value = _clean_text(value)
    if not text_value:
        return None
    if text_value.endswith(".0"):
        text_value = text_value[:-2]
    text_value = "".join(ch for ch in text_value if ch.isalnum() or ch in {"-", "_", "/"})
    return text_value or None


def _to_int_or_none(value: Any) -> Optional[int]:
    text_value = _clean_text(value)
    if not text_value:
        return None
    if text_value.endswith(".0"):
        text_value = text_value[:-2]
    return int(text_value) if text_value.isdigit() else None


def _to_date_or_none(value: Any) -> Optional[date]:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date()
