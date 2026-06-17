"""Excel export service for raw operational project data.

Export convention:
- Code/database structure stays in English.
- Exported values stay raw as stored in the database.
- Summary is macro-level, not a project list.
- Overview_All is the portfolio-level project list.
- Project-type sheets use explicit allowlists by type to avoid non-applicable
  attributes leaking from wide dataframes.
- Dates and Documents are exported raw, without derived columns.
- Electrical modeling is optional and evaluated against one selected model only.
"""

from __future__ import annotations

import io
from datetime import date
from typing import Iterable

import pandas as pd
from sqlalchemy import text


REFERENCE_MILESTONE_PRIORITY = [
    "COD_Actual",
    "COD_Estimated",
    "Commissioning_Actual",
    "Commissioning_Estimated",
    "StartConstruction_Actual",
    "StartConstruction_Estimated",
]

OVERVIEW_PROJECT_COLUMNS = [
    "ProjectID",
    "ProjectName",
    "OwnerName",
    "ProjectEntityName",
    "NUP",
    "ProjectType",
    "project_discriminator",
    "StatusName",
]

TYPE_BASE_COLUMNS = [
    "ProjectID",
    "ProjectName",
    "OwnerName",
    "ProjectEntityName",
    "NUP",
    "StatusName",
]

REFERENCE_COLUMNS = [
    "ReferenceMilestone",
    "ReferenceDate",
    "ReferenceSource",
]

COUNT_COLUMNS = [
    "DateCount",
    "DocumentCount",
]

SELECTED_MODEL_COLUMN = "IsInSelectedElectricalModel"

PROJECT_TYPE_SHEETS = {
    "transmission": "Transmission",
    "generation": "Generation",
    "bess": "BESS",
    "der": "DER",
}

SHEET_NAMES = {
    "summary": "Summary",
    "overview": "Overview_All",
    "dates": "Dates",
    "documents": "Documents",
    "export_info": "Export_Info",
    "empty": "Sin datos",
}

# Explicit allowlists. Project-type sheets should not keep arbitrary columns from
# wide joined dataframes, because that leaks attributes from other project types.
TYPE_ALLOWED_FEATURE_COLUMNS = {
    "transmission": [
        "VoltageLevel",
        "SubstationOrNode",
        "Substation",
        "Node",
        "BayName",
        "Busbar",
        "LineName",
        "LineLength",
        "CircuitCount",
        "TransmissionTotalCapacity",
        "Location",
        "Region",
        "Commune",
        "PGP_URL",
        "SEO_URL",
        "SEOURL",
        "SEO URL",
        "StartConstructionSEO",
        "Start_Construction SEO",
    ],
    "generation": [
        "Technology",
        "TechnologyGroup",
        "PowerCapacity",
        "GenerationTotalCapacity",
        "SubstationOrNode",
        "Substation",
        "Node",
        "BayName",
        "Location",
        "Region",
        "Commune",
        "PGP_URL",
    ],
    "bess": [
        "Technology",
        "TechnologyGroup",
        "PowerCapacity",
        "StorageCapacity",
        "StorageDuration",
        "EnergyCapacity",
        "DurationHours",
        "SubstationOrNode",
        "Substation",
        "Node",
        "BayName",
        "Location",
        "Region",
        "Commune",
        "PGP_URL",
    ],
    "der": [
        "Technology",
        "TechnologyGroup",
        "PowerCapacity",
        "GenerationTotalCapacity",
        "SubstationOrNode",
        "Substation",
        "Node",
        "BayName",
        "Location",
        "Region",
        "Commune",
        "PGP_URL",
    ],
}


