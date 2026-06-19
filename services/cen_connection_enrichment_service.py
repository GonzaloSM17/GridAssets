"""CEN Conexiones enrichment service.

This service owns the database-facing part of the CEN Conexiones workflow:
- build a match preview from normalized CEN rows;
- expose ranked candidates for manual validation;
- apply only the rows/candidates selected by the user;
- write NUP and RelevantDate values;
- delegate derived status changes to ProjectStatusService.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from database.db_connection import get_sqlserver_engine
from parsers.cen_connection_files_parse import normalize_project_name
from services.project_status_service import ProjectStatusService

SOURCE_NAME = "CEN - Conexiones"
DATE_FIELDS: dict[str, str] = {
    "commissioning_actual": "Commissioning_Actual",
    "commissioning_estimated": "Commissioning_Estimated",
    "cod_actual": "COD_Actual",
    "cod_estimated": "COD_Estimated",
}
AUTO_STATUSES = {"matched_by_nup", "matched_by_name"}
REVIEW_STATUSES = {"candidate_by_name", "ambiguous_name", "ambiguous_nup", "nup_type_mismatch"}


@dataclass(frozen=True)
class MatchThresholds:
    """Name matching thresholds used by the preview service."""

    auto_name_score: float = 0.97
    review_name_score: float = 0.93
    ambiguity_delta: float = 0.03


class CENConnectionEnrichmentService:
    """Match and apply CEN connection enrichment rows to existing projects."""

    def __init__(self, engine: Optional[Engine] = None, thresholds: Optional[MatchThresholds] = None) -> None:
        self.engine = engine or get_sqlserver_engine()
        self.thresholds = thresholds or MatchThresholds()

    # ------------------------------------------------------------------
    # Preview / matching
    # ------------------------------------------------------------------
    def load_project_reference(self) -> pd.DataFrame:
        query = text(
            """
            SELECT
                p.ProjectID,
                p.ProjectName,
                p.NUP,
                p.project_discriminator AS ProjectType,
                pe.ProjectEntityName,
                COALESCE(tg.TechnologyName, td.TechnologyName, tb.TechnologyName) AS TechnologyName,
                COALESCE(gp.PowerCapacity, dp.PowerCapacity, bp.PowerCapacity, tp.TotalCapacity) AS CapacityValue,
                COALESCE(gp.Location, dp.Location, bp.Location) AS LocationValue,
                b.BayName AS BayName
            FROM Project p
            LEFT JOIN ProjectEntity pe ON pe.ProjectEntityID = p.ProjectEntityID
            LEFT JOIN TransmissionProject tp ON tp.ProjectID = p.ProjectID
            LEFT JOIN GenerationProject gp ON gp.ProjectID = p.ProjectID
            LEFT JOIN DERProject dp ON dp.ProjectID = p.ProjectID
            LEFT JOIN BESSProject bp ON bp.ProjectID = p.ProjectID
            LEFT JOIN Technology tg ON tg.TechnologyID = gp.TechnologyID
            LEFT JOIN Technology td ON td.TechnologyID = dp.TechnologyID
            LEFT JOIN Technology tb ON tb.TechnologyID = bp.TechnologyID
            LEFT JOIN Bay b ON b.BayID = COALESCE(gp.BayID, dp.BayID, bp.BayID)
            """
        )
        with self.engine.connect() as conn:
            projects = pd.read_sql_query(query, conn)
        if projects.empty:
            return self._empty_project_reference()
        projects = projects.copy()
        projects["nup_key"] = projects["NUP"].apply(_normalize_nup_key)
        projects["normalized_project_name"] = projects["ProjectName"].apply(normalize_project_name)
        projects["normalized_entity_name"] = projects["ProjectEntityName"].apply(_normalize_text)
        projects["normalized_technology"] = projects["TechnologyName"].apply(_normalize_text)
        projects["normalized_location"] = projects["LocationValue"].apply(_normalize_text)
        projects["project_type_key"] = projects["ProjectType"].apply(_normalize_type_key)
        return projects

    def build_match_preview(self, records: pd.DataFrame) -> pd.DataFrame:
        projects = self.load_project_reference()
        return self.build_match_preview_from_projects(records, projects)

    def build_match_preview_from_projects(self, records: pd.DataFrame, projects: pd.DataFrame) -> pd.DataFrame:
        if records is None or records.empty:
            return pd.DataFrame()
        if projects is None or projects.empty:
            preview = records.copy().reset_index(drop=True)
            preview["preview_row_id"] = preview.index.astype(int)
            preview["match_status"] = "not_found"
            preview["match_method"] = "none"
            preview["matched_project_id"] = pd.NA
            preview["matched_project_name"] = pd.NA
            preview["match_score"] = 0.0
            return self._finalize_preview(preview)

        work = records.copy().reset_index(drop=True)
        for col in ["nup", "project_name", "connection_project_type", "company", "technology", "record_action"]:
            if col not in work.columns:
                work[col] = pd.NA
        work["preview_row_id"] = work.index.astype(int)
        work["normalized_project_name"] = work.get("normalized_project_name", work["project_name"].apply(normalize_project_name))
        work["nup_key"] = work["nup"].apply(_normalize_nup_key)
        work["connection_type_key"] = work["connection_project_type"].apply(_normalize_type_key)
        work["normalized_company"] = work["company"].apply(_normalize_text)

        rows: list[dict[str, Any]] = []
        for _, record in work.iterrows():
            rows.append(self._match_one(record, projects))
        return self._finalize_preview(pd.DataFrame(rows))

    def summarize_preview(self, preview: pd.DataFrame) -> pd.DataFrame:
        if preview is None or preview.empty or "match_status" not in preview.columns:
            return pd.DataFrame(columns=["match_status", "rows"])
        return (
            preview.groupby("match_status", dropna=False)
            .size()
            .reset_index(name="rows")
            .sort_values("rows", ascending=False)
            .reset_index(drop=True)
        )

    def _match_one(self, record: pd.Series, projects: pd.DataFrame) -> dict[str, Any]:
        base = record.to_dict()
        compatible = _filter_compatible_projects(projects, record.get("connection_type_key"))
        nup_key = record.get("nup_key")

        if _has_value(nup_key):
            nup_candidates = projects.loc[projects["nup_key"] == nup_key].copy()
            if not nup_candidates.empty:
                typed_nup_candidates = _filter_compatible_projects(nup_candidates, record.get("connection_type_key"))
                if not typed_nup_candidates.empty:
                    return self._result_from_candidates(
                        base,
                        typed_nup_candidates,
                        status_if_single="matched_by_nup",
                        method="nup_type",
                        score=1.0,
                        comment="Coincidencia por NUP con tipo compatible.",
                    )
                return self._result_from_candidates(
                    base,
                    nup_candidates,
                    status_if_single="nup_type_mismatch",
                    method="nup_only",
                    score=1.0,
                    comment="El NUP existe en BD, pero el tipo no es compatible. Revisar manualmente.",
                )

        name = record.get("normalized_project_name")
        if not _has_value(name):
            return self._empty_result(base, "no_project_name", "none", "Sin NUP y sin nombre útil para matching.")

        name_candidates = self._score_name_candidates(record, compatible)
        if name_candidates.empty and len(compatible) != len(projects):
            name_candidates = self._score_name_candidates(record, projects)
        if name_candidates.empty:
            return self._empty_result(base, "not_found", "name", "No hay candidato con puntaje suficiente.")

        best_score = float(name_candidates.iloc[0]["match_score"])
        top_band = name_candidates.loc[name_candidates["match_score"] >= best_score - self.thresholds.ambiguity_delta]
        if best_score >= self.thresholds.auto_name_score and len(top_band) == 1:
            return self._result_from_candidates(
                base,
                name_candidates.head(3),
                status_if_single="matched_by_name",
                method="name_high_confidence",
                score=best_score,
                comment="Coincidencia de nombre tratada con alta confianza.",
            )
        if best_score >= self.thresholds.review_name_score:
            status = "ambiguous_name" if len(top_band) > 1 else "candidate_by_name"
            return self._result_from_candidates(
                base,
                name_candidates.head(3),
                status_if_single=status,
                method="name_review",
                score=best_score,
                comment="Candidato por nombre. Revisar antes de aplicar.",
            )
        return self._empty_result(
            base,
            "not_found",
            "name",
            "El mejor candidato está bajo el umbral de revisión.",
            score=best_score,
            candidates=name_candidates.head(3),
        )

    def _score_name_candidates(self, record: pd.Series, projects: pd.DataFrame) -> pd.DataFrame:
        if projects.empty:
            return projects.copy()
        name = record.get("normalized_project_name")
        company = record.get("normalized_company")
        technology = _normalize_text(record.get("technology"))
        rows: list[dict[str, Any]] = []
        for _, project in projects.iterrows():
            project_name = project.get("normalized_project_name")
            if not _has_value(project_name):
                continue
            score = max(_name_similarity(name, project_name), _token_similarity(name, project_name))
            entity = project.get("normalized_entity_name")
            if _has_value(company) and _has_value(entity) and _token_overlap(company, entity) >= 0.5:
                score += 0.04
            project_technology = project.get("normalized_technology")
            if _has_value(technology) and _has_value(project_technology) and _token_overlap(technology, project_technology) >= 0.5:
                score += 0.02
            score = min(float(score), 1.0)
            if score >= self.thresholds.review_name_score:
                item = project.to_dict()
                item["match_score"] = round(score, 4)
                rows.append(item)
        if not rows:
            return pd.DataFrame(columns=list(projects.columns) + ["match_score"])
        candidates = pd.DataFrame(rows).sort_values(["match_score", "ProjectName"], ascending=[False, True]).reset_index(drop=True)
        return _deduplicate_candidates_by_project_id(candidates)

    def _result_from_candidates(
        self,
        base: dict[str, Any],
        candidates: pd.DataFrame,
        status_if_single: str,
        method: str,
        score: float,
        comment: str,
    ) -> dict[str, Any]:
        candidates = _deduplicate_candidates_by_project_id(candidates).reset_index(drop=True)
        if candidates.empty:
            return self._empty_result(base, "not_found", method, "No hay candidato único disponible para aplicar.")
        top = candidates.iloc[0]
        candidate_count = int(len(candidates))
        if candidate_count > 1 and status_if_single == "matched_by_nup":
            status = "ambiguous_nup"
            action = "review"
            comment = "Más de un proyecto comparte el NUP. Revisar candidato."
        elif status_if_single in REVIEW_STATUSES:
            status = status_if_single
            action = "review"
        else:
            status = status_if_single
            action = "would_update"

        result = dict(base)
        result.update(
            {
                "match_status": status,
                "match_method": method,
                "matched_project_id": _safe_int(top.get("ProjectID")),
                "matched_project_name": top.get("ProjectName"),
                "matched_project_type": top.get("ProjectType"),
                "matched_project_nup": top.get("NUP"),
                "match_score": round(float(score), 4),
                "name_score": round(float(score), 4),
                "candidate_count": candidate_count,
                "top_candidates": _format_candidates(candidates.head(5)),
                "proposed_action": action,
                "comment": comment,
                "match_comment": comment,
            }
        )
        self._attach_candidate_columns(result, candidates.head(3))
        return result

    def _empty_result(
        self,
        base: dict[str, Any],
        status: str,
        method: str,
        comment: str,
        score: float = 0.0,
        candidates: Optional[pd.DataFrame] = None,
    ) -> dict[str, Any]:
        result = dict(base)
        result.update(
            {
                "match_status": status,
                "match_method": method,
                "matched_project_id": pd.NA,
                "matched_project_name": pd.NA,
                "matched_project_type": pd.NA,
                "matched_project_nup": pd.NA,
                "match_score": round(float(score), 4),
                "name_score": round(float(score), 4),
                "candidate_count": int(len(candidates)) if candidates is not None else 0,
                "top_candidates": _format_candidates(candidates) if candidates is not None else "",
                "proposed_action": "skip",
                "comment": comment,
                "match_comment": comment,
            }
        )
        clean_candidates = _deduplicate_candidates_by_project_id(candidates) if candidates is not None else pd.DataFrame()
        self._attach_candidate_columns(result, clean_candidates.head(3))
        return result

    @staticmethod
    def _attach_candidate_columns(result: dict[str, Any], candidates: pd.DataFrame) -> None:
        for rank in (1, 2, 3):
            result[f"candidate_{rank}_project_id"] = pd.NA
            result[f"candidate_{rank}_project_name"] = pd.NA
            result[f"candidate_{rank}_project_type"] = pd.NA
            result[f"candidate_{rank}_project_nup"] = pd.NA
            result[f"candidate_{rank}_project_entity"] = pd.NA
            result[f"candidate_{rank}_technology"] = pd.NA
            result[f"candidate_{rank}_capacity"] = pd.NA
            result[f"candidate_{rank}_location"] = pd.NA
            result[f"candidate_{rank}_bay"] = pd.NA
            result[f"candidate_{rank}_score"] = pd.NA
            result[f"candidate_{rank}_action_summary"] = pd.NA
        if candidates is None or candidates.empty:
            return
        candidates = _deduplicate_candidates_by_project_id(candidates)
        for idx, (_, candidate) in enumerate(candidates.head(3).iterrows(), start=1):
            result[f"candidate_{idx}_project_id"] = _safe_int(candidate.get("ProjectID"))
            result[f"candidate_{idx}_project_name"] = candidate.get("ProjectName")
            result[f"candidate_{idx}_project_type"] = candidate.get("ProjectType")
            result[f"candidate_{idx}_project_nup"] = candidate.get("NUP")
            result[f"candidate_{idx}_project_entity"] = candidate.get("ProjectEntityName")
            result[f"candidate_{idx}_technology"] = candidate.get("TechnologyName")
            result[f"candidate_{idx}_capacity"] = candidate.get("CapacityValue")
            result[f"candidate_{idx}_location"] = candidate.get("LocationValue")
            result[f"candidate_{idx}_bay"] = candidate.get("BayName")
            result[f"candidate_{idx}_score"] = candidate.get("match_score", result.get("match_score"))
            result[f"candidate_{idx}_action_summary"] = _candidate_action_summary(result, candidate)

    def _finalize_preview(self, preview: pd.DataFrame) -> pd.DataFrame:
        result = preview.copy().reset_index(drop=True)
        if "preview_row_id" not in result.columns:
            result["preview_row_id"] = result.index.astype(int)
        status = result.get("match_status", pd.Series([""] * len(result), index=result.index)).fillna("").astype(str)
        result["match_group"] = status.map(
            lambda value: "auto_proposed" if value in AUTO_STATUSES else "needs_validation" if value in REVIEW_STATUSES else "no_candidate"
        )
        result["match_group_label"] = result["match_group"].map(
            {"auto_proposed": "Propuestos", "needs_validation": "Por validar", "no_candidate": "Sin candidato"}
        )
        result["match_status_label"] = status.map(
            {
                "matched_by_nup": "Propuesto por NUP",
                "matched_by_name": "Propuesto por nombre",
                "candidate_by_name": "Candidato por nombre",
                "ambiguous_name": "Ambiguo por nombre",
                "ambiguous_nup": "Ambiguo por NUP",
                "nup_type_mismatch": "NUP con tipo distinto",
                "not_found": "Sin candidato",
                "no_project_name": "Sin nombre usable",
            }
        ).fillna(status)
        score = pd.to_numeric(result.get("match_score", pd.Series([None] * len(result), index=result.index)), errors="coerce")
        result["score_display"] = score.map(lambda value: "" if pd.isna(value) else f"{value:.3f}")
        result["can_apply"] = result["match_group"].isin({"auto_proposed", "needs_validation"}) & result.get(
            "matched_project_id", pd.Series([None] * len(result), index=result.index)
        ).notna()
        result["default_apply"] = result["match_group"].eq("auto_proposed") & result.get(
            "matched_project_id", pd.Series([None] * len(result), index=result.index)
        ).notna()
        result["would_update_nup"] = result.apply(_would_update_nup_from_row, axis=1)
        result["date_changes_proposed"] = result.apply(_count_date_values, axis=1)
        result["date_changes_text"] = result.apply(_date_changes_text, axis=1)
        result["action_summary"] = result.apply(_action_summary, axis=1)
        return result

    # ------------------------------------------------------------------
    # Apply selected enrichment
    # ------------------------------------------------------------------
    def apply_selected_enrichment(
        self,
        preview: pd.DataFrame,
        selected_row_ids: list[int],
        selected_candidate_project_ids: Optional[dict[int, int]] = None,
    ) -> dict[str, Any]:
        selected_candidate_project_ids = selected_candidate_project_ids or {}
        summary: dict[str, int] = {
            "rows_received": int(len(selected_row_ids or [])),
            "rows_processed": 0,
            "rows_applied": 0,
            "rows_with_changes": 0,
            "rows_without_changes": 0,
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
        if preview is None or preview.empty or not selected_row_ids:
            return {"summary": summary, "details": pd.DataFrame(details)}

        work = preview.copy()
        if "preview_row_id" not in work.columns:
            work = work.reset_index(drop=True)
            work["preview_row_id"] = work.index.astype(int)
        selected_set = {int(value) for value in selected_row_ids if value is not None}
        work = work.loc[work["preview_row_id"].astype(int).isin(selected_set)].copy()

        enriched_project_ids: set[int] = set()
        now = datetime.utcnow()
        with self.engine.begin() as conn:
            source_id = self._ensure_source(conn, SOURCE_NAME)
            milestone_ids = {field: self._ensure_milestone(conn, milestone) for field, milestone in DATE_FIELDS.items()}
            for _, row in work.iterrows():
                summary["rows_processed"] += 1
                row_id = _safe_int(row.get("preview_row_id"))
                project_id = _safe_int(selected_candidate_project_ids.get(int(row_id)) if row_id is not None and int(row_id) in selected_candidate_project_ids else row.get("matched_project_id"))
                if project_id is None:
                    summary["rows_skipped"] += 1
                    details.append(self._detail(row, "omitted", "Fila omitida: sin ID de proyecto BD seleccionado."))
                    continue
                try:
                    if str(row.get("record_action") or "date_enrichment") == "status_cancelled":
                        result = ProjectStatusService.set_cancelled_if_no_cod_actual(conn, project_id)
                        if result.blocked_by_cod_actual:
                            summary["status_cancelled_conflicts"] += 1
                            summary["rows_skipped"] += 1
                            details.append(self._detail(row, "conflict", result.message, project_id))
                        else:
                            summary["rows_applied"] += 1
                            if result.status_changed:
                                summary["status_updated_to_cancelled"] += 1
                                summary["rows_with_changes"] += 1
                            else:
                                summary["rows_without_changes"] += 1
                            details.append(self._detail(row, "applied" if result.status_changed else "unchanged", result.message, project_id))
                        continue

                    row_changed = False
                    if self._update_nup_if_empty(conn, project_id, row):
                        summary["nup_updated"] += 1
                        row_changed = True
                    elif self._nup_conflict_exists(conn, project_id, row):
                        summary["nup_conflicts"] += 1

                    touched_date = False
                    for field, milestone_id in milestone_ids.items():
                        date_value = _to_datetime(row.get(field))
                        if date_value is None:
                            continue
                        touched_date = True
                        action = self._upsert_relevant_date(conn, project_id, milestone_id, source_id, date_value, now)
                        if action == "created":
                            summary["dates_created"] += 1
                            row_changed = True
                        elif action == "updated":
                            summary["dates_updated"] += 1
                            row_changed = True
                        else:
                            summary["dates_unchanged"] += 1

                    if touched_date:
                        status_result = ProjectStatusService.sync_project_status_from_dates(conn, project_id)
                        self._count_status_result(summary, status_result)

                    summary["rows_applied"] += 1
                    if row_changed:
                        summary["rows_with_changes"] += 1
                        enriched_project_ids.add(project_id)
                        detail_status = "applied"
                        detail_message = "Enriquecimiento aplicado con cambios."
                    else:
                        summary["rows_without_changes"] += 1
                        detail_status = "unchanged"
                        detail_message = "Fila procesada sin cambios nuevos en la base de datos."
                    details.append(self._detail(row, detail_status, detail_message, project_id))
                except Exception as exc:  # pragma: no cover - defensive UI reporting
                    summary["errors"] += 1
                    details.append(self._detail(row, "error", str(exc), project_id))
        summary["projects_enriched"] = len(enriched_project_ids)
        return {"summary": summary, "details": pd.DataFrame(details)}

    @staticmethod
    def _count_status_result(summary: dict[str, int], result: Any) -> None:
        if not getattr(result, "status_changed", False):
            return
        name = getattr(result, "new_status_name", None)
        if name == "InService":
            summary["status_updated_to_in_service"] += 1
        elif name == "UnderConstruction":
            summary["status_updated_to_under_construction"] += 1
        elif name == "Planned":
            summary["status_updated_to_planned"] += 1
        elif name == "Cancelled":
            summary["status_updated_to_cancelled"] += 1

    @staticmethod
    def _ensure_source(conn: Any, source_name: str) -> int:
        source_id = conn.execute(text("SELECT SourceID FROM Source WHERE SourceName = :name"), {"name": source_name}).scalar()
        if source_id is None:
            conn.execute(text("INSERT INTO Source (SourceName) VALUES (:name)"), {"name": source_name})
            source_id = conn.execute(text("SELECT SourceID FROM Source WHERE SourceName = :name"), {"name": source_name}).scalar()
        return int(source_id)

    @staticmethod
    def _ensure_milestone(conn: Any, milestone_name: str) -> int:
        milestone_id = conn.execute(
            text("SELECT MilestoneTypeID FROM MilestoneType WHERE MilestoneName = :name"), {"name": milestone_name}
        ).scalar()
        if milestone_id is None:
            conn.execute(text("INSERT INTO MilestoneType (MilestoneName) VALUES (:name)"), {"name": milestone_name})
            milestone_id = conn.execute(
                text("SELECT MilestoneTypeID FROM MilestoneType WHERE MilestoneName = :name"), {"name": milestone_name}
            ).scalar()
        return int(milestone_id)

    @staticmethod
    def _update_nup_if_empty(conn: Any, project_id: int, row: pd.Series) -> bool:
        incoming_nup = _safe_int(row.get("nup"))
        if incoming_nup is None:
            return False
        current_nup = conn.execute(text("SELECT NUP FROM Project WHERE ProjectID = :project_id"), {"project_id": project_id}).scalar()
        if current_nup is not None:
            return False
        conn.execute(text("UPDATE Project SET NUP = :nup WHERE ProjectID = :project_id"), {"nup": incoming_nup, "project_id": project_id})
        return True

    @staticmethod
    def _nup_conflict_exists(conn: Any, project_id: int, row: pd.Series) -> bool:
        incoming_nup = _safe_int(row.get("nup"))
        if incoming_nup is None:
            return False
        current_nup = conn.execute(text("SELECT NUP FROM Project WHERE ProjectID = :project_id"), {"project_id": project_id}).scalar()
        return current_nup is not None and int(current_nup) != int(incoming_nup)

    @staticmethod
    def _upsert_relevant_date(conn: Any, project_id: int, milestone_id: int, source_id: int, date_value: datetime, extracted_at: datetime) -> str:
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
            {"project_id": project_id, "milestone_id": milestone_id, "source_id": source_id},
        ).mappings().first()
        if existing is None:
            conn.execute(
                text(
                    """
                    INSERT INTO RelevantDate (ProjectID, MilestoneTypeID, SourceID, DateValue, ExtractedAt)
                    VALUES (:project_id, :milestone_id, :source_id, :date_value, :extracted_at)
                    """
                ),
                {
                    "project_id": project_id,
                    "milestone_id": milestone_id,
                    "source_id": source_id,
                    "date_value": date_value,
                    "extracted_at": extracted_at,
                },
            )
            return "created"
        old_date = _to_datetime(existing["DateValue"])
        if old_date is not None and old_date.date() == date_value.date():
            return "unchanged"
        conn.execute(
            text(
                """
                UPDATE RelevantDate
                SET DateValue = :date_value, ExtractedAt = :extracted_at
                WHERE RelevantDateID = :relevant_date_id
                """
            ),
            {"date_value": date_value, "extracted_at": extracted_at, "relevant_date_id": existing["RelevantDateID"]},
        )
        return "updated"

    @staticmethod
    def _detail(row: pd.Series, status: str, message: str, project_id: Optional[int] = None) -> dict[str, Any]:
        data = row.to_dict()
        data.update({"status": status, "message": message, "matched_project_id": project_id or row.get("matched_project_id")})
        return data

    @staticmethod
    def _empty_project_reference() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "ProjectID",
                "ProjectName",
                "NUP",
                "ProjectType",
                "ProjectEntityName",
                "TechnologyName",
                "CapacityValue",
                "LocationValue",
                "BayName",
                "nup_key",
                "normalized_project_name",
                "normalized_entity_name",
                "normalized_technology",
                "normalized_location",
                "project_type_key",
            ]
        )


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def _deduplicate_candidates_by_project_id(candidates: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Return candidates sorted by score and unique by ProjectID.

    The database reference can contain repeated rows for the same ProjectID,
    especially when joins or mixed technology/project structures expand a
    project. The UI must show each candidate only once; otherwise the manual
    validation table can display duplicate options with identical scores.
    """
    if candidates is None or candidates.empty:
        return pd.DataFrame()
    result = candidates.copy()
    if "ProjectID" not in result.columns:
        return result.reset_index(drop=True)

    result["_project_id_int"] = result["ProjectID"].apply(_safe_int)
    result = result.loc[result["_project_id_int"].notna()].copy()
    if result.empty:
        return result.drop(columns=["_project_id_int"], errors="ignore").reset_index(drop=True)

    if "match_score" in result.columns:
        result["_score_sort"] = pd.to_numeric(result["match_score"], errors="coerce").fillna(-1.0)
    else:
        result["_score_sort"] = -1.0
    if "ProjectName" not in result.columns:
        result["ProjectName"] = ""

    result = result.sort_values(["_score_sort", "ProjectName"], ascending=[False, True])
    result = result.drop_duplicates(subset=["_project_id_int"], keep="first")
    return result.drop(columns=["_project_id_int", "_score_sort"], errors="ignore").reset_index(drop=True)

