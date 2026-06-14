# db_projects_parse.py
"""
CNE Excel project parser.

This module reads the monthly CNE construction declaration workbook and converts
its project rows into dataclasses used by the database population layer.

Main improvements over the first version:
- Accepts an explicit file path, not only a filename inside ./temp.
- Keeps backwards compatibility with ProjectParser(filename="...").
- Parses CNE/Excel dates safely, including Excel serial dates.
- Tracks a lightweight parsing report for UI previews and ingestion validation.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, Union
import math
import unicodedata

import pandas as pd


@dataclass
class UnderConstructionProject:
    name: str = None
    project_entity: str = None
    cod: date = None

    # PGP portal data
    pgp_NUP: Optional[int] = None
    pgp_url: Optional[str] = None
    pgp_commissioning_planned: Optional[date] = None
    pgp_commissioning_actual: Optional[date] = None
    pgp_cod_planned: Optional[date] = None
    pgp_cod_actual: Optional[date] = None


@dataclass
class TransmissionProject(UnderConstructionProject):
    type: str = None
    description: str = None

    act: str = None
    act_award: str = None
    resolution_exempt: str = None

    voltage_level: str = None
    total_capacity: str = None

    # Construction tracking data
    tracking_construction_start: date = None
    tracking_cod_planned: date = None


@dataclass
class GridScaleProject(UnderConstructionProject):
    power_capacity: float = None
    resolution: str = None
    location: str = None
    bay: str = None


@dataclass
class GeneratorProject(GridScaleProject):
    total_capacity: str = None
    technology: str = None


@dataclass
class DERProject(GridScaleProject):
    total_capacity: str = None
    technology: str = None


@dataclass
class BESSProject(GridScaleProject):
    technology: str = None
    storage_capacity: str = None


@dataclass
class ParseReport:
    input_file: str = None
    available_sheets: List[str] = field(default_factory=list)
    missing_sheets: List[str] = field(default_factory=list)
    parsed_rows_by_sheet: Dict[str, int] = field(default_factory=dict)
    dropped_rows_by_sheet: Dict[str, int] = field(default_factory=dict)
    missing_columns_by_sheet: Dict[str, List[str]] = field(default_factory=dict)
    invalid_dates: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "input_file": self.input_file,
            "available_sheets": self.available_sheets,
            "missing_sheets": self.missing_sheets,
            "parsed_rows_by_sheet": self.parsed_rows_by_sheet,
            "dropped_rows_by_sheet": self.dropped_rows_by_sheet,
            "missing_columns_by_sheet": self.missing_columns_by_sheet,
            "invalid_dates": self.invalid_dates,
        }


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

    def __init__(
        self,
        filename: Optional[str] = None,
        file_path: Optional[Union[str, Path]] = None,
        debug: bool = False,
    ):
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

    # ---------------- File handling ----------------

    def _resolve_file_path(self, raw_path: Union[str, Path]) -> Path:
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
        self.file = pd.ExcelFile(self.file_path)
        self.sheets = self.file.sheet_names
        self.report.available_sheets = list(self.sheets)
        self.report.missing_sheets = [
            sheet for sheet in self.EXPECTED_SHEETS if sheet not in self.sheets
        ]

    def _open_data(self, sheet_name: str) -> pd.DataFrame:
        if sheet_name not in self.sheets:
            return pd.DataFrame()

        raw_df = self.file.parse(sheet_name, dtype=object, **self.SHEET_CONFIG[sheet_name])
        original_rows = len(raw_df)

        raw_df.columns = [str(col).strip().lower() for col in raw_df.columns]
        df = raw_df.dropna(subset=["proyecto"]).copy()

        if "propietario" in df.columns:
            df = df.dropna(subset=["propietario"]).copy()

        self.report.dropped_rows_by_sheet[sheet_name] = original_rows - len(df)

        df["proyecto"] = (
            df["proyecto"]
            .astype(str)
            .str.replace(r"[\r\n]+", " ", regex=True)
            .str.replace(r"\bSE\b", "S/E", regex=True)
            .str.replace(r"\s*-\s*", " – ", regex=True)
            .str.replace(r"\s+", " ", regex=True)
            .str.strip()
        )

        for column in [
            "capacidad instalada [mw]",
            "potencia",
            "potencia neta [mw]",
            "capacidad de almacenamiento [mwh]",
        ]:
            if column in df.columns:
                df[column] = df[column].apply(self._clean_decimal_text)

        return df.reset_index(drop=True)

    # ---------------- Utils ----------------

    @staticmethod
    def _normalize_string(value: Any) -> str:
        value = "" if value is None else str(value)
        value = unicodedata.normalize("NFKD", value.strip().lower())
        return "".join(c for c in value if not unicodedata.combining(c))

    def _find_candidate(self, columns: List[str], criteria: List[str]) -> Optional[str]:
        for col in columns:
            col_norm = self._normalize_string(col)
            if all(c in col_norm for c in criteria):
                return col
        return None

    @staticmethod
    def _clean_decimal_text(value: Any) -> Optional[str]:
        if pd.isna(value):
            return None
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none"}:
            return None
        return text.replace(",", ".")

    def _get_val(self, row, col_map, attr):
        col = col_map.get(attr)
        if col and col in row and pd.notna(row[col]):
            return row[col]
        return None

    @staticmethod
    def _excel_serial_to_date(value: Union[int, float]) -> Optional[date]:
        """Convert Excel serial date numbers to Python date objects.

        Excel's 1900 date system includes a historical leap-year bug. The common
        Python conversion uses 1899-12-30 as the origin, which matches pandas and
        Excel for practical modern dates.
        """
        if value is None or not math.isfinite(float(value)):
            return None
        if value < 1:
            return None
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()

    @classmethod
    def _parse_date(cls, value: Any) -> Optional[date]:
        """Parse a CNE date value and return a date object.

        Handles:
        - datetime/date/Timestamp values
        - Excel serial dates such as 44348
        - ISO dates such as 2021-06-01
        - Chilean day-first dates such as 01-06-2021 or 01/06/2021
        """
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
            for fmt in explicit_formats:
                try:
                    return datetime.strptime(text, fmt).date()
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


    @staticmethod
    def _safe_float(value) -> Optional[float]:
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clean_voltage(value) -> str:
        if value is None or pd.isna(value):
            return ""
        text = str(value).lower()
        text = text.replace("kv", "")
        text = text.replace(" ", "")
        return text.strip()

    # ---------------- Report helpers ----------------

    def get_report(self) -> Dict[str, Any]:
        return self.report.as_dict()

    def get_project_counts(self) -> Dict[str, int]:
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

    def _record_invalid_date(
        self, sheet: str, row_index: int, attr: str, column: str, raw_value: Any
    ) -> None:
        self.report.invalid_dates.append(
            {
                "sheet": sheet,
                "row_index": int(row_index),
                "attribute": attr,
                "column": column,
                "raw_value": str(raw_value),
            }
        )

    # ---------------- Generic parser ----------------

    def _parse_projects(
        self,
        sheets: List[str],
        model: Type[UnderConstructionProject],
        raw_criteria: Dict[str, List[str]],
        extra_handler=None,
        debug: Optional[bool] = None,
    ) -> List[UnderConstructionProject]:
        if debug is None:
            debug = self.debug

        clean_criteria = {
            k: [self._normalize_string(c) for c in v] for k, v in raw_criteria.items()
        }

        projects = []

        for sheet in sheets:
            df = self._open_data(sheet)
            if df.empty:
                continue

            col_map = {
                attr: self._find_candidate(df.columns.tolist(), crit)
                for attr, crit in clean_criteria.items()
            }

            missing_columns = [attr for attr, col in col_map.items() if col is None]
            if missing_columns:
                self.report.missing_columns_by_sheet[sheet] = missing_columns

            if debug:
                print(f"\nSheet: {sheet}")
                print("Columns found:")
                for attr, col in col_map.items():
                    print(f"  {attr:20} -> {col}")

            parsed_count = 0
            for row_index, row in df.iterrows():
                project = model()

                for attr in col_map:
                    val = self._get_val(row, col_map, attr)

                    if attr == "cod":
                        parsed_date = self._parse_date(val)
                        setattr(project, attr, parsed_date)

                        if val is not None and parsed_date is None:
                            self._record_invalid_date(
                                sheet=sheet,
                                row_index=row_index,
                                attr=attr,
                                column=col_map.get(attr),
                                raw_value=val,
                            )

                        if debug and val is not None:
                            print(
                                f"Parsed {attr}: {val!r} ({type(val).__name__}) -> {parsed_date}"
                            )
                    elif attr in (
                        "power_capacity",
                        "storage_capacity",
                        "total_capacity",
                    ):
                        setattr(project, attr, self._clean_decimal_text(val))
                    elif isinstance(val, str):
                        setattr(project, attr, val.strip())
                    elif val is not None:
                        setattr(project, attr, val)

                if extra_handler:
                    extra_handler(project, row, col_map, sheet)

                projects.append(project)
                parsed_count += 1

            self.report.parsed_rows_by_sheet[sheet] = parsed_count

        return projects

    # ---------------- Specific parser ----------------

    def _storage_transmission_projects(self) -> List[TransmissionProject]:
        def handler(p, row, col_map, sheet):
            p.type = sheet
            p.voltage_level = self._clean_voltage(
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
        )

    def _storage_generation_projects(self) -> List[GeneratorProject]:
        return self._parse_projects(
            ["P.Generación"],
            GeneratorProject,
            {
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
        return self._parse_projects(
            ["BESS"],
            BESSProject,
            {
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

    def _storage_der_projects(self) -> List[DERProject]:
        return self._parse_projects(
            ["PMGD"],
            DERProject,
            {
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


if __name__ == "__main__":
    parser = ProjectParser(filename="Tablas-Declaracion-Construccion-Enero-2026.xlsx")
    print("Project counts:")
    print(parser.get_project_counts())
    print("Parse report:")
    print(parser.get_report())
