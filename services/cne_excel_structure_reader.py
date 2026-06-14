"""CNE Excel structure reader.

This module performs a lightweight, deterministic inspection of an uploaded CNE
Excel workbook before the full ProjectParser is executed.

It does not populate the database and it does not parse every project. Its job is
to extract the structural evidence that an AI validation agent can evaluate:
file metadata, sheet names, expected parser sheets, columns, row counts, and a
small sample of rows per sheet.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import json
import math
import unicodedata

import pandas as pd

EXPECTED_SHEET_CONFIG: Dict[str, Dict[str, Any]] = {
    "P.Generación": {"header": 2, "usecols": "B:L"},
    "PMGD": {"header": 2, "usecols": "B:L"},
    "BESS": {"header": 2, "usecols": "B:P"},
    "ON_STxN": {"header": 2, "usecols": "B:G"},
    "OA_STxN": {"header": 2, "usecols": "B:G"},
    "ON_STxZ": {"header": 2, "usecols": "B:G"},
    "OA_STxZ": {"header": 2, "usecols": "B:G"},
    "ON_D418": {"header": 3, "usecols": "B:F"},
    "OA_D418": {"header": 3, "usecols": "B:F"},
    "OEO_D418": {"header": 3, "usecols": "B:F"},
    "OPyM_ST": {"header": 2, "usecols": "B:H"},
    "Art.102": {"header": 2, "usecols": "B:F"},
}

EXPECTED_SHEETS: List[str] = list(EXPECTED_SHEET_CONFIG.keys())

# These requirements mirror the deterministic parser criteria. They are used to
# estimate whether each sheet has enough structure to be parsed safely.
SHEET_FIELD_CRITERIA: Dict[str, Dict[str, List[str]]] = {
    "P.Generación": {
        "name": ["proyecto"],
        "project_entity": ["propietario"],
        "resolution": ["resolución"],
        "cod": ["fecha", "estimada"],
        "power_capacity": ["potencia", "neta"],
        "total_capacity": ["capacidad", "instalada"],
        "location": ["ubicación"],
        "technology": ["tecnología"],
        "bay": ["punto", "conexión"],
    },
    "PMGD": {
        "name": ["proyecto"],
        "project_entity": ["propietario"],
        "resolution": ["resolución"],
        "cod": ["fecha", "estimada"],
        "power_capacity": ["potencia", "neta"],
        "total_capacity": ["capacidad", "instalada"],
        "location": ["ubicación"],
        "technology": ["tecnología"],
        "bay": ["punto", "conexión"],
    },
    "BESS": {
        "name": ["proyecto"],
        "project_entity": ["propietario"],
        "resolution": ["resolución"],
        "cod": ["fecha", "estimada"],
        "power_capacity": ["potencia", "neta"],
        "location": ["ubicación"],
        "technology": ["tecnología"],
        "storage_capacity": ["almacenamiento"],
        "bay": ["punto", "conexión"],
    },
}

TRANSMISSION_SHEETS = [
    "ON_STxN",
    "OA_STxN",
    "ON_STxZ",
    "OA_STxZ",
    "ON_D418",
    "OA_D418",
    "OEO_D418",
    "OPyM_ST",
    "Art.102",
]

TRANSMISSION_CRITERIA = {
    "name": ["proyecto"],
    "project_entity": ["responsable"],
    "act": ["decreto"],
    "act_award": ["adjudicacion"],
    "resolution_exempt": ["resolución", "exenta"],
    "cod": ["fecha"],
    "description": ["descripción"],
    "total_capacity": ["potencia"],
    "voltage_level": ["tensión"],
}

for sheet_name in TRANSMISSION_SHEETS:
    SHEET_FIELD_CRITERIA[sheet_name] = TRANSMISSION_CRITERIA


@dataclass
class SheetStructure:
    sheet_name: str
    exists: bool
    parser_header_row: Optional[int] = None
    parser_usecols: Optional[str] = None
    columns: List[str] = field(default_factory=list)
    normalized_columns: List[str] = field(default_factory=list)
    row_count_raw: int = 0
    row_count_with_project: int = 0
    row_count_parse_candidates: int = 0
    missing_required_fields: List[str] = field(default_factory=list)
    matched_fields: Dict[str, Optional[str]] = field(default_factory=dict)
    sample_rows: List[Dict[str, Any]] = field(default_factory=list)
    read_error: Optional[str] = None


@dataclass
class ExcelStructureReport:
    filename: str
    file_path: str
    extension: str
    file_size_bytes: int
    can_open: bool
    open_error: Optional[str]
    sheet_names: List[str]
    expected_sheets: List[str]
    missing_expected_sheets: List[str]
    unexpected_sheets: List[str]
    sheets: Dict[str, SheetStructure]

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["sheets"] = {name: asdict(sheet) for name, sheet in self.sheets.items()}
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=indent)


def normalize_text(value: Any) -> str:
    value = "" if value is None else str(value)
    value = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(char for char in value if not unicodedata.combining(char))


def find_candidate(columns: List[str], criteria: List[str]) -> Optional[str]:
    normalized_criteria = [normalize_text(item) for item in criteria]
    for column in columns:
        normalized_column = normalize_text(column)
        if all(item in normalized_column for item in normalized_criteria):
            return column
    return None


def make_json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if not isinstance(value, (str, int, float, bool)) else value


class CNEExcelStructureReader:
    """Read lightweight workbook structure required by the CNE validation agent."""

    def __init__(
        self, expected_sheet_config: Optional[Dict[str, Dict[str, Any]]] = None
    ):
        self.expected_sheet_config = expected_sheet_config or EXPECTED_SHEET_CONFIG
        self.expected_sheets = list(self.expected_sheet_config.keys())

    def read(
        self, file_path: Union[str, Path], sample_rows: int = 5
    ) -> ExcelStructureReport:
        path = Path(file_path).expanduser().resolve()
        extension = path.suffix.lower()
        file_size = path.stat().st_size if path.exists() else 0

        if not path.exists():
            return ExcelStructureReport(
                filename=path.name,
                file_path=str(path),
                extension=extension,
                file_size_bytes=file_size,
                can_open=False,
                open_error="File does not exist.",
                sheet_names=[],
                expected_sheets=self.expected_sheets,
                missing_expected_sheets=self.expected_sheets,
                unexpected_sheets=[],
                sheets={},
            )

        if extension not in {".xlsx", ".xlsm", ".xls"}:
            return ExcelStructureReport(
                filename=path.name,
                file_path=str(path),
                extension=extension,
                file_size_bytes=file_size,
                can_open=False,
                open_error=f"Unsupported file extension: {extension}",
                sheet_names=[],
                expected_sheets=self.expected_sheets,
                missing_expected_sheets=self.expected_sheets,
                unexpected_sheets=[],
                sheets={},
            )

        try:
            workbook = pd.ExcelFile(path)
        except Exception as exc:
            return ExcelStructureReport(
                filename=path.name,
                file_path=str(path),
                extension=extension,
                file_size_bytes=file_size,
                can_open=False,
                open_error=f"Could not open workbook: {exc}",
                sheet_names=[],
                expected_sheets=self.expected_sheets,
                missing_expected_sheets=self.expected_sheets,
                unexpected_sheets=[],
                sheets={},
            )

        sheet_names = workbook.sheet_names
        missing_sheets = [
            sheet for sheet in self.expected_sheets if sheet not in sheet_names
        ]
        unexpected_sheets = [
            sheet for sheet in sheet_names if sheet not in self.expected_sheets
        ]

        sheet_reports: Dict[str, SheetStructure] = {}
        for sheet_name in self.expected_sheets:
            config = self.expected_sheet_config[sheet_name]
            if sheet_name not in sheet_names:
                sheet_reports[sheet_name] = SheetStructure(
                    sheet_name=sheet_name,
                    exists=False,
                    parser_header_row=config.get("header"),
                    parser_usecols=config.get("usecols"),
                    read_error="Expected sheet is missing.",
                )
                continue

            sheet_reports[sheet_name] = self._read_sheet(
                workbook=workbook,
                sheet_name=sheet_name,
                config=config,
                sample_rows=sample_rows,
            )

        return ExcelStructureReport(
            filename=path.name,
            file_path=str(path),
            extension=extension,
            file_size_bytes=file_size,
            can_open=True,
            open_error=None,
            sheet_names=sheet_names,
            expected_sheets=self.expected_sheets,
            missing_expected_sheets=missing_sheets,
            unexpected_sheets=unexpected_sheets,
            sheets=sheet_reports,
        )

    def _read_sheet(
        self,
        workbook: pd.ExcelFile,
        sheet_name: str,
        config: Dict[str, Any],
        sample_rows: int,
    ) -> SheetStructure:
        try:
            df = workbook.parse(sheet_name=sheet_name, dtype=object, **config)
            columns = [str(column).strip() for column in df.columns]
            normalized_columns = [normalize_text(column) for column in columns]
            row_count_raw = int(len(df))

            lower_column_lookup = {normalize_text(column): column for column in columns}
            project_column = lower_column_lookup.get("proyecto")
            owner_column = lower_column_lookup.get("propietario")
            responsible_column = lower_column_lookup.get("responsable")

            row_count_with_project = 0
            row_count_parse_candidates = 0
            if project_column in df.columns:
                with_project = df[df[project_column].notna()]
                row_count_with_project = int(len(with_project))
                candidate_df = with_project
                if owner_column in df.columns:
                    candidate_df = candidate_df[candidate_df[owner_column].notna()]
                elif responsible_column in df.columns:
                    candidate_df = candidate_df[
                        candidate_df[responsible_column].notna()
                    ]
                row_count_parse_candidates = int(len(candidate_df))
            else:
                candidate_df = df.head(0)

            criteria = SHEET_FIELD_CRITERIA.get(sheet_name, {})
            matched_fields = {
                field_name: find_candidate(columns, field_criteria)
                for field_name, field_criteria in criteria.items()
            }

            critical_fields = ["name", "project_entity"]
            missing_required_fields = [
                field_name
                for field_name in critical_fields
                if field_name in matched_fields and matched_fields[field_name] is None
            ]

            # Include non-critical missing fields as warnings for AI context.
            for field_name, matched_column in matched_fields.items():
                if matched_column is None and field_name not in missing_required_fields:
                    missing_required_fields.append(field_name)

            sample_df = candidate_df.head(sample_rows)
            sample_payload: List[Dict[str, Any]] = []
            for _, row in sample_df.iterrows():
                row_payload = {
                    str(column): make_json_safe(row[column]) for column in df.columns
                }
                sample_payload.append(row_payload)

            return SheetStructure(
                sheet_name=sheet_name,
                exists=True,
                parser_header_row=config.get("header"),
                parser_usecols=config.get("usecols"),
                columns=columns,
                normalized_columns=normalized_columns,
                row_count_raw=row_count_raw,
                row_count_with_project=row_count_with_project,
                row_count_parse_candidates=row_count_parse_candidates,
                missing_required_fields=missing_required_fields,
                matched_fields=matched_fields,
                sample_rows=sample_payload,
                read_error=None,
            )
        except Exception as exc:
            return SheetStructure(
                sheet_name=sheet_name,
                exists=True,
                parser_header_row=config.get("header"),
                parser_usecols=config.get("usecols"),
                read_error=f"Could not read sheet with parser settings: {exc}",
            )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Inspect CNE Excel workbook structure."
    )
    parser.add_argument("file", help="Path to the Excel workbook to inspect.")
    parser.add_argument("--sample-rows", type=int, default=5)
    args = parser.parse_args()

    reader = CNEExcelStructureReader()
    report = reader.read(args.file, sample_rows=args.sample_rows)
    print(report.to_json(indent=2))