def _filter_compatible_projects(projects: pd.DataFrame, connection_type: Any) -> pd.DataFrame:
    compatible_types = _compatible_project_types(connection_type)
    if not compatible_types:
        return projects.copy()
    filtered = projects.loc[projects["project_type_key"].isin(compatible_types)].copy()
    return filtered if not filtered.empty else projects.copy()


def _compatible_project_types(connection_type: Any) -> set[str]:
    key = _normalize_type_key(connection_type)
    if key == "transmission":
        return {"transmission"}
    if key == "bess":
        return {"bess"}
    if key == "pmgd":
        return {"der", "generation"}
    if key == "generation_or_bess":
        return {"generation", "bess", "der"}
    if key == "generation":
        return {"generation", "der"}
    return set()


def _normalize_type_key(value: Any) -> str:
    text_value = _normalize_text(value)
    if not _has_value(text_value):
        return ""
    return str(text_value).replace(" ", "_")


def _normalize_nup_key(value: Any) -> Any:
    if value is None:
        return pd.NA
    try:
        if pd.isna(value):
            return pd.NA
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    text_value = str(value).strip()
    if not text_value:
        return pd.NA
    text_value = re.sub(r"\.0$", "", text_value)
    text_value = re.sub(r"[^0-9A-Za-z\-_/]", "", text_value)
    return text_value if text_value else pd.NA