def build_projects_excel(
    overview_df: pd.DataFrame,
    features_df: pd.DataFrame,
    documents_df: pd.DataFrame,
    dates_df: pd.DataFrame,
    include_electrical_modeling: bool = False,
    electrical_model_id: int | None = None,
) -> bytes:
    """Return an .xlsx workbook with raw operational project data."""
    overview_df = _ensure_dataframe(overview_df)
    features_df = _ensure_dataframe(features_df)
    documents_df = _ensure_dataframe(documents_df)
    dates_df = _ensure_dataframe(dates_df)

    selected_model_id = _normalize_optional_int(electrical_model_id)
    include_selected_model = include_electrical_modeling and selected_model_id is not None

    reference_df = _build_reference_dates(dates_df)
    counts_df = _build_counts(dates_df, documents_df)
    modeled_project_ids = (
        _load_modeled_project_ids(selected_model_id) if include_selected_model else set()
    )
    selected_model_metadata = (
        _load_selected_model_metadata(selected_model_id) if include_selected_model else {}
    )

    sheets: list[tuple[str, pd.DataFrame]] = [
        (
            SHEET_NAMES["summary"],
            _build_summary_sheet(
                overview_df=overview_df,
                dates_df=dates_df,
                documents_df=documents_df,
                reference_df=reference_df,
                modeled_project_ids=modeled_project_ids,
                include_selected_model=include_selected_model,
                selected_model_metadata=selected_model_metadata,
            ),
        ),
        (
            SHEET_NAMES["overview"],
            _build_overview_sheet(
                overview_df=overview_df,
                reference_df=reference_df,
                counts_df=counts_df,
                modeled_project_ids=modeled_project_ids,
                include_selected_model=include_selected_model,
            ),
        ),
    ]

    for project_type, sheet_name in PROJECT_TYPE_SHEETS.items():
        type_sheet = _build_project_type_sheet(
            overview_df=overview_df,
            features_df=features_df,
            project_type=project_type,
            reference_df=reference_df,
            modeled_project_ids=modeled_project_ids,
            include_selected_model=include_selected_model,
        )
        if not _is_placeholder_or_empty(type_sheet):
            sheets.append((sheet_name, type_sheet))

    sheets.extend(
        [
            (SHEET_NAMES["dates"], _build_raw_sheet(dates_df)),
            (SHEET_NAMES["documents"], _build_raw_sheet(documents_df)),
        ]
    )

    if include_selected_model:
        sheets.append(
            (
                SHEET_NAMES["export_info"],
                _build_export_info_sheet(
                    selected_model_id=selected_model_id,
                    selected_model_metadata=selected_model_metadata,
                    modeled_project_ids=modeled_project_ids,
                    overview_df=overview_df,
                ),
            )
        )

    return _write_workbook(sheets)


def list_electrical_models_for_export() -> pd.DataFrame:
    """Return active electrical models available for export selection."""
    query = text(
        """
        SELECT
            em.ElectricalModelID,
            s.SoftwareName,
            em.ElectricalModelName
        FROM ElectricalModel em
        INNER JOIN Software s ON em.SoftwareID = s.SoftwareID
        WHERE em.IsActive = 1
        ORDER BY s.SoftwareName, em.ElectricalModelName;
        """
    )

    try:
        from database.db_connection import get_sqlserver_engine

        engine = get_sqlserver_engine()
        with engine.connect() as connection:
            return pd.read_sql_query(query, connection)
    except Exception:
        return pd.DataFrame(
            columns=["ElectricalModelID", "SoftwareName", "ElectricalModelName"]
        )


def suggested_filename(prefix: str = "gridassets_export") -> str:
    """Return a timestamped Excel filename."""
    timestamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
    return f"{prefix}_{timestamp}.xlsx"


