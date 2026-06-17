"""Streamlit view for CEN connection file inspection.

This view is intentionally read-only for the database. It only uploads,
normalizes, summarizes and previews CEN connection files.
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Optional

import pandas as pd
import streamlit as st

from parsers.cen_connection_files_parse import CENConnectionFileParser, ConnectionParseResult


class CENConnectionView:
    """UI for CEN connection enrichment file inspection."""

    UPLOAD_HELP = (
        "Estos archivos pertenecen a CEN Conexiones y se usarán como fuente "
        "secundaria para enriquecer proyectos existentes. No crean proyectos nuevos."
    )

    @staticmethod
    def render() -> None:
        st.markdown("### CEN Conexiones — Enriquecimiento de BD")
        st.caption(
            "Inspección inicial de archivos de conexión. "
            "Esta fase no actualiza la base de datos."
        )

        tab_entry, tab_construction = st.tabs(
            [
                "Entrada en Operación",
                "Declarados en Construcción",
            ]
        )

        with tab_entry:
            CENConnectionView._render_uploader(
                label="Proyectos con Entrada en Operación (.xlsx)",
                profile_key="entry_operation",
                uploader_key="cen_connection_entry_operation_uploader",
            )

        with tab_construction:
            CENConnectionView._render_uploader(
                label="Proyectos Declarados en Construcción (.xlsx)",
                profile_key="declared_construction",
                uploader_key="cen_connection_declared_construction_uploader",
            )

    @staticmethod
    def render_expander(expanded: bool = False) -> None:
        """Convenience wrapper for integration in app.py."""
        with st.expander("CEN Conexiones — Enriquecimiento de BD", expanded=expanded):
            CENConnectionView.render()

    @staticmethod
    def _render_uploader(label: str, profile_key: str, uploader_key: str) -> None:
        uploaded = st.file_uploader(
            label,
            type=["xlsx", "xlsm", "xls"],
            key=uploader_key,
            help=CENConnectionView.UPLOAD_HELP,
        )

        if uploaded is None:
            st.info("Sube un archivo para inspeccionar su estructura y fechas normalizadas.")
            return

        tmp_path = CENConnectionView._save_uploaded_file(uploaded)
        try:
            result = CENConnectionFileParser().parse(tmp_path, profile_key=profile_key)
        except Exception as exc:
            st.error(f"No fue posible leer el archivo: {exc}")
            return

        CENConnectionView._render_result(result)

    @staticmethod
    def _save_uploaded_file(uploaded) -> Path:
        suffix = Path(uploaded.name).suffix or ".xlsx"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded.getbuffer())
            return Path(tmp.name)

    @staticmethod
    def _render_result(result: ConnectionParseResult) -> None:
        st.success(f"Archivo reconocido: {result.profile_label}")

        summary = result.summary_dict()
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Filas normalizadas", summary["total_rows"])
        col2.metric("Filas con NUP", summary["rows_with_nup"])
        col3.metric("Filas con fechas", summary["rows_with_any_date"])
        col4.metric("Advertencias", summary["warnings"])

        with st.expander("Resumen por hoja", expanded=True):
            if result.sheet_summaries.empty:
                st.warning("No hay resumen de hojas disponible.")
            else:
                st.dataframe(result.sheet_summaries, use_container_width=True, hide_index=True)

        if result.warnings:
            with st.expander("Advertencias", expanded=False):
                for warning in result.warnings:
                    st.warning(warning)

        records = result.records.copy()
        if records.empty:
            st.warning("No se normalizaron filas útiles desde este archivo.")
            return

        CENConnectionView._render_date_coverage(records)
        CENConnectionView._render_type_coverage(records)
        CENConnectionView._render_preview(records)
        CENConnectionView._render_download(records, result.profile_key)

    @staticmethod
    def _render_date_coverage(records: pd.DataFrame) -> None:
        date_cols = [
            "commissioning_actual",
            "commissioning_estimated",
            "cod_actual",
            "cod_estimated",
        ]
        existing = [col for col in date_cols if col in records.columns]
        coverage = pd.DataFrame(
            {
                "field": existing,
                "non_empty_rows": [int(records[col].notna().sum()) for col in existing],
            }
        )
        coverage["total_rows"] = len(records)
        coverage["coverage_pct"] = (coverage["non_empty_rows"] / coverage["total_rows"] * 100).round(1)

        with st.expander("Cobertura de fechas", expanded=True):
            st.dataframe(coverage, use_container_width=True, hide_index=True)

    @staticmethod
    def _render_type_coverage(records: pd.DataFrame) -> None:
        cols = ["source_sheet", "connection_project_type"]
        if not set(cols).issubset(records.columns):
            return
        coverage = (
            records.groupby(cols, dropna=False)
            .size()
            .reset_index(name="rows")
            .sort_values(["source_sheet", "connection_project_type"])
        )
        with st.expander("Cobertura por hoja y tipo", expanded=False):
            st.dataframe(coverage, use_container_width=True, hide_index=True)

    @staticmethod
    def _render_preview(records: pd.DataFrame) -> None:
        preferred_cols = [
            "source_sheet",
            "row_number",
            "connection_project_type",
            "nup",
            "project_name",
            "company",
            "region",
            "commune",
            "technology",
            "power_mw",
            "commissioning_actual",
            "commissioning_estimated",
            "cod_actual",
            "cod_estimated",
        ]
        cols = [col for col in preferred_cols if col in records.columns]
        st.markdown("#### Preview normalizado")
        st.dataframe(records[cols].head(300), use_container_width=True, hide_index=True)
        if len(records) > 300:
            st.caption(f"Mostrando 300 de {len(records)} filas normalizadas.")

    @staticmethod
    def _render_download(records: pd.DataFrame, profile_key: str) -> None:
        csv_data = records.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Descargar CSV normalizado",
            data=csv_data,
            file_name=f"cen_connection_{profile_key}_normalized.csv",
            mime="text/csv",
            use_container_width=True,
        )
