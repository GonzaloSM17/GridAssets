"""CEN connection enrichment service.

This module performs two operations:
1. Build a read-only match preview between normalized CEN connection rows and
   existing projects in the database.
2. Apply a controlled enrichment for safe matches only.

Rules:
- CEN connection files never create projects.
- CNE remains the primary source for project creation.
- CEN connection files can enrich existing projects with NUP and relevant dates.
- Ambiguous or weak matches are reported but not applied.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, Optional

import pandas as pd
from sqlalchemy.orm import sessionmaker

from database.db_connection import get_sqlserver_engine
from database.db_orm_model import MilestoneType, Project, RelevantDate, Source
from parsers.cen_connection_files_parse import normalize_project_name


SOURCE_NAME = "CEN Conexiones"

DATE_FIELD_TO_MILESTONE = {
    "commissioning_actual": "Commissioning_Actual",
    "commissioning_estimated": "Commissioning_Estimated",
    "cod_actual": "COD_Actual",
    "cod_estimated": "COD_Estimated",
}

SAFE_MATCH_STATUSES = {"matched_by_nup", "matched_by_name"}

TYPE_COMPATIBILITY = {
    "transmission": {"transmission"},
    "bess": {"bess"},
    "pmgd": {"der", "generation"},
    "generation_or_bess": {"generation", "bess", "der"},
}


@dataclass
class ApplySummary:
    """Summary returned after applying safe enrichment rows."""

    rows_received: int = 0
    rows_safe: int = 0
    rows_applied: int = 0
    rows_skipped: int = 0
    nup_updated: int = 0
    nup_conflicts: int = 0
    dates_created: int = 0
    dates_updated: int = 0
    dates_unchanged: int = 0
    errors: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "rows_received": self.rows_received,
            "rows_safe": self.rows_safe,
            "rows_applied": self.rows_applied,
            "rows_skipped": self.rows_skipped,
            "nup_updated": self.nup_updated,
            "nup_conflicts": self.nup_conflicts,
            "dates_created": self.dates_created,
            "dates_updated": self.dates_updated,
            "dates_unchanged": self.dates_unchanged,
            "errors": self.errors,
        }


class CENConnectionEnrichmentService:
    """Preview and apply CEN connection enrichment over existing projects."""

    def __init__(self, echo: bool = False) -> None:
        self.engine = get_sqlserver_engine(echo=echo)
        self.Session = sessionmaker(bind=self.engine)

    # ------------------------------------------------------------------
    # Public preview API
    # ------------------------------------------------------------------
    def build_match_preview(self, records: pd.DataFrame) -> pd.DataFrame:
        """Return a row-level preview of how normalized records match the DB."""
        projects = self._load_projects_dataframe()
        existing_dates = self._load_existing_dates_dataframe()

        if records.empty:
            return pd.DataFrame()

        preview_rows = []
        for _, record in records.iterrows():
            match = self._match_record(record, projects)
            action = self._build_action_preview(record, match, existing_dates)
            preview_rows.append({**self._record_preview_base(record), **match, **action})

        return pd.DataFrame(preview_rows)

    # ------------------------------------------------------------------
    # Public apply API
    # ------------------------------------------------------------------
    def apply_safe_enrichment(self, preview: pd.DataFrame) -> Dict[str, Any]:
        """Apply enrichment only for safe matches in a preview DataFrame."""
        summary = ApplySummary(rows_received=int(len(preview)))
        details = []

        if preview.empty:
            return {"summary": summary.to_dict(), "details": pd.DataFrame(details)}

        safe_rows = preview.loc[preview["match_status"].isin(SAFE_MATCH_STATUSES)].copy()
        summary.rows_safe = int(len(safe_rows))

        session = self.Session()
        try:
            source = self._ensure_source(session, SOURCE_NAME)
            milestones = {
                name: self._ensure_milestone(session, name)
                for name in DATE_FIELD_TO_MILESTONE.values()
            }

            for _, row in safe_rows.iterrows():
                try:
                    project_id = _to_int_or_none(row.get("matched_project_id"))
                    if project_id is None:
                        summary.rows_skipped += 1
                        details.append(self._detail(row, "skipped", "Missing matched ProjectID."))
                        continue

                    project = session.get(Project, project_id)
                    if project is None:
                        summary.rows_skipped += 1
                        details.append(self._detail(row, "skipped", "Matched project no longer exists."))
                        continue

                    row_changed = False
                    nup_result = self._apply_nup(project, row)
                    if nup_result == "updated":
                        summary.nup_updated += 1
                        row_changed = True
                    elif nup_result == "conflict":
                        summary.nup_conflicts += 1

                    date_result = self._apply_dates(
                        session=session,
                        source=source,
                        milestones=milestones,
                        project_id=project.ProjectID,
                        row=row,
                    )
                    summary.dates_created += date_result["created"]
                    summary.dates_updated += date_result["updated"]
                    summary.dates_unchanged += date_result["unchanged"]
                    row_changed = row_changed or date_result["created"] > 0 or date_result["updated"] > 0

                    if row_changed:
                        summary.rows_applied += 1
                        details.append(self._detail(row, "applied", "Enrichment applied."))
                    else:
                        summary.rows_skipped += 1
                        details.append(self._detail(row, "unchanged", "No database changes needed."))

                except Exception as exc:  # pragma: no cover - defensive UI reporting
                    summary.errors += 1
                    details.append(self._detail(row, "error", str(exc)))

            session.commit()
            return {"summary": summary.to_dict(), "details": pd.DataFrame(details)}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------
    def _load_projects_dataframe(self) -> pd.DataFrame:
        session = self.Session()
        try:
            rows = (
                session.query(
                    Project.ProjectID,
                    Project.ProjectName,
                    Project.NUP,
                    Project.project_discriminator,
                )
                .order_by(Project.ProjectID)
                .all()
            )
            data = [
                {
                    "project_id": row.ProjectID,
                    "project_name_db": row.ProjectName,
                    "nup_db": _normalize_nup(row.NUP),
                    "project_type_db": row.project_discriminator,
                    "normalized_project_name_db": normalize_project_name(row.ProjectName),
                }
                for row in rows
            ]
            return pd.DataFrame(data)
        finally:
            session.close()

    def _load_existing_dates_dataframe(self) -> pd.DataFrame:
        session = self.Session()
        try:
            rows = (
                session.query(
                    RelevantDate.ProjectID,
                    MilestoneType.MilestoneName,
                    Source.SourceName,
                    RelevantDate.DateValue,
                )
                .join(MilestoneType, RelevantDate.MilestoneTypeID == MilestoneType.MilestoneTypeID)
                .join(Source, RelevantDate.SourceID == Source.SourceID)
                .filter(Source.SourceName == SOURCE_NAME)
                .all()
            )
            return pd.DataFrame(
                [
                    {
                        "project_id": row.ProjectID,
                        "milestone_name": row.MilestoneName,
                        "source_name": row.SourceName,
                        "date_value": row.DateValue.date() if row.DateValue else None,
                    }
                    for row in rows
                ]
            )
        finally:
            session.close()

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
            compatible_projects = projects.loc[projects["project_type_db"].isin(compatible_types)].copy()

        nup = _normalize_nup(record.get("nup"))
        if nup:
            by_nup = compatible_projects.loc[compatible_projects["nup_db"] == nup]
            if len(by_nup) == 1:
                return self._project_match(by_nup.iloc[0], "matched_by_nup", 1.0, "Unique match by NUP and compatible type.")
            if len(by_nup) > 1:
                return self._ambiguous_match(by_nup, "ambiguous_nup", "Multiple compatible projects share the same NUP.")

            global_by_nup = projects.loc[projects["nup_db"] == nup]
            if len(global_by_nup) > 0:
                return self._ambiguous_match(global_by_nup, "nup_type_mismatch", "NUP exists in DB, but not in a compatible project type.")

        normalized_name = record.get("normalized_project_name")
        normalized_name = _clean_text(normalized_name)
        if not normalized_name:
            return self._empty_match("no_project_name", "No usable project name in connection row.")

        exact = compatible_projects.loc[compatible_projects["normalized_project_name_db"] == normalized_name]
        if len(exact) == 1:
            return self._project_match(exact.iloc[0], "matched_by_name", 1.0, "Exact normalized-name match and compatible type.")
        if len(exact) > 1:
            return self._ambiguous_match(exact, "ambiguous_name", "Multiple compatible projects match the normalized name exactly.")

        candidates = self._score_name_candidates(normalized_name, compatible_projects)
        if candidates.empty:
            return self._empty_match("not_found", "No compatible name candidates found.")

        best = candidates.iloc[0]
        if float(best["name_score"]) >= 0.82:
            return self._project_match(best, "candidate_by_name", float(best["name_score"]), "Close name candidate. Review manually before applying.")

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
    def _project_match(project_row: pd.Series, status: str, score: float, comment: str) -> Dict[str, Any]:
        return {
            "match_status": status,
            "matched_project_id": project_row.get("project_id"),
            "matched_project_name": project_row.get("project_name_db"),
            "matched_project_type": project_row.get("project_type_db"),
            "matched_project_nup": project_row.get("nup_db"),
            "name_score": round(float(score), 3),
            "candidate_count": 1,
            "match_comment": comment,
        }

    @staticmethod
    def _ambiguous_match(projects: pd.DataFrame, status: str, comment: str) -> Dict[str, Any]:
        names = projects["project_name_db"].head(5).tolist()
        return {
            "match_status": status,
            "matched_project_id": pd.NA,
            "matched_project_name": " | ".join(str(name) for name in names),
            "matched_project_type": pd.NA,
            "matched_project_nup": pd.NA,
            "name_score": pd.NA,
            "candidate_count": int(len(projects)),
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
        if match["match_status"] not in SAFE_MATCH_STATUSES:
            return {
                "is_safe_to_apply": False,
                "would_update_nup": False,
                "date_values_in_file": self._count_date_values(record),
                "date_changes_proposed": 0,
                "action_proposed": "review_only",
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
        return sum(1 for field in DATE_FIELD_TO_MILESTONE if _to_date_or_none(record.get(field)) is not None)

    @staticmethod
    def _record_preview_base(record: pd.Series) -> Dict[str, Any]:
        return {
            "source_detail": record.get("source_detail"),
            "source_sheet": record.get("source_sheet"),
            "row_number": record.get("row_number"),
            "connection_project_type": record.get("connection_project_type"),
            "nup": _normalize_nup(record.get("nup")),
            "project_name": record.get("project_name"),
            "company": record.get("company"),
            "region": record.get("region"),
            "technology": record.get("technology"),
            "commissioning_actual": record.get("commissioning_actual"),
            "commissioning_estimated": record.get("commissioning_estimated"),
            "cod_actual": record.get("cod_actual"),
            "cod_estimated": record.get("cod_estimated"),
        }

    # ------------------------------------------------------------------
    # Apply helpers
    # ------------------------------------------------------------------
    def _apply_nup(self, project: Project, row: pd.Series) -> str:
        nup_file = _normalize_nup(row.get("nup"))
        if not nup_file:
            return "empty"

        nup_db = _normalize_nup(project.NUP)
        if nup_db == nup_file:
            return "unchanged"
        if nup_db:
            return "conflict"

        nup_int = _to_int_or_none(nup_file)
        if nup_int is None:
            return "invalid"

        project.NUP = nup_int
        return "updated"

    def _apply_dates(
        self,
        session,
        source: Source,
        milestones: Dict[str, MilestoneType],
        project_id: int,
        row: pd.Series,
    ) -> Dict[str, int]:
        result = {"created": 0, "updated": 0, "unchanged": 0}
        now = datetime.now()

        for field, milestone_name in DATE_FIELD_TO_MILESTONE.items():
            incoming = _to_date_or_none(row.get(field))
            if incoming is None:
                continue

            dt_value = datetime.combine(incoming, datetime.min.time())
            milestone = milestones[milestone_name]
            existing = (
                session.query(RelevantDate)
                .filter(
                    RelevantDate.ProjectID == project_id,
                    RelevantDate.MilestoneTypeID == milestone.MilestoneTypeID,
                    RelevantDate.SourceID == source.SourceID,
                )
                .one_or_none()
            )

            if existing is None:
                session.add(
                    RelevantDate(
                        ProjectID=project_id,
                        MilestoneTypeID=milestone.MilestoneTypeID,
                        SourceID=source.SourceID,
                        DateValue=dt_value,
                        ExtractedAt=now,
                    )
                )
                result["created"] += 1
            elif existing.DateValue != dt_value:
                existing.DateValue = dt_value
                existing.ExtractedAt = now
                result["updated"] += 1
            else:
                result["unchanged"] += 1

        return result

    @staticmethod
    def _ensure_source(session, source_name: str) -> Source:
        source = session.query(Source).filter(Source.SourceName == source_name).one_or_none()
        if source is None:
            source = Source(SourceName=source_name)
            session.add(source)
            session.flush()
        return source

    @staticmethod
    def _ensure_milestone(session, milestone_name: str) -> MilestoneType:
        milestone = (
            session.query(MilestoneType)
            .filter(MilestoneType.MilestoneName == milestone_name)
            .one_or_none()
        )
        if milestone is None:
            milestone = MilestoneType(MilestoneName=milestone_name)
            session.add(milestone)
            session.flush()
        return milestone

    @staticmethod
    def _detail(row: pd.Series, status: str, message: str) -> Dict[str, Any]:
        return {
            "status": status,
            "message": message,
            "matched_project_id": row.get("matched_project_id"),
            "matched_project_name": row.get("matched_project_name"),
            "source_sheet": row.get("source_sheet"),
            "row_number": row.get("row_number"),
            "project_name": row.get("project_name"),
            "nup": row.get("nup"),
        }


def _name_similarity(left: Any, right: Any) -> float:
    left_text = _clean_text(left)
    right_text = _clean_text(right)
    if not left_text or not right_text:
        return 0.0
    return SequenceMatcher(None, left_text, right_text).ratio()


def _clean_text(value: Any) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text if text else None


def _normalize_nup(value: Any) -> Optional[str]:
    text = _clean_text(value)
    if not text:
        return None
    text = text.replace(".0", "") if text.endswith(".0") else text
    text = "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_", "/"})
    return text or None


def _to_int_or_none(value: Any) -> Optional[int]:
    text = _clean_text(value)
    if not text:
        return None
    text = text.replace(".0", "") if text.endswith(".0") else text
    return int(text) if text.isdigit() else None


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