def _build_summary_sheet(
    overview_df: pd.DataFrame,
    dates_df: pd.DataFrame,
    documents_df: pd.DataFrame,
    reference_df: pd.DataFrame,
    modeled_project_ids: set[int],
    include_selected_model: bool,
    selected_model_metadata: dict[str, object],
) -> pd.DataFrame:
    """Build macro-level export metrics."""
    rows: list[dict[str, object]] = []

    total_projects = len(overview_df) if not overview_df.empty else 0
    rows.append({"Metric": "TotalProjects", "Value": total_projects})

    if not overview_df.empty:
        if "project_discriminator" in overview_df.columns:
            type_counts = (
                overview_df["project_discriminator"]
                .fillna("Unknown")
                .astype(str)
                .str.lower()
                .value_counts()
                .to_dict()
            )
            for project_type in PROJECT_TYPE_SHEETS:
                rows.append(
                    {
                        "Metric": f"{PROJECT_TYPE_SHEETS[project_type]}Projects",
                        "Value": int(type_counts.get(project_type, 0)),
                    }
                )

        if "NUP" in overview_df.columns:
            nup_series = overview_df["NUP"]
            projects_with_nup = int(nup_series.notna().sum())
            rows.extend(
                [
                    {"Metric": "ProjectsWithNUP", "Value": projects_with_nup},
                    {
                        "Metric": "ProjectsWithoutNUP",
                        "Value": int(total_projects - projects_with_nup),
                    },
                ]
            )

    rows.extend(
        [
            {"Metric": "TotalDates", "Value": int(len(dates_df))},
            {"Metric": "TotalDocuments", "Value": int(len(documents_df))},
        ]
    )

    if not reference_df.empty and "ProjectID" in reference_df.columns:
        projects_with_reference = int(reference_df["ProjectID"].dropna().nunique())
    else:
        projects_with_reference = 0
    rows.extend(
        [
            {"Metric": "ProjectsWithReferenceDate", "Value": projects_with_reference},
            {
                "Metric": "ProjectsWithoutReferenceDate",
                "Value": int(max(total_projects - projects_with_reference, 0)),
            },
        ]
    )

    if include_selected_model:
        modeled_count = _count_modeled_projects_in_overview(
            overview_df=overview_df,
            modeled_project_ids=modeled_project_ids,
        )
        rows.extend(
            [
                {
                    "Metric": "SelectedElectricalModelID",
                    "Value": selected_model_metadata.get("ElectricalModelID"),
                },
                {
                    "Metric": "SelectedSoftwareName",
                    "Value": selected_model_metadata.get("SoftwareName"),
                },
                {
                    "Metric": "SelectedElectricalModelName",
                    "Value": selected_model_metadata.get("ElectricalModelName"),
                },
                {
                    "Metric": "ProjectsInSelectedElectricalModel",
                    "Value": modeled_count,
                },
                {
                    "Metric": "ProjectsOutsideSelectedElectricalModel",
                    "Value": int(max(total_projects - modeled_count, 0)),
                },
            ]
        )

    return pd.DataFrame(rows, columns=["Metric", "Value"])


def _build_overview_sheet(
    overview_df: pd.DataFrame,
    reference_df: pd.DataFrame,
    counts_df: pd.DataFrame,
    modeled_project_ids: set[int],
    include_selected_model: bool,
) -> pd.DataFrame:
    """Build the all-project overview list."""
    if overview_df.empty:
        return _build_empty_export_sheet()

    overview = _select_existing_columns(
        overview_df.copy(),
        OVERVIEW_PROJECT_COLUMNS,
        keep_remaining=False,
    )
    overview = _merge_on_project_id(overview, reference_df)
    overview = _merge_on_project_id(overview, counts_df)

    for column in COUNT_COLUMNS:
        if column not in overview.columns:
            overview[column] = 0
        overview[column] = overview[column].fillna(0).astype(int)

    if include_selected_model:
        overview = _add_selected_model_flag(overview, modeled_project_ids)

    return _select_existing_columns(
        overview,
        OVERVIEW_PROJECT_COLUMNS
        + REFERENCE_COLUMNS
        + COUNT_COLUMNS
        + ([SELECTED_MODEL_COLUMN] if include_selected_model else []),
        keep_remaining=False,
    )