def _normalize_text(value: Any) -> Any:
    if value is None:
        return pd.NA
    try:
        if pd.isna(value):
            return pd.NA
    except (TypeError, ValueError):
        pass
    text_value = str(value).strip().lower()
    if not text_value:
        return pd.NA
    text_value = unicodedata.normalize("NFKD", text_value)
    text_value = "".join(ch for ch in text_value if not unicodedata.combining(ch))
    text_value = re.sub(r"[^a-z0-9]+", " ", text_value)
    text_value = re.sub(r"\s+", " ", text_value).strip()
    return text_value if text_value else pd.NA


def _name_similarity(left: Any, right: Any) -> float:
    if not _has_value(left) or not _has_value(right):
        return 0.0
    return SequenceMatcher(None, str(left), str(right)).ratio()


def _token_similarity(left: Any, right: Any) -> float:
    if not _has_value(left) or not _has_value(right):
        return 0.0
    left_tokens = set(str(left).split())
    right_tokens = set(str(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens.intersection(right_tokens))
    precision = overlap / len(left_tokens)
    recall = overlap / len(right_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _token_overlap(left: Any, right: Any) -> float:
    if not _has_value(left) or not _has_value(right):
        return 0.0
    left_tokens = set(str(left).split())
    right_tokens = set(str(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens.intersection(right_tokens)) / min(len(left_tokens), len(right_tokens))


def _format_candidates(candidates: Optional[pd.DataFrame]) -> str:
    if candidates is None or candidates.empty:
        return ""
    parts = []
    for _, candidate in candidates.head(5).iterrows():
        score = candidate.get("match_score", "")
        score_text = f" score={score}" if _has_value(score) else ""
        nup = candidate.get("NUP", "")
        nup_text = f" NUP={nup}" if _has_value(nup) else ""
        parts.append(
            f"{candidate.get('ProjectID')} | {candidate.get('ProjectName')} | {candidate.get('ProjectType')}{nup_text}{score_text}"
        )
    return " ; ".join(parts)


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return str(value).strip() != ""


def _to_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    try:
        parsed = pd.to_datetime(value, errors="coerce")
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed


def _count_date_values(row: pd.Series) -> int:
    return sum(1 for field in DATE_FIELDS if _to_datetime(row.get(field)) is not None)


def _date_changes_text(row: pd.Series) -> str:
    labels = {
        "commissioning_actual": "PES real",
        "commissioning_estimated": "PES est.",
        "cod_actual": "EO real",
        "cod_estimated": "EO est.",
    }
    return ", ".join(label for field, label in labels.items() if _to_datetime(row.get(field)) is not None)


def _would_update_nup_from_row(row: pd.Series) -> bool:
    return _safe_int(row.get("nup")) is not None and row.get("matched_project_id") is not None


def _candidate_action_summary(source_row: dict[str, Any], candidate: pd.Series) -> str:
    """Return the change summary as it would apply to a specific candidate."""
    action = str(source_row.get("record_action") or "date_enrichment")
    if action == "status_cancelled":
        return "Cambiar estado a Cancelled"

    parts: list[str] = []
    incoming_nup = _safe_int(source_row.get("nup"))
    candidate_nup = _safe_int(candidate.get("NUP"))
    if incoming_nup is not None and candidate_nup is None:
        parts.append("NUP: actualizar")
    elif incoming_nup is not None and candidate_nup is not None and incoming_nup != candidate_nup:
        parts.append("NUP: conflicto")
    elif incoming_nup is not None:
        parts.append("NUP: sin cambio")

    date_parts: list[str] = []
    for field, milestone in DATE_FIELDS.items():
        if _to_datetime(source_row.get(field)) is not None:
            date_parts.append(milestone)
    if date_parts:
        parts.append("Fechas: " + ", ".join(date_parts))

    return ", ".join(parts) if parts else "Sin cambios propuestos"


def _action_summary(row: pd.Series) -> str:
    action = str(row.get("record_action") or "date_enrichment")
    if action == "status_cancelled":
        return "Cambiar estado a Cancelled"
    parts = []
    if _would_update_nup_from_row(row):
        parts.append("NUP")
    dates = _date_changes_text(row)
    if dates:
        parts.append(dates)
    return ", ".join(parts)
