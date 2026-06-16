"""Excel export service for project data.

This module builds a multi-sheet workbook with one sheet per project type.
It is intentionally defensive: if there are no projects yet, it still returns
an Excel file with a visible placeholder sheet so Streamlit does not fail while
rendering the download button.
"""

from __future__ import annotations

import io
from datetime import date
from typing import Iterable

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
    """Return an .xlsx workbook with project information.

    The workbook normally contains one sheet per project type. If the database
    has no projects yet, a placeholder sheet is created. This avoids the
    openpyxl error: "At least one sheet must be visible".
    """
    overview_df = _ensure_dataframe(overview_df)
    features_df = _ensure_dataframe(features_df)
    documents_df = _ensure_dataframe(documents_df)
    dates_df = _ensure_dataframe(dates_df)

    buffer = io.BytesIO()
    written_sheets = 0

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for type_key, sheet_name in PROJECT_TYPE_SHEETS.items():
            sheet_df = _build_type_sheet(
                project_type=type_key,
                overview_df=overview_df,
                features_df=features_df,
                documents_df=documents_df,
                dates_df=dates_df,
            )

            if sheet_df.empty:
                continue

            sheet_df.to_excel(writer, sheet_name=sheet_name, index=False)
            written_sheets += 1

        if written_sheets == 0:
            _build_empty_workbook_sheet().to_excel(
                writer,
                sheet_name="Sin datos",
                index=False,
            )

    return buffer.getvalue()


def _build_type_sheet(
    project_type: str,
    overview_df: pd.DataFrame,
    features_df: pd.DataFrame,
    documents_df: pd.DataFrame,
    dates_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build one export sheet for a specific project type."""
    discriminator_column = "project_discriminator"

    if overview_df.empty or discriminator_column not in overview_df.columns:
        return pd.DataFrame()

    base = overview_df[overview_df[discriminator_column] == project_type].copy()
    base = base.drop(columns=[discriminator_column], errors="ignore")

    if base.empty or "ProjectID" not in base.columns:
        return pd.DataFrame()

    project_ids = set(base["ProjectID"].dropna().tolist())

    type_features = _filter_by_project_ids(features_df, project_ids)
    type_features = type_features.drop(columns=["ProjectType"], errors="ignore")
    type_features = type_features.dropna(axis=1, how="all")

    dates_pivot = _build_dates_pivot(dates_df, project_ids)
    documents_series = _build_documents_series(documents_df, project_ids)

    result = base.copy()

    if not type_features.empty and "ProjectID" in type_features.columns:
        result = result.merge(type_features, on="ProjectID", how="left")

    if not dates_pivot.empty and "ProjectID" in dates_pivot.columns:
        result = result.merge(dates_pivot, on="ProjectID", how="left")

    if not documents_series.empty and "ProjectID" in documents_series.columns:
        result = result.merge(documents_series, on="ProjectID", how="left")

    return result


def _build_dates_pivot(
    dates_df: pd.DataFrame,
    project_ids: set) -> pd.DataFrame:
    """Pivot project dates into one column per milestone/source."""
    type_dates = _filter_by_project_ids(dates_df, project_ids)

    required_columns = {"ProjectID", "MilestoneName", "DateValue"}
    if type_dates.empty or not required_columns.issubset(type_dates.columns):
        return pd.DataFrame({"ProjectID": list(project_ids)})

    type_dates = type_dates.copy()
    type_dates["_col"] = type_dates.apply(
        lambda row: _build_milestone_column_name(row),
        axis=1,
    )

    type_dates = type_dates.drop_duplicates(
        subset=["ProjectID", "_col"],
        keep="first",
    )

    dates_pivot = type_dates.pivot(
        index="ProjectID",
        columns="_col",
        values="DateValue",
    ).reset_index()
    dates_pivot.columns.name = None

    return dates_pivot


def _build_documents_series(
    documents_df: pd.DataFrame,
    project_ids: set) -> pd.DataFrame:
    """Concatenate legal documents into one text column per project."""
    type_documents = _filter_by_project_ids(documents_df, project_ids)

    if type_documents.empty or "ProjectID" not in type_documents.columns:
        return pd.DataFrame({"ProjectID": list(project_ids), "Documentos": ""})

    documents_series = (
        type_documents.groupby("ProjectID")
        .apply(_concat_documents, include_groups=False)
        .reset_index()
        .rename(columns={0: "Documentos"})
    )

    return documents_series


def _concat_documents(group: pd.DataFrame) -> str:
    """Return a compact legal-document summary for one project."""
    parts = []

    for _, row in group.iterrows():
        name = str(row.get("DocumentName", "") or "").strip()
        document_type = str(row.get("DocumentType", "") or "").strip()

        if not name:
            continue

        if document_type:
            parts.append(f"{document_type}: {name}")
        else:
            parts.append(name)

    return "; ".join(parts)


def _build_milestone_column_name(row: pd.Series) -> str:
    """Build a human-readable date column name."""
    milestone = str(row.get("MilestoneName", "") or "").strip()
    source = str(row.get("SourceName", "") or "").strip()

    if milestone and source:
        return f"{milestone} ({source})"

    return milestone or source or "Fecha"


def _filter_by_project_ids(df: pd.DataFrame, project_ids: Iterable) -> pd.DataFrame:
    """Return rows whose ProjectID belongs to project_ids, safely."""
    if df.empty or "ProjectID" not in df.columns:
        return pd.DataFrame({"ProjectID": list(project_ids)})

    return df[df["ProjectID"].isin(project_ids)].copy()


def _build_empty_workbook_sheet() -> pd.DataFrame:
    """Return a visible placeholder sheet for an empty database."""
    return pd.DataFrame(
        {
            "Estado": ["Sin proyectos"],
            "Mensaje": [
                "La base de datos está creada, pero aún no hay proyectos para exportar."
            ],
        }
    )


def _ensure_dataframe(value) -> pd.DataFrame:
    """Normalize optional data into a pandas DataFrame."""
    if isinstance(value, pd.DataFrame):
        return value

    return pd.DataFrame()


def suggested_filename() -> str:
    """Return the default Excel export filename."""
    return f"proyectos_sen_{date.today().strftime('%Y-%m-%d')}.xlsx"
