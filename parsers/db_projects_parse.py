# db_projects_parse.py
"""CNE Excel project parser.

This module reads the monthly CNE construction declaration workbook and converts
its project rows into dataclasses used by the database population layer.

Responsibilities:
- Accept an explicit file path or a legacy filename.
- Read CNE Excel sheets.
- Normalize project names.
- Parse dates safely, including Excel serial dates.
- Parse numeric capacities as float values.
- Preserve source technology text so TechnologyResolver can normalize it later.
- Produce a lightweight parse report for UI previews and ingestion validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, Union
import math
import unicodedata

import pandas as pd

from services.numeric_value_parser import NumericValueParser
from services.project_name_normalizer import ProjectNameNormalizer

# ==================== DATACLASSES ====================


@dataclass
class UnderConstructionProject:
    """Base CNE project dataclass."""

    name: Optional[str] = None
    project_entity: Optional[str] = None
    cod: Optional[date] = None

    # PGP portal data
    pgp_NUP: Optional[int] = None
    pgp_url: Optional[str] = None
    pgp_commissioning_planned: Optional[date] = None
    pgp_commissioning_actual: Optional[date] = None
    pgp_cod_planned: Optional[date] = None
    pgp_cod_actual: Optional[date] = None


@dataclass
class TransmissionProject(UnderConstructionProject):
    """Transmission project parsed from CNE workbook."""

    type: Optional[str] = None
    description: Optional[str] = None

    act: Optional[str] = None
    act_award: Optional[str] = None
    resolution_exempt: Optional[str] = None

    voltage_level: Optional[str] = None
    total_capacity: Optional[float] = None

    tracking_construction_start: Optional[date] = None
    tracking_cod_planned: Optional[date] = None


@dataclass
class GridScaleProject(UnderConstructionProject):
    """Base dataclass for generation, DER and BESS-like projects."""

    power_capacity: Optional[float] = None
    resolution: Optional[str] = None
    location: Optional[str] = None
    bay: Optional[str] = None


@dataclass
class GeneratorProject(GridScaleProject):
    """Generation project parsed from CNE workbook."""

    total_capacity: Optional[float] = None
    technology: Optional[str] = None


@dataclass
class DERProject(GridScaleProject):
    """DER / PMGD project parsed from CNE workbook."""

    total_capacity: Optional[float] = None
    technology: Optional[str] = None


@dataclass
class BESSProject(GridScaleProject):
    """BESS project parsed from CNE workbook."""

    technology: Optional[str] = None
    storage_capacity: Optional[float] = None


@dataclass
class ParseReport:
    """Lightweight parsing report for UI previews and ingestion validation."""

    input_file: Optional[str] = None
    available_sheets: List[str] = field(default_factory=list)
    missing_sheets: List[str] = field(default_factory=list)
    parsed_rows_by_sheet: Dict[str, int] = field(default_factory=dict)
    dropped_rows_by_sheet: Dict[str, int] = field(default_factory=dict)
    missing_columns_by_sheet: Dict[str, List[str]] = field(default_factory=dict)
    invalid_dates: List[Dict[str, Any]] = field(default_factory=list)
    invalid_numeric_values: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        """Return report as a JSON-friendly dictionary."""
        return {
            "input_file": self.input_file,
            "available_sheets": self.available_sheets,
            "missing_sheets": self.missing_sheets,
            "parsed_rows_by_sheet": self.parsed_rows_by_sheet,
            "dropped_rows_by_sheet": self.dropped_rows_by_sheet,
            "missing_columns_by_sheet": self.missing_columns_by_sheet,
            "invalid_dates": self.invalid_dates,
            "invalid_numeric_values": self.invalid_numeric_values,
        }


# ==================== PARSER ====================


class ProjectParser:
    """Parse CNE Excel workbooks into project dataclasses."""

    SHEET_CONFIG: Dict[str, Dict[str, Any]] = {
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

    EXPECTED_SHEETS = list(SHEET_CONFIG.keys())

    NUMERIC_ATTRIBUTES = {
        "power_capacity",
        "storage_capacity",
        "total_capacity",
    }

    def __init__(
        self,
        filename: Optional[str] = None,
        file_path: Optional[Union[str, Path]] = None,
        debug: bool = False,
    ):
        """Create parser from filename or explicit file path."""
        if file_path is None and filename is None:
            raise ValueError("Either filename or file_path must be provided.")

        self.debug = debug
        self.input_name = filename or Path(file_path).name
        self.file_path = self._resolve_file_path(file_path or filename)
        self.report = ParseReport(input_file=str(self.file_path))

        self._load_file()

        self.transmission_projects = self._storage_transmission_projects()
        self.generation_projects = self._storage_generation_projects()
        self.der_projects = self._storage_der_projects()
        self.bess_projects = self._storage_bess_projects()

    # ------------------------------------------------------------------
    # File handling
    # ------------------------------------------------------------------

    def _resolve_file_path(self, raw_path: Union[str, Path]) -> Path:
        """Resolve workbook path from absolute, relative or legacy temp location."""
        candidate = Path(raw_path).expanduser()

        if candidate.is_absolute() and candidate.exists():
            return candidate

        if candidate.exists():
            return candidate.resolve()

        base = Path(__file__).resolve().parent
        fallback_paths = [
            base / candidate,
            base / "temp" / candidate.name,
            Path.cwd() / candidate,
            Path.cwd() / "temp" / candidate.name,
        ]

        for path in fallback_paths:
            if path.exists():
                return path.resolve()

        raise FileNotFoundError(f"CNE Excel file not found: {raw_path}")

    def _load_file(self) -> None:
        """Load workbook metadata."""
        self.file = pd.ExcelFile(self.file_path)
        self.sheets = self.file.sheet_names

        self.report.available_sheets = list(self.sheets)
        self.report.missing_sheets = [
            sheet for sheet in self.EXPECTED_SHEETS if sheet not in self.sheets
        ]

    def _open_data(self, sheet_name: str) -> pd.DataFrame:
        """Open a configured CNE sheet and return clean candidate project rows."""
        if sheet_name not in self.sheets:
            return pd.DataFrame()

        raw_df = self.file.parse(
            sheet_name,
            dtype=object,
            **self.SHEET_CONFIG[sheet_name],
        )
        original_rows = len(raw_df)

        raw_df.columns = [str(column).strip().lower() for column in raw_df.columns]

        if "proyecto" not in raw_df.columns:
            self.report.missing_columns_by_sheet[sheet_name] = ["proyecto"]
            return pd.DataFrame()

        df = raw_df.dropna(subset=["proyecto"]).copy()

        if "propietario" in df.columns:
            df = df.dropna(subset=["propietario"]).copy()

        self.report.dropped_rows_by_sheet[sheet_name] = original_rows - len(df)

        df["proyecto"] = df["proyecto"].apply(self._clean_project_name)

        return df.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Public report helpers
    # ------------------------------------------------------------------

    def get_report(self) -> Dict[str, Any]:
        """Return parser report as dictionary."""
        return self.report.as_dict()

    def get_project_counts(self) -> Dict[str, int]:
        """Return parsed project counts by project family."""
        return {
            "transmission": len(self.transmission_projects),
            "generation": len(self.generation_projects),
            "der": len(self.der_projects),
            "bess": len(self.bess_projects),
            "total": (
                len(self.transmission_projects)
                + len(self.generation_projects)
                + len(self.der_projects)
                + len(self.bess_projects)
            ),
        }

    # ------------------------------------------------------------------
    # Generic utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_string(value: Any) -> str:
        """Normalize text for accent-insensitive matching."""
        value = "" if value is None else str(value)
        value = unicodedata.normalize("NFKD", value.strip().lower())
        return "".join(char for char in value if not unicodedata.combining(char))

    @staticmethod
    def _clean_project_name(value: Any) -> str:
        """Clean and normalize project name text."""
        normalized = ProjectNameNormalizer.normalize(value)
        return normalized or ""

    def _find_candidate(self, columns: List[str], criteria: List[str]) -> Optional[str]:
        """Find first column containing all normalized criteria terms."""
        for column in columns:
            column_normalized = self._normalize_string(column)
            if all(criteria_item in column_normalized for criteria_item in criteria):
                return column

        return None

    def _get_val(
        self,
        row: pd.Series,
        col_map: Dict[str, Optional[str]],
        attr: str,
    ) -> Any:
        """Return raw row value for a mapped attribute."""
        column = col_map.get(attr)

        if not column or column not in row:
            return None

        value = row[column]

        if pd.isna(value):
            return None

        return value

    @staticmethod
    def _clean_text(value: Any) -> Optional[str]:
        """Return stripped text or None."""
        if value is None or pd.isna(value):
            return None

        text = str(value).strip()

        if not text or text.lower() in {"nan", "none", "nat"}:
            return None

        return text

    @staticmethod
    def _clean_voltage(value: Any) -> Optional[str]:
        """Clean voltage value."""
        if value is None or pd.isna(value):
            return None

        text = str(value).lower()
        text = text.replace("kv", "")
        text = text.replace(" ", "")

        return text.strip() or None

    # ------------------------------------------------------------------
    # Date parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _excel_serial_to_date(value: Union[int, float]) -> Optional[date]:
        """Convert Excel serial date numbers to Python date objects."""
        if value is None or not math.isfinite(float(value)):
            return None

        if value < 1:
            return None

        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()

    @classmethod
    def _parse_date(cls, value: Any) -> Optional[date]:
        """Parse a CNE date value and return a date object."""
        if value is None or pd.isna(value):
            return None

        if isinstance(value, pd.Timestamp):
            return value.date()

        if isinstance(value, datetime):
            return value.date()

        if isinstance(value, date):
            return value

        if isinstance(value, (int, float)):
            numeric_value = float(value)
            if 20000 <= numeric_value <= 80000:
                return cls._excel_serial_to_date(numeric_value)

        if isinstance(value, str):
            text = value.strip()

            if not text or text.lower() in {"nan", "none", "nat"}:
                return None

            normalized_number = text.replace(",", ".")

            try:
                numeric_value = float(normalized_number)
                if 20000 <= numeric_value <= 80000:
                    return cls._excel_serial_to_date(numeric_value)
            except ValueError:
                pass

            parsed_month_year = cls._parse_month_year_text(text)
            if parsed_month_year is not None:
                return parsed_month_year

            explicit_formats = [
                "%Y-%m-%d",
                "%Y/%m/%d",
                "%d/%m/%Y",
                "%d-%m-%Y",
                "%d.%m.%Y",
            ]

            for date_format in explicit_formats:
                try:
                    return datetime.strptime(text, date_format).date()
                except ValueError:
                    continue

            parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
            if pd.notna(parsed):
                return parsed.date()

        return None

    @staticmethod
    def _parse_month_year_text(value: str) -> Optional[date]:
        """Parse compact Spanish month-year values such as mar-26."""
        text = ProjectParser._normalize_string(value)
        text = text.replace(".", "").replace("/", "-").replace(" ", "-")

        parts = [part for part in text.split("-") if part]

        if len(parts) != 2:
            return None

        month_text, year_text = parts

        spanish_months = {
            "ene": 1,
            "enero": 1,
            "feb": 2,
            "febrero": 2,
            "mar": 3,
            "marzo": 3,
            "abr": 4,
            "abril": 4,
            "may": 5,
            "mayo": 5,
            "jun": 6,
            "junio": 6,
            "jul": 7,
            "julio": 7,
            "ago": 8,
            "agosto": 8,
            "sep": 9,
            "sept": 9,
            "septiembre": 9,
            "oct": 10,
            "octubre": 10,
            "nov": 11,
            "noviembre": 11,
            "dic": 12,
            "diciembre": 12,
        }

        month = spanish_months.get(month_text)

        if month is None or not year_text.isdigit():
            return None

        year = int(year_text)

        if year < 100:
            year += 2000 if year < 70 else 1900

        return date(year, month, 1)

    # ------------------------------------------------------------------
    # Report recorders
    # ------------------------------------------------------------------

    def _record_invalid_date(
        self,
        sheet: str,
        row_index: int,
        attr: str,
        column: Optional[str],
        raw_value: Any,
    ) -> None:
        """Record date parsing issue."""
        self.report.invalid_dates.append(
            {
                "sheet": sheet,
                "row_index": int(row_index),
                "attribute": attr,
                "column": column,
                "raw_value": str(raw_value),
            }
        )

    def _record_invalid_numeric_value(
        self,
        sheet: str,
        row_index: int,
        attr: str,
        column: Optional[str],
        raw_value: Any,
    ) -> None:
        """Record numeric parsing issue."""
        self.report.invalid_numeric_values.append(
            {
                "sheet": sheet,
                "row_index": int(row_index),
                "attribute": attr,
                "column": column,
                "raw_value": str(raw_value),
            }
        )

    # ------------------------------------------------------------------
    # Generic parser
    # ------------------------------------------------------------------

    def _parse_projects(
        self,
        sheets: List[str],
        model: Type[UnderConstructionProject],
        raw_criteria: Dict[str, List[str]],
        extra_handler=None,
        debug: Optional[bool] = None,
    ) -> List[UnderConstructionProject]:
        """Parse configured sheets into project dataclass instances."""
        if debug is None:
            debug = self.debug

        clean_criteria = {
            attr: [self._normalize_string(criteria) for criteria in criteria_list]
            for attr, criteria_list in raw_criteria.items()
        }

        projects = []

        for sheet in sheets:
            df = self._open_data(sheet)

            if df.empty:
                continue

            col_map = {
                attr: self._find_candidate(df.columns.tolist(), criteria)
                for attr, criteria in clean_criteria.items()
            }

            missing_columns = [
                attr for attr, column in col_map.items() if column is None
            ]

            if missing_columns:
                self.report.missing_columns_by_sheet[sheet] = missing_columns

            if debug:
                print(f"\nSheet: {sheet}")
                print("Columns found:")

                for attr, column in col_map.items():
                    print(f"  {attr:20} -> {column}")

            parsed_count = 0

            for row_index, row in df.iterrows():
                project = model()

                for attr in col_map:
                    raw_value = self._get_val(row, col_map, attr)

                    if attr == "cod":
                        parsed_date = self._parse_date(raw_value)
                        setattr(project, attr, parsed_date)

                        if raw_value is not None and parsed_date is None:
                            self._record_invalid_date(
                                sheet=sheet,
                                row_index=row_index,
                                attr=attr,
                                column=col_map.get(attr),
                                raw_value=raw_value,
                            )

                        if debug and raw_value is not None:
                            print(
                                f"Parsed {attr}: {raw_value!r} "
                                f"({type(raw_value).__name__}) -> {parsed_date}"
                            )

                        continue

                    if attr in self.NUMERIC_ATTRIBUTES:
                        parsed_number = NumericValueParser.parse_float(raw_value)
                        setattr(project, attr, parsed_number)

                        if raw_value is not None and parsed_number is None:
                            self._record_invalid_numeric_value(
                                sheet=sheet,
                                row_index=row_index,
                                attr=attr,
                                column=col_map.get(attr),
                                raw_value=raw_value,
                            )

                        if debug and raw_value is not None:
                            print(
                                f"Parsed {attr}: {raw_value!r} "
                                f"({type(raw_value).__name__}) -> {parsed_number}"
                            )

                        continue

                    cleaned_text = self._clean_text(raw_value)
                    setattr(project, attr, cleaned_text)

                if extra_handler:
                    extra_handler(project, row, col_map, sheet)

                projects.append(project)
                parsed_count += 1

            self.report.parsed_rows_by_sheet[sheet] = parsed_count

        return projects

    # ------------------------------------------------------------------
    # Specific project parsers
    # ------------------------------------------------------------------

    def _storage_transmission_projects(self) -> List[TransmissionProject]:
        """Parse all transmission sheets."""

        def handler(project, row, col_map, sheet):
            project.type = sheet
            project.voltage_level = self._clean_voltage(
                self._get_val(row, col_map, "voltage_level")
            )

        return self._parse_projects(
            sheets=[
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
            model=TransmissionProject,
            raw_criteria={
                "name": ["proyecto"],
                "project_entity": ["responsable"],
                "act": ["decreto"],
                "act_award": ["adjudicacion"],
                "resolution_exempt": ["resolución", "exenta"],
                "cod": ["fecha"],
                "description": ["descripción"],
                "total_capacity": ["potencia"],
                "voltage_level": ["tensión"],
            },
            extra_handler=handler,
        )

    def _storage_generation_projects(self) -> List[GeneratorProject]:
        """Parse generation sheet."""
        return self._parse_projects(
            sheets=["P.Generación"],
            model=GeneratorProject,
            raw_criteria={
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
        )

    def _storage_der_projects(self) -> List[DERProject]:
        """Parse PMGD / DER sheet."""
        return self._parse_projects(
            sheets=["PMGD"],
            model=DERProject,
            raw_criteria={
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
        )

    def _storage_bess_projects(self) -> List[BESSProject]:
        """Parse BESS sheet."""
        return self._parse_projects(
            sheets=["BESS"],
            model=BESSProject,
            raw_criteria={
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
        )


if __name__ == "__main__":
    parser = ProjectParser(filename="Tablas-Declaracion-Construccion-Enero-2026.xlsx")

    print("Project counts:")
    print(parser.get_project_counts())

    print("Parse report:")
    print(parser.get_report())
