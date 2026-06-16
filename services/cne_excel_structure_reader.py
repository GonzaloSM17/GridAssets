# services/cne_excel_structure_reader.py

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pandas as pd


class CNEExcelStructureReader:
    """Read workbook structure needed to validate CNE parser readiness."""

    EXPECTED_SHEETS = [
        "P.Generación",
        "PMGD",
        "BESS",
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

    SHEET_CONFIG = {
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

    REQUIRED_FIELDS = {
        "generation": {
            "sheets": ["P.Generación"],
            "fields": {
                "name": ["proyecto"],
                "project_entity": ["propietario"],
                "technology": ["tecnología", "tecnologia"],
                "cod": ["fecha"],
                "resolution": ["resolución", "resolucion"],
                "power_capacity": ["potencia"],
            },
        },
        "der": {
            "sheets": ["PMGD"],
            "fields": {
                "name": ["proyecto"],
                "project_entity": ["propietario"],
                "technology": ["tecnología", "tecnologia"],
                "cod": ["fecha"],
                "resolution": ["resolución", "resolucion"],
                "power_capacity": ["potencia"],
            },
        },
        "bess": {
            "sheets": ["BESS"],
            "fields": {
                "name": ["proyecto"],
                "project_entity": ["propietario"],
                "technology": ["tecnología", "tecnologia"],
                "cod": ["fecha"],
                "resolution": ["resolución", "resolucion"],
                "power_capacity": ["potencia"],
                "storage_capacity": ["almacenamiento"],
            },
        },
        "transmission": {
            "sheets": [
                "ON_STxN",
                "OA_STxN",
                "ON_STxZ",
                "OA_STxZ",
                "ON_D418",
                "OA_D418",
                "OEO_D418",
                "OPyM_ST",
                "Art.102",
            ],
            "fields": {
                "name": ["proyecto"],
                "project_entity": ["responsable"],
                "cod": ["fecha"],
            },
        },
    }

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)

    def read(self) -> Dict[str, Any]:
        excel = pd.ExcelFile(self.file_path)
        sheet_names = excel.sheet_names

        missing_expected_sheets = [
            sheet for sheet in self.EXPECTED_SHEETS if sheet not in sheet_names
        ]

        unexpected_sheets = [
            sheet for sheet in sheet_names if sheet not in self.EXPECTED_SHEETS
        ]

        columns_by_sheet = {}
        row_counts_by_sheet = {}
        sample_rows_by_sheet = {}
        recognized_fields_by_sheet = {}
        missing_fields_by_sheet = {}

        for sheet in self.EXPECTED_SHEETS:
            if sheet not in sheet_names:
                continue

            config = self.SHEET_CONFIG.get(sheet, {"header": 2})

            try:
                df = excel.parse(sheet, dtype=str, **config)
                df.columns = [str(col).strip() for col in df.columns]

                columns = list(df.columns)
                columns_by_sheet[sheet] = columns

                project_col = self._find_column(columns, ["proyecto"])
                if project_col:
                    candidate_df = df[df[project_col].notna()]
                else:
                    candidate_df = df

                row_counts_by_sheet[sheet] = int(len(candidate_df))

                sample_rows_by_sheet[sheet] = (
                    candidate_df.head(3)
                    .fillna("")
                    .astype(str)
                    .to_dict(orient="records")
                )

                recognized, missing = self._recognize_sheet_fields(sheet, columns)
                recognized_fields_by_sheet[sheet] = recognized
                missing_fields_by_sheet[sheet] = missing

            except Exception as error:
                columns_by_sheet[sheet] = []
                row_counts_by_sheet[sheet] = 0
                sample_rows_by_sheet[sheet] = []
                recognized_fields_by_sheet[sheet] = {}
                missing_fields_by_sheet[sheet] = [f"read_error: {error}"]

        return {
            "filename": self.file_path.name,
            "extension": self.file_path.suffix.lower(),
            "sheet_names": sheet_names,
            "expected_sheets": self.EXPECTED_SHEETS,
            "missing_expected_sheets": missing_expected_sheets,
            "unexpected_sheets": unexpected_sheets,
            "columns_by_sheet": columns_by_sheet,
            "row_counts_by_sheet": row_counts_by_sheet,
            "recognized_fields_by_sheet": recognized_fields_by_sheet,
            "missing_fields_by_sheet": missing_fields_by_sheet,
            "sample_rows_by_sheet": sample_rows_by_sheet,
        }

    def _recognize_sheet_fields(self, sheet: str, columns: list[str]):
        required_fields = {}

        for group in self.REQUIRED_FIELDS.values():
            if sheet in group["sheets"]:
                required_fields.update(group["fields"])

        recognized = {}
        missing = []

        for field_name, keywords in required_fields.items():
            column = self._find_column(columns, keywords)
            if column:
                recognized[field_name] = column
            else:
                missing.append(field_name)

        return recognized, missing

    @staticmethod
    def _find_column(columns: list[str], keywords: list[str]) -> str | None:
        normalized_columns = {
            column: CNEExcelStructureReader._normalize_text(column)
            for column in columns
        }

        normalized_keywords = [
            CNEExcelStructureReader._normalize_text(keyword) for keyword in keywords
        ]

        for original, normalized in normalized_columns.items():
            if any(keyword in normalized for keyword in normalized_keywords):
                return original

        return None

    @staticmethod
    def _normalize_text(value: str) -> str:
        value = str(value).lower().strip()
        replacements = {
            "á": "a",
            "é": "e",
            "í": "i",
            "ó": "o",
            "ú": "u",
            "ü": "u",
            "ñ": "n",
        }
        for source, target in replacements.items():
            value = value.replace(source, target)
        return value