def _build_project_type_sheet(
    overview_df: pd.DataFrame,
    features_df: pd.DataFrame,
    project_type: str,
    reference_df: pd.DataFrame,
    modeled_project_ids: set[int],
    include_selected_model: bool,
) -> pd.DataFrame:
    """Build one denormalized sheet for a project type using a column allowlist."""
    if overview_df.empty or "project_discriminator" not in overview_df.columns:
        return _build_empty_export_sheet()

    overview_type = overview_df[
        overview_df["project_discriminator"].astype(str).str.lower() == project_type
    ].copy()
    if overview_type.empty:
        return _build_empty_export_sheet()

    result = _select_existing_columns(
        overview_type,
        TYPE_BASE_COLUMNS,
        keep_remaining=False,
    )

    if not features_df.empty and "ProjectID" in features_df.columns:
        features_type = features_df.copy()
        if "project_discriminator" in features_type.columns:
            features_type = features_type[
                features_type["project_discriminator"].astype(str).str.lower()
                == project_type
            ]
        feature_columns = [
            column
            for column in TYPE_ALLOWED_FEATURE_COLUMNS.get(project_type, [])
            if column in features_type.columns
        ]
        if feature_columns:
            features_type = features_type[["ProjectID", *feature_columns]].copy()
            result = result.merge(features_type, on="ProjectID", how="left")

    result = _merge_on_project_id(result, reference_df)

    if include_selected_model:
        result = _add_selected_model_flag(result, modeled_project_ids)

    final_columns = (
        TYPE_BASE_COLUMNS
        + TYPE_ALLOWED_FEATURE_COLUMNS.get(project_type, [])
        + REFERENCE_COLUMNS
        + ([SELECTED_MODEL_COLUMN] if include_selected_model else [])
    )
    return _select_existing_columns(result, final_columns, keep_remaining=False)


def _build_raw_sheet(df: pd.DataFrame) -> pd.DataFrame:
    """Return a raw sheet without additional identifiers or derived columns."""
    if df.empty:
        return _build_empty_export_sheet()
    return df.copy()


