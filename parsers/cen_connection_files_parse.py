"""Parser and normalizer for CEN connection spreadsheets.

This module reads CEN connection files and converts their heterogeneous sheets
into a single canonical DataFrame. It does not read from or write to the DB.

Supported profiles:
- Proyectos con Entrada en Operacion: real PES/EO dates.
- Proyectos Declarados en Construccion: estimated and actual PES/EO dates.

Canonical date mapping:
- PES -> Commissioning
- EO  -> COD
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import re
import unicodedata

import pandas as pd


EXCEL_EPOCH = datetime(1899, 12, 30)


IGNORED_SHEETS = set()

DESISTED_SHEET_ALIASES = {
    "proy con art 157 y desistidos",
    "proyectos con art 157 y desistidos",
    "proy con art 157 desistidos",
}


@dataclass(frozen=True)
class SheetProfile:
    """Configuration used to normalize one recognized sheet."""

    project_type: str
    source_sheet_role: str
    date_columns: Dict[str, Tuple[str, str]]
    optional_fields: Dict[str, Sequence[str]] = field(default_factory=dict)
    record_action: str = "date_enrichment"
    target_status: Optional[str] = None


@dataclass(frozen=True)
class FileProfile:
    """Configuration used to normalize one recognized file type."""

    key: str
    label: str
    source_system: str
    source_detail: str
    sheets: Dict[str, SheetProfile]


@dataclass
class ConnectionParseResult:
    """Container returned by CENConnectionFileParser.parse."""

    file_path: str
    profile_key: str
    profile_label: str
    records: pd.DataFrame
    sheet_summaries: pd.DataFrame
    warnings: List[str]

    @property
    def total_rows(self) -> int:
        return int(len(self.records))

    @property
    def rows_with_nup(self) -> int:
        if self.records.empty or "nup" not in self.records.columns:
            return 0
        return int(self.records["nup"].notna().sum())

    @property
    def rows_with_any_date(self) -> int:
        if self.records.empty:
            return 0
        date_cols = [
            "commissioning_actual",
            "commissioning_estimated",
            "cod_actual",
            "cod_estimated",
        ]
        existing = [col for col in date_cols if col in self.records.columns]
        if not existing:
            return 0
        return int(self.records[existing].notna().any(axis=1).sum())

    def summary_dict(self) -> Dict[str, Any]:
        return {
            "profile_key": self.profile_key,
            "profile_label": self.profile_label,
            "total_rows": self.total_rows,
            "rows_with_nup": self.rows_with_nup,
            "rows_with_any_date": self.rows_with_any_date,
            "warnings": len(self.warnings),
        }


COMMON_OPTIONAL_FIELDS: Dict[str, Sequence[str]] = {
    "nup": ("NUP", "N.U.P", "Numero NUP", "N° NUP"),
    "process": ("Proceso",),
    "company": ("Empresa", "Titular", "Propietario", "Empresa Propietaria"),
    "project_name": ("Nombre Proyecto", "Nombre del Proyecto", "Proyecto"),
    "transmission_system": ("Sistema de Transmisión", "Sistema de Transmision"),
    "voltage_kv": ("Nivel de tensión [kV]", "Nivel de tension [kV]", "Tensión [kV]", "Tension [kV]"),
    "generation_type": ("Tipo de Generación", "Tipo de Generacion"),
    "technology": ("Tipo Tecnología", "Tipo Tecnologia", "Tecnología", "Tecnologia", "Tipo de Tecnología"),
    "power_mw": ("Potencia Neta Total [MW]", "Potencia [MW]", "Potencia MW", "Potencia Neta [MW]"),
    "connection_point": ("Punto de Conexión", "Punto de Conexion"),
    "region": ("Región", "Region"),
    "commune": ("Comuna",),
    "pes_actual_raw": ("Fecha Real de PES", "Fecha real de PES", "Fecha Real PES"),
    "pes_estimated_raw": ("Fechas Estimada de PES", "Fecha Estimada de PES", "Fecha estimada de PES", "Fecha Estimada PES"),
    "eo_actual_raw": ("Fecha Real de EO", "Fecha real de EO", "Fecha Real EO"),
    "eo_estimated_raw": ("Fechas Estimada de EO", "Fecha Estimada de EO", "Fecha estimada de EO", "Fecha Estimada EO"),
    "eo_letter_date": ("Fecha Emisión carta EO", "Fecha Emision carta EO", "Fecha emisión carta EO"),
    "eo_letter_number": ("N° Carta EO", "N Carta EO", "Numero Carta EO", "Nro Carta EO"),
    "observation": ("Observación", "Observacion", "Comentario", "Comentarios", "Estado"),
}


def _entry_sheet(project_type: str, source_sheet_role: str) -> SheetProfile:
    return SheetProfile(
        project_type=project_type,
        source_sheet_role=source_sheet_role,
        date_columns={
            "Fecha Real de PES": ("commissioning", "actual"),
            "Fecha Real de EO": ("cod", "actual"),
        },
        optional_fields=COMMON_OPTIONAL_FIELDS,
    )


def _construction_sheet(project_type: str, source_sheet_role: str) -> SheetProfile:
    return SheetProfile(
        project_type=project_type,
        source_sheet_role=source_sheet_role,
        date_columns={
            "Fecha Real de PES": ("commissioning", "actual"),
            "Fechas Estimada de PES": ("commissioning", "estimated"),
            "Fecha Real de EO": ("cod", "actual"),
            "Fechas Estimada de EO": ("cod", "estimated"),
        },
        optional_fields=COMMON_OPTIONAL_FIELDS,
    )


def _desisted_sheet(project_type: str, source_sheet_role: str) -> SheetProfile:
    return SheetProfile(
        project_type=project_type,
        source_sheet_role=source_sheet_role,
        date_columns={},
        optional_fields=COMMON_OPTIONAL_FIELDS,
        record_action="status_cancelled",
        target_status="Cancelled",
    )


FILE_PROFILES: Dict[str, FileProfile] = {
    "entry_operation": FileProfile(
        key="entry_operation",
        label="Proyectos con Entrada en Operacion",
        source_system="CEN - Conexiones",
        source_detail="Entrada en Operacion",
        sheets={
            "Tx_En Operación": _entry_sheet("transmission", "tx_entry_operation"),
            "Gx_En Operación": _entry_sheet("generation_or_bess", "gx_entry_operation"),
        },
    ),
    "declared_construction": FileProfile(
        key="declared_construction",
        label="Proyectos Declarados en Construccion",
        source_system="CEN - Conexiones",
        source_detail="Declarados en Construccion",
        sheets={
            "Tx_En Gestión": _construction_sheet("transmission", "tx_declared_construction"),
            "P. Gx En Gestión": _construction_sheet("generation_or_bess", "gx_declared_construction"),
            "Gx_BESS En Gestión": _construction_sheet("bess", "bess_declared_construction"),
            "PMGD_ En Gestión": _construction_sheet("pmgd", "pmgd_declared_construction"),
            "Proy. con art. 157 y desistidos": _desisted_sheet("unknown", "cancelled_or_art157"),
        },
    ),
}


CANONICAL_COLUMNS = [
    "source_system",
    "source_detail",
    "source_profile",
    "source_file",
    "source_sheet",
    "source_sheet_role",
    "row_number",
    "record_action",
    "target_status",
    "connection_project_type",
    "nup",
    "project_name",
    "normalized_project_name",
    "process",
    "company",
    "region",
    "commune",
    "transmission_system",
    "voltage_kv",
    "generation_type",
    "technology",
    "power_mw",
    "connection_point",
    "commissioning_actual",
    "commissioning_estimated",
    "cod_actual",
    "cod_estimated",
    "eo_letter_date",
    "eo_letter_number",
    "observation",
]


class CENConnectionFileParser:
    """Read and normalize CEN connection spreadsheets."""

    def __init__(self, profiles: Optional[Dict[str, FileProfile]] = None) -> None:
        self.profiles = profiles or FILE_PROFILES

    def parse(self, file_path: str | Path, profile_key: Optional[str] = None) -> ConnectionParseResult:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        excel = pd.ExcelFile(path)
        profile = self._resolve_profile(excel.sheet_names, profile_key)

        warnings: List[str] = []
        normalized_frames: List[pd.DataFrame] = []
        summary_rows: List[Dict[str, Any]] = []

        for actual_sheet_name, sheet_profile in self._iter_matching_sheets(excel.sheet_names, profile):
            try:
                raw_df, header_row = self._read_sheet(path, actual_sheet_name)
            except Exception as exc:  # pragma: no cover - defensive reporting
                warnings.append(f"Could not read sheet '{actual_sheet_name}': {exc}")
                continue

            if raw_df.empty:
                summary_rows.append(
                    {
                        "source_sheet": actual_sheet_name,
                        "status": "empty",
                        "rows": 0,
                        "rows_with_nup": 0,
                        "rows_with_any_date": 0,
                        "header_row": header_row,
                    }
                )
                continue

            normalized = self._normalize_sheet(
                raw_df=raw_df,
                path=path,
                actual_sheet_name=actual_sheet_name,
                profile=profile,
                sheet_profile=sheet_profile,
                header_row=header_row,
            )
            normalized_frames.append(normalized)

            date_cols = ["commissioning_actual", "commissioning_estimated", "cod_actual", "cod_estimated"]
            summary_rows.append(
                {
                    "source_sheet": actual_sheet_name,
                    "status": "parsed",
                    "rows": int(len(normalized)),
                    "rows_with_nup": int(normalized["nup"].notna().sum()) if "nup" in normalized else 0,
                    "rows_with_any_date": int(normalized[date_cols].notna().any(axis=1).sum()) if len(normalized) else 0,
                    "header_row": header_row,
                }
            )

        ignored = self._ignored_sheets(excel.sheet_names)
        for sheet_name in ignored:
            summary_rows.append(
                {
                    "source_sheet": sheet_name,
                    "status": "ignored",
                    "rows": None,
                    "rows_with_nup": None,
                    "rows_with_any_date": None,
                    "header_row": None,
                }
            )

        safe_frames = [
            frame.reindex(columns=CANONICAL_COLUMNS).astype("object")
            for frame in normalized_frames
            if frame is not None and not frame.empty
        ]
        if safe_frames:
            records = pd.concat(safe_frames, ignore_index=True, sort=False)
        else:
            records = pd.DataFrame(columns=CANONICAL_COLUMNS)
            warnings.append("No recognized sheets were parsed.")

        records = self._finalize_records(records)
        sheet_summaries = pd.DataFrame(summary_rows)
        return ConnectionParseResult(
            file_path=str(path),
            profile_key=profile.key,
            profile_label=profile.label,
            records=records,
            sheet_summaries=sheet_summaries,
            warnings=warnings,
        )

    def parse_to_dataframe(self, file_path: str | Path, profile_key: Optional[str] = None) -> pd.DataFrame:
        """Convenience method for callers that only need the canonical DataFrame."""
        return self.parse(file_path, profile_key=profile_key).records

    def _resolve_profile(self, sheet_names: Sequence[str], profile_key: Optional[str]) -> FileProfile:
        if profile_key:
            if profile_key not in self.profiles:
                raise ValueError(f"Unknown profile_key '{profile_key}'. Valid values: {list(self.profiles)}")
            return self.profiles[profile_key]

        sheet_key_set = {_normalize_key(sheet) for sheet in sheet_names}
        scores: List[Tuple[int, FileProfile]] = []
        for profile in self.profiles.values():
            expected = {_normalize_key(sheet) for sheet in profile.sheets.keys()}
            scores.append((len(sheet_key_set.intersection(expected)), profile))

        best_score, best_profile = max(scores, key=lambda item: item[0])
        if best_score == 0:
            raise ValueError(
                "Could not detect the CEN connection file profile. "
                f"Available sheets: {', '.join(sheet_names)}"
            )
        return best_profile

    def _iter_matching_sheets(self, sheet_names: Sequence[str], profile: FileProfile) -> Iterable[Tuple[str, SheetProfile]]:
        by_normalized_actual = {_normalize_key(sheet): sheet for sheet in sheet_names}
        for expected_sheet, sheet_profile in profile.sheets.items():
            actual_sheet = by_normalized_actual.get(_normalize_key(expected_sheet))
            if actual_sheet:
                yield actual_sheet, sheet_profile

    def _ignored_sheets(self, sheet_names: Sequence[str]) -> List[str]:
        ignored = []
        for sheet in sheet_names:
            if _normalize_key(sheet) in IGNORED_SHEETS:
                ignored.append(sheet)
        return ignored

    def _read_sheet(self, path: Path, sheet_name: str) -> Tuple[pd.DataFrame, int]:
        preview = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=12)
        header_row = self._detect_header_row(preview)
        df = pd.read_excel(path, sheet_name=sheet_name, header=header_row)
        df = df.dropna(how="all")
        df.columns = [str(col).strip() for col in df.columns]
        return df, header_row

    def _detect_header_row(self, preview: pd.DataFrame) -> int:
        best_row = 0
        best_score = -1
        aliases = []
        for values in COMMON_OPTIONAL_FIELDS.values():
            aliases.extend(values)
        alias_keys = {_normalize_key(alias) for alias in aliases}
        for idx, row in preview.iterrows():
            values = {_normalize_key(value) for value in row.tolist() if pd.notna(value)}
            score = len(values.intersection(alias_keys))
            if score > best_score:
                best_score = score
                best_row = int(idx)
        return best_row

    def _normalize_sheet(
        self,
        raw_df: pd.DataFrame,
        path: Path,
        actual_sheet_name: str,
        profile: FileProfile,
        sheet_profile: SheetProfile,
        header_row: int,
    ) -> pd.DataFrame:
        source_cols = _build_source_column_map(raw_df.columns)
        data: Dict[str, Any] = {
            "source_system": profile.source_system,
            "source_detail": profile.source_detail,
            "source_profile": profile.key,
            "source_file": path.name,
            "source_sheet": actual_sheet_name,
            "source_sheet_role": sheet_profile.source_sheet_role,
            "row_number": raw_df.index.to_series().astype(int) + header_row + 2,
            "record_action": sheet_profile.record_action,
            "target_status": sheet_profile.target_status,
            "connection_project_type": sheet_profile.project_type,
        }

        normalized = pd.DataFrame(data)

        for canonical_col, aliases in sheet_profile.optional_fields.items():
            source_col = _find_column(source_cols, aliases)
            if source_col is not None:
                normalized[canonical_col] = raw_df[source_col].apply(_clean_scalar)
            else:
                normalized[canonical_col] = pd.NA

        normalized["nup"] = normalized["nup"].apply(_normalize_nup)
        normalized["normalized_project_name"] = normalized["project_name"].apply(normalize_project_name)

        normalized["commissioning_actual"] = _first_date_series(
            raw_df, source_cols, COMMON_OPTIONAL_FIELDS["pes_actual_raw"]
        )
        normalized["commissioning_estimated"] = _first_date_series(
            raw_df, source_cols, COMMON_OPTIONAL_FIELDS["pes_estimated_raw"]
        )
        normalized["cod_actual"] = _first_date_series(
            raw_df, source_cols, COMMON_OPTIONAL_FIELDS["eo_actual_raw"]
        )
        normalized["cod_estimated"] = _first_date_series(
            raw_df, source_cols, COMMON_OPTIONAL_FIELDS["eo_estimated_raw"]
        )
        normalized["eo_letter_date"] = _first_date_series(
            raw_df, source_cols, COMMON_OPTIONAL_FIELDS["eo_letter_date"]
        )

        return normalized

    def _finalize_records(self, records: pd.DataFrame) -> pd.DataFrame:
        for col in CANONICAL_COLUMNS:
            if col not in records.columns:
                records[col] = pd.NA
        records = records[CANONICAL_COLUMNS]

        # Remove fully empty logical records while keeping rows that have at least name, NUP or dates.
        logical_cols = [
            "nup",
            "project_name",
            "commissioning_actual",
            "commissioning_estimated",
            "cod_actual",
            "cod_estimated",
        ]
        records = records.loc[records[logical_cols].notna().any(axis=1)].copy()
        return records.reset_index(drop=True)


def _build_source_column_map(columns: Iterable[Any]) -> Dict[str, str]:
    return {_normalize_key(col): str(col).strip() for col in columns}


def _find_column(source_cols: Dict[str, str], aliases: Sequence[str]) -> Optional[str]:
    for alias in aliases:
        key = _normalize_key(alias)
        if key in source_cols:
            return source_cols[key]
    return None


def _first_date_series(raw_df: pd.DataFrame, source_cols: Dict[str, str], aliases: Sequence[str]) -> pd.Series:
    source_col = _find_column(source_cols, aliases)
    if source_col is None:
        return pd.Series([pd.NaT] * len(raw_df), index=raw_df.index)
    return raw_df[source_col].apply(parse_excel_date)


def _clean_scalar(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA
    if isinstance(value, str):
        text = re.sub(r"\s+", " ", value).strip()
        return text if text else pd.NA
    return value


def _normalize_nup(value: Any) -> Any:
    value = _clean_scalar(value)
    if value is pd.NA or pd.isna(value):
        return pd.NA
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    text = re.sub(r"\.0$", "", text)
    text = re.sub(r"[^0-9A-Za-z\-_/]", "", text)
    return text if text else pd.NA


def _normalize_key(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_project_name(value: Any) -> Any:
    """Normalize project names for later matching previews."""
    value = _clean_scalar(value)
    if value is pd.NA or pd.isna(value):
        return pd.NA
    text = _normalize_key(value)
    replacements = {
        "proyecto": "",
        "central": "",
        "parque": "",
        "planta": "",
        "fotovoltaico": "fv",
        "solar fotovoltaico": "fv",
        "subestacion": "se",
        "s e": "se",
    }
    for old, new in replacements.items():
        text = re.sub(rf"\b{re.escape(old)}\b", new, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if text else pd.NA


def parse_excel_date(value: Any) -> Any:
    """Parse dates from Excel, pandas timestamps, serials, or text.

    Returns datetime.date or pandas.NaT.
    """
    if value is None or pd.isna(value):
        return pd.NaT

    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value <= 0:
            return pd.NaT
        try:
            return (EXCEL_EPOCH + timedelta(days=float(value))).date()
        except Exception:
            return pd.NaT

    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "-", "s/i", "sin informacion"}:
        return pd.NaT

    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return pd.NaT
    return parsed.date()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Inspect and normalize a CEN connection spreadsheet.")
    parser.add_argument("file_path", help="Path to the CEN connection Excel file")
    parser.add_argument("--profile", choices=list(FILE_PROFILES), default=None)
    parser.add_argument("--csv", default=None, help="Optional output CSV path")
    args = parser.parse_args()

    result = CENConnectionFileParser().parse(args.file_path, profile_key=args.profile)
    print(result.summary_dict())
    print(result.sheet_summaries.to_string(index=False))
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    print(result.records.head(20).to_string(index=False))
    if args.csv:
        result.records.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"Saved normalized CSV to {args.csv}")
