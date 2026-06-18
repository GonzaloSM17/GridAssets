"""Preview matching service for CEN connection enrichment files.

This module is intentionally read-only. It compares normalized CEN connection
records against existing GridAssets projects and returns a preview DataFrame.

Matching strategy:
1. Try NUP when both the incoming record and the database project have NUP.
2. Fall back to normalized project name when NUP is absent or unavailable.
3. Keep ambiguous matches as review items; never auto-resolve them here.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any, Iterable, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from database.db_connection import get_sqlserver_engine
from parsers.cen_connection_files_parse import normalize_project_name


DATE_FIELDS = [
    "commissioning_actual",
    "commissioning_estimated",
    "cod_actual",
    "cod_estimated",
]

DISPLAY_COLUMNS = [
    "source_sheet",
    "row_number",
    "connection_project_type",
    "nup",
    "project_name",
    "company",
    "commissioning_actual",
    "commissioning_estimated",
    "cod_actual",
    "cod_estimated",
    "match_status",
    "match_method",
    "matched_project_id",
    "matched_project_name",
    "matched_project_type",
    "matched_project_nup",
    "match_score",
    "candidate_count",
    "top_candidates",
    "proposed_action",
    "comment",
]


@dataclass(frozen=True)
class MatchThresholds:
    """Name matching thresholds used by the preview service."""

    auto_name_score: float = 0.92
    review_name_score: float = 0.82
    ambiguity_delta: float = 0.03


class CENConnectionEnrichmentService:
    """Build read-only match previews between CEN records and DB projects."""

    def __init__(
        self,
        engine: Optional[Engine] = None,
        thresholds: Optional[MatchThresholds] = None,
    ) -> None:
        self.engine = engine or get_sqlserver_engine()
        self.thresholds = thresholds or MatchThresholds()

    def load_project_reference(self) -> pd.DataFrame:
        """Load the project fields needed for matching.

        The query only reads existing projects. It includes a few auxiliary
        fields that help interpret candidates in the Streamlit preview.
        """
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
            LEFT JOIN ProjectEntity pe
                ON pe.ProjectEntityID = p.ProjectEntityID
            LEFT JOIN TransmissionProject tp
                ON tp.ProjectID = p.ProjectID
            LEFT JOIN GenerationProject gp
                ON gp.ProjectID = p.ProjectID
            LEFT JOIN DERProject dp
                ON dp.ProjectID = p.ProjectID
            LEFT JOIN BESSProject bp
                ON bp.ProjectID = p.ProjectID
            LEFT JOIN Technology tg
                ON tg.TechnologyID = gp.TechnologyID
            LEFT JOIN Technology td
                ON td.TechnologyID = dp.TechnologyID
            LEFT JOIN Technology tb
                ON tb.TechnologyID = bp.TechnologyID
            LEFT JOIN Bay b
                ON b.BayID = COALESCE(gp.BayID, dp.BayID, bp.BayID)
            """
        )
        with self.engine.connect() as connection:
            projects = pd.read_sql_query(query, connection)

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
        """Return a read-only match preview for normalized CEN records."""
        projects = self.load_project_reference()
        return self.build_match_preview_from_projects(records, projects)

    def build_match_preview_from_projects(
        self,
        records: pd.DataFrame,
        projects: pd.DataFrame,
    ) -> pd.DataFrame:
        """Return preview using an already loaded project reference DataFrame."""
        if records is None or records.empty:
            return pd.DataFrame(columns=DISPLAY_COLUMNS)

        if projects is None or projects.empty:
            preview = records.copy()
            preview["match_status"] = "not_found"
            preview["match_method"] = "none"
            preview["matched_project_id"] = pd.NA
            preview["matched_project_name"] = pd.NA
            preview["matched_project_type"] = pd.NA
            preview["matched_project_nup"] = pd.NA
            preview["match_score"] = 0.0
            preview["candidate_count"] = 0
            preview["top_candidates"] = ""
            preview["proposed_action"] = "skip"
            preview["comment"] = "No projects were found in the database reference."
            return self._ordered_preview(preview)

        work = records.copy()
        for col in ["nup", "project_name", "connection_project_type", "company"]:
            if col not in work.columns:
                work[col] = pd.NA
        if "normalized_project_name" not in work.columns:
            work["normalized_project_name"] = work["project_name"].apply(normalize_project_name)
        work["nup_key"] = work["nup"].apply(_normalize_nup_key)
        work["connection_type_key"] = work["connection_project_type"].apply(_normalize_type_key)
        work["normalized_company"] = work["company"].apply(_normalize_text)

        rows = []
        for _, record in work.iterrows():
            rows.append(self._match_one(record, projects))

        preview = pd.DataFrame(rows)
        return self._ordered_preview(preview)

    def summarize_preview(self, preview: pd.DataFrame) -> pd.DataFrame:
        """Return match status counts for UI display."""
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
                typed_nup_candidates = _filter_compatible_projects(
                    nup_candidates,
                    record.get("connection_type_key"),
                )
                if not typed_nup_candidates.empty:
                    return self._result_from_candidates(
                        base=base,
                        candidates=typed_nup_candidates,
                        status_if_single="matched_by_nup",
                        method="nup_type",
                        score=1.0,
                        comment="Matched by NUP and compatible project type.",
                    )
                return self._result_from_candidates(
                    base=base,
                    candidates=nup_candidates,
                    status_if_single="nup_type_mismatch",
                    method="nup_only",
                    score=1.0,
                    comment="NUP exists in DB, but project type is not compatible. Review manually.",
                )

        name = record.get("normalized_project_name")
        if not _has_value(name):
            return self._empty_result(
                base,
                status="no_project_name",
                method="none",
                action="skip",
                comment="No NUP match and no project name available for name matching.",
            )

        name_candidates = self._score_name_candidates(record, compatible)
        if name_candidates.empty and len(compatible) != len(projects):
            # If strict type compatibility gives no candidates, keep a weaker global
            # candidate list for manual review without marking it as matched.
            name_candidates = self._score_name_candidates(record, projects)

        if name_candidates.empty:
            return self._empty_result(
                base,
                status="not_found",
                method="name",
                action="skip",
                comment="No project candidate exceeded the minimum name score.",
            )

        best = name_candidates.iloc[0]
        best_score = float(best["match_score"])
        top_band = name_candidates.loc[
            name_candidates["match_score"] >= best_score - self.thresholds.ambiguity_delta
        ]

        if best_score >= self.thresholds.auto_name_score and len(top_band) == 1:
            return self._result_from_candidates(
                base=base,
                candidates=name_candidates.head(1),
                status_if_single="matched_by_name",
                method="name_high_confidence",
                score=best_score,
                comment="Matched by normalized project name with high confidence. Preview only.",
            )

        if best_score >= self.thresholds.review_name_score:
            status = "ambiguous_name" if len(top_band) > 1 else "candidate_by_name"
            return self._result_from_candidates(
                base=base,
                candidates=name_candidates.head(5),
                status_if_single=status,
                method="name_review",
                score=best_score,
                comment="Name candidate found. Review before applying any update.",
            )

        return self._empty_result(
            base,
            status="not_found",
            method="name",
            action="skip",
            comment="Best name candidate is below the review threshold.",
            score=best_score,
            candidates=name_candidates.head(5),
        )

    def _score_name_candidates(self, record: pd.Series, projects: pd.DataFrame) -> pd.DataFrame:
        if projects.empty:
            return projects.copy()

        name = record.get("normalized_project_name")
        company = record.get("normalized_company")
        technology = _normalize_text(record.get("technology"))

        rows = []
        for _, project in projects.iterrows():
            project_name = project.get("normalized_project_name")
            if not _has_value(project_name):
                continue

            base_score = _name_similarity(name, project_name)
            token_score = _token_similarity(name, project_name)
            score = max(base_score, token_score)

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

        return (
            pd.DataFrame(rows)
            .sort_values(["match_score", "ProjectName"], ascending=[False, True])
            .reset_index(drop=True)
        )

    def _result_from_candidates(
        self,
        base: dict[str, Any],
        candidates: pd.DataFrame,
        status_if_single: str,
        method: str,
        score: float,
        comment: str,
    ) -> dict[str, Any]:
        candidate_count = int(len(candidates))
        top = candidates.iloc[0]

        if candidate_count > 1 and status_if_single.startswith("matched_by_nup"):
            status = "ambiguous_nup"
            action = "review"
            comment = "Multiple DB projects share this NUP. Review with type/name before applying."
        elif candidate_count > 1 and status_if_single == "nup_type_mismatch":
            status = "nup_type_mismatch"
            action = "review"
        elif status_if_single in {"candidate_by_name", "ambiguous_name", "nup_type_mismatch"}:
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
                "candidate_count": candidate_count,
                "top_candidates": _format_candidates(candidates.head(5)),
                "proposed_action": action,
                "comment": comment,
            }
        )
        return result

    def _empty_result(
        self,
        base: dict[str, Any],
        status: str,
        method: str,
        action: str,
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
                "candidate_count": int(len(candidates)) if candidates is not None else 0,
                "top_candidates": _format_candidates(candidates) if candidates is not None else "",
                "proposed_action": action,
                "comment": comment,
            }
        )
        return result

    def _ordered_preview(self, preview: pd.DataFrame) -> pd.DataFrame:
        for col in DISPLAY_COLUMNS:
            if col not in preview.columns:
                preview[col] = pd.NA
        remaining = [col for col in preview.columns if col not in DISPLAY_COLUMNS]
        return preview[DISPLAY_COLUMNS + remaining]

    def _empty_project_reference(self) -> pd.DataFrame:
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
    if value is None or pd.isna(value):
        return pd.NA
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
    if value is None or pd.isna(value):
        return pd.NA
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
        score_text = f" score={score}" if score != "" and not pd.isna(score) else ""
        nup = candidate.get("NUP", "")
        nup_text = f" NUP={nup}" if nup != "" and not pd.isna(nup) else ""
        parts.append(
            f"{candidate.get('ProjectID')} | {candidate.get('ProjectName')} | "
            f"{candidate.get('ProjectType')}{nup_text}{score_text}"
        )
    return " ; ".join(parts)


def _safe_int(value: Any) -> Any:
    if value is None or pd.isna(value):
        return pd.NA
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return str(value).strip() != ""