def _build_export_info_sheet(
    selected_model_id: int | None,
    selected_model_metadata: dict[str, object],
    modeled_project_ids: set[int],
    overview_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build metadata for the selected electrical model export."""
    total_projects = len(overview_df) if not overview_df.empty else 0
    modeled_count = _count_modeled_projects_in_overview(
        overview_df=overview_df,
        modeled_project_ids=modeled_project_ids,
    )
    rows = [
        {"Field": "ExportGeneratedAt", "Value": pd.Timestamp.now()},
        {"Field": "SelectedElectricalModelID", "Value": selected_model_id},
        {
            "Field": "SelectedSoftwareName",
            "Value": selected_model_metadata.get("SoftwareName"),
        },
        {
            "Field": "SelectedElectricalModelName",
            "Value": selected_model_metadata.get("ElectricalModelName"),
        },
        {"Field": "TotalProjects", "Value": total_projects},
        {"Field": "ProjectsInSelectedElectricalModel", "Value": modeled_count},
        {
            "Field": "ProjectsOutsideSelectedElectricalModel",
            "Value": int(max(total_projects - modeled_count, 0)),
        },
    ]
    return pd.DataFrame(rows, columns=["Field", "Value"])


def _build_reference_dates(dates_df: pd.DataFrame) -> pd.DataFrame:
    """Pick one reference milestone/date per project using domain priority."""
    required_columns = {"ProjectID", "MilestoneName", "DateValue"}
    if dates_df.empty or not required_columns.issubset(dates_df.columns):
        return pd.DataFrame(columns=["ProjectID", *REFERENCE_COLUMNS])

    dates = dates_df.copy()
    dates["_priority"] = dates["MilestoneName"].map(_milestone_priority)
    dates["_date_sort"] = _safe_to_datetime(dates["DateValue"])

    if "ExtractedAt" in dates.columns:
        dates["_extracted_sort"] = _safe_to_datetime(dates["ExtractedAt"])
    else:
        dates["_extracted_sort"] = pd.NaT

    dates = dates[dates["_priority"] < len(REFERENCE_MILESTONE_PRIORITY)].copy()
    if dates.empty:
        return pd.DataFrame(columns=["ProjectID", *REFERENCE_COLUMNS])

    dates = dates.sort_values(
        by=["ProjectID", "_priority", "_date_sort", "_extracted_sort"],
        ascending=[True, True, False, False],
    )
    reference = dates.drop_duplicates(subset=["ProjectID"], keep="first")

    source_series = (
        reference["SourceName"]
        if "SourceName" in reference.columns
        else pd.Series([pd.NA] * len(reference), index=reference.index)
    )

    return pd.DataFrame(
        {
            "ProjectID": reference["ProjectID"].values,
            "ReferenceMilestone": reference["MilestoneName"].values,
            "ReferenceDate": reference["DateValue"].values,
            "ReferenceSource": source_series.values,
        }
    )


def _build_counts(dates_df: pd.DataFrame, documents_df: pd.DataFrame) -> pd.DataFrame:
    """Build project-level date/document counts for the overview sheet."""
    project_ids = set()
    if "ProjectID" in dates_df.columns:
        project_ids.update(dates_df["ProjectID"].dropna().tolist())
    if "ProjectID" in documents_df.columns:
        project_ids.update(documents_df["ProjectID"].dropna().tolist())

    counts = pd.DataFrame({"ProjectID": sorted(project_ids)})
    if counts.empty:
        return pd.DataFrame(columns=["ProjectID", *COUNT_COLUMNS])

    if not dates_df.empty and "ProjectID" in dates_df.columns:
        date_counts = dates_df.groupby("ProjectID").size().reset_index(name="DateCount")
        counts = _merge_on_project_id(counts, date_counts)
    else:
        counts["DateCount"] = 0

    if not documents_df.empty and "ProjectID" in documents_df.columns:
        document_counts = (
            documents_df.groupby("ProjectID").size().reset_index(name="DocumentCount")
        )
        counts = _merge_on_project_id(counts, document_counts)
    else:
        counts["DocumentCount"] = 0

    for column in COUNT_COLUMNS:
        if column not in counts.columns:
            counts[column] = 0
        counts[column] = counts[column].fillna(0).astype(int)

    return counts


def _load_modeled_project_ids(electrical_model_id: int) -> set[int]:
    """Return project IDs modeled in the selected electrical model."""
    query = text(
        """
        SELECT ProjectID
        FROM ProjectElectricalModel
        WHERE ElectricalModelID = :electrical_model_id
          AND IsModeled = 1;
        """
    )

    try:
        from database.db_connection import get_sqlserver_engine

        engine = get_sqlserver_engine()
        with engine.connect() as connection:
            rows = connection.execute(
                query,
                {"electrical_model_id": int(electrical_model_id)},
            ).fetchall()
        return {int(row[0]) for row in rows if row[0] is not None}
    except Exception:
        return set()


def _load_selected_model_metadata(electrical_model_id: int | None) -> dict[str, object]:
    """Return metadata for the selected electrical model."""
    if electrical_model_id is None:
        return {}

    query = text(
        """
        SELECT
            em.ElectricalModelID,
            s.SoftwareName,
            em.ElectricalModelName
        FROM ElectricalModel em
        INNER JOIN Software s ON em.SoftwareID = s.SoftwareID
        WHERE em.ElectricalModelID = :electrical_model_id;
        """
    )

    try:
        from database.db_connection import get_sqlserver_engine

        engine = get_sqlserver_engine()
        with engine.connect() as connection:
            row = connection.execute(
                query,
                {"electrical_model_id": int(electrical_model_id)},
            ).mappings().first()
        return dict(row) if row else {"ElectricalModelID": electrical_model_id}
    except Exception:
        return {"ElectricalModelID": electrical_model_id}


def _add_selected_model_flag(
    df: pd.DataFrame,
    modeled_project_ids: set[int],
) -> pd.DataFrame:
    """Add selected electrical model membership as a boolean column."""
    result = df.copy()
    if "ProjectID" not in result.columns:
        result[SELECTED_MODEL_COLUMN] = False
        return result

    result[SELECTED_MODEL_COLUMN] = result["ProjectID"].map(
        lambda project_id: _project_id_to_int(project_id) in modeled_project_ids
    )
    return result


def _count_modeled_projects_in_overview(
    overview_df: pd.DataFrame,
    modeled_project_ids: set[int],
) -> int:
    """Count selected-model projects present in the exported overview."""
    if overview_df.empty or "ProjectID" not in overview_df.columns:
        return 0
    overview_project_ids = {
        project_id
        for project_id in overview_df["ProjectID"].map(_project_id_to_int).tolist()
        if project_id is not None
    }
    return len(overview_project_ids.intersection(modeled_project_ids))


def _milestone_priority(milestone_name: object) -> int:
    """Return the priority index for a milestone name."""
    name = str(milestone_name or "").strip()
    try:
        return REFERENCE_MILESTONE_PRIORITY.index(name)
    except ValueError:
        return len(REFERENCE_MILESTONE_PRIORITY)


def _safe_to_datetime(series: pd.Series) -> pd.Series:
    """Parse date-like values without pandas day/month warnings."""
    return pd.to_datetime(series, errors="coerce", dayfirst=True)


def _merge_on_project_id(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    """Merge two dataframes on ProjectID when possible."""
    if left.empty:
        return left
    if right.empty or "ProjectID" not in left.columns or "ProjectID" not in right.columns:
        return left
    return left.merge(right, on="ProjectID", how="left")


def _select_existing_columns(
    df: pd.DataFrame,
    preferred_columns: Iterable[str],
    keep_remaining: bool,
) -> pd.DataFrame:
    """Return columns in preferred order, optionally keeping the rest."""
    preferred = [column for column in preferred_columns if column in df.columns]
    if not keep_remaining:
        return df[preferred].copy()

    remaining = [column for column in df.columns if column not in preferred]
    return df[preferred + remaining].copy()


def _write_workbook(sheets: list[tuple[str, pd.DataFrame]]) -> bytes:
    """Write all sheets into a formatted workbook."""
    buffer = io.BytesIO()
    visible_sheets = [(name, _ensure_dataframe(df)) for name, df in sheets]

    if not visible_sheets:
        visible_sheets = [(SHEET_NAMES["empty"], _build_empty_export_sheet())]

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name, sheet_df in visible_sheets:
            safe_sheet_name = _safe_sheet_name(sheet_name)
            if sheet_df.empty:
                sheet_df = _build_empty_export_sheet()
            sheet_df.to_excel(writer, sheet_name=safe_sheet_name, index=False)
            _format_sheet(writer, safe_sheet_name, sheet_df)

    return buffer.getvalue()


def _format_sheet(writer: pd.ExcelWriter, sheet_name: str, df: pd.DataFrame) -> None:
    """Apply simple Excel formatting to one sheet."""
    worksheet = writer.sheets[sheet_name]
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions

    for column_cells in worksheet.columns:
        column_letter = column_cells[0].column_letter
        max_length = 0

        for cell in column_cells:
            value = cell.value
            if value is None:
                continue
            max_length = max(max_length, len(str(value)))

        worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, 10), 48)

    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, (pd.Timestamp, date)):
                cell.number_format = "yyyy-mm-dd"


def _safe_sheet_name(sheet_name: str) -> str:
    """Return a valid Excel sheet name."""
    invalid_chars = ["\\", "/", "*", "?", ":", "[", "]"]
    safe_name = str(sheet_name)
    for char in invalid_chars:
        safe_name = safe_name.replace(char, "-")
    return safe_name[:31] or "Sheet"


def _build_empty_export_sheet() -> pd.DataFrame:
    """Return a visible placeholder sheet for empty exports."""
    return pd.DataFrame(
        {
            "Estado": ["Sin datos"],
            "Mensaje": ["No hay información disponible para esta hoja."],
        }
    )


def _is_placeholder_or_empty(df: pd.DataFrame) -> bool:
    """Return True when a sheet should not be included as a data sheet."""
    if df.empty:
        return True
    return list(df.columns) == ["Estado", "Mensaje"] and len(df) == 1


def _ensure_dataframe(df: pd.DataFrame | None) -> pd.DataFrame:
    """Return a safe dataframe instance."""
    if df is None:
        return pd.DataFrame()
    return df.copy()


def _normalize_optional_int(value: object) -> int | None:
    """Return an int or None for optional identifiers."""
    if value in (None, ""):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _project_id_to_int(value: object) -> int | None:
    """Normalize ProjectID-like values for set membership checks."""
    try:
        if pd.isna(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
