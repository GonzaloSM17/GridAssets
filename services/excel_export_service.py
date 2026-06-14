"""Excel export service — builds a per-type multi-sheet workbook."""

from __future__ import annotations

import io
from datetime import date

import pandas as pd

PROJECT_TYPE_SHEETS = {
    "transmission": "Transmisión",
    "generation": "Generación",
    "bess": "BESS",
    "der": "DER",
}


def build_projects_excel(
    overview_df: pd.DataFrame,
    features_df: pd.DataFrame,
    documents_df: pd.DataFrame,
    dates_df: pd.DataFrame,
) -> bytes:
    """Return a .xlsx with one sheet per project type, each containing all data.

    Each sheet has: overview columns + type features + milestone dates (pivoted)
    + legal documents (concatenated string).
    """
    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for type_key, sheet_name in PROJECT_TYPE_SHEETS.items():
            sheet_df = _build_type_sheet(
                type_key, overview_df, features_df, documents_df, dates_df
            )
            if not sheet_df.empty:
                sheet_df.to_excel(writer, sheet_name=sheet_name, index=False)

    return buffer.getvalue()


def _build_type_sheet(
    project_type: str,
    overview_df: pd.DataFrame,
    features_df: pd.DataFrame,
    documents_df: pd.DataFrame,
    dates_df: pd.DataFrame,
) -> pd.DataFrame:
    disc_col = "project_discriminator"

    if disc_col not in overview_df.columns:
        return pd.DataFrame()

    # 1. Base overview for this type
    base = overview_df[overview_df[disc_col] == project_type].copy()
    base = base.drop(columns=[disc_col], errors="ignore")

    if base.empty:
        return pd.DataFrame()

    project_ids = set(base["ProjectID"])

    # 2. Type-specific features (drop all-null columns)
    type_features = features_df[features_df["ProjectID"].isin(project_ids)].copy()
    type_features = type_features.drop(columns=["ProjectType"], errors="ignore")
    type_features = type_features.dropna(axis=1, how="all")

    # 3. Milestone dates — one column per (MilestoneName + SourceName)
    type_dates = dates_df[dates_df["ProjectID"].isin(project_ids)].copy()

    if not type_dates.empty and "MilestoneName" in type_dates.columns:
        type_dates = type_dates.copy()
        type_dates["_col"] = type_dates.apply(
            lambda r: f"{r.get('MilestoneName', '')} ({r.get('SourceName', '')})",
            axis=1,
        )
        # Keep first (most recent) record per ProjectID + milestone column
        type_dates = type_dates.drop_duplicates(
            subset=["ProjectID", "_col"], keep="first"
        )
        dates_pivot = type_dates.pivot(
            index="ProjectID", columns="_col", values="DateValue"
        ).reset_index()
        dates_pivot.columns.name = None
    else:
        dates_pivot = pd.DataFrame({"ProjectID": list(project_ids)})

    # 4. Legal documents — concatenated string per project
    type_docs = documents_df[documents_df["ProjectID"].isin(project_ids)].copy()

    if not type_docs.empty:

        def _concat(group: pd.DataFrame) -> str:
            parts = []
            for _, row in group.iterrows():
                name = str(row.get("DocumentName", "") or "").strip()
                dtype = str(row.get("DocumentType", "") or "").strip()
                if name:
                    parts.append(f"{dtype}: {name}" if dtype else name)
            return "; ".join(parts)

        docs_series = (
            type_docs.groupby("ProjectID")
            .apply(_concat, include_groups=False)
            .reset_index()
            .rename(columns={0: "Documentos"})
        )
    else:
        docs_series = pd.DataFrame({"ProjectID": list(project_ids), "Documentos": ""})

    # 5. Join all parts
    result = base.merge(type_features, on="ProjectID", how="left")
    result = result.merge(dates_pivot, on="ProjectID", how="left")
    result = result.merge(docs_series, on="ProjectID", how="left")

    return result


def suggested_filename() -> str:
    return f"proyectos_sen_{date.today().strftime('%Y-%m-%d')}.xlsx"
