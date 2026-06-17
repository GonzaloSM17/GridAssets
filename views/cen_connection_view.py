"""Streamlit view for CEN Conexiones enrichment."""
from __future__ import annotations

from pathlib import Path
import tempfile

import pandas as pd
import streamlit as st

from parsers.cen_connection_files_parse import CENConnectionFileParser, ConnectionParseResult
from services.cen_connection_enrichment_service import CENConnectionEnrichmentService


class CENConnectionView:
    """UI for CEN Conexiones complementary enrichment."""

    UPLOAD_HELP = (
        "Archivos CEN Conexiones usados para complementar proyectos existentes. "
        "No crean proyectos nuevos."
    )

    @staticmethod
    def render_expander(expanded: bool = False) -> None:
        with st.expander("Complementar base de datos", expanded=expanded):
            CENConnectionView.render()

    @staticmethod
    def render() -> None:
        st.markdown("### Complementar base de datos")
        st.caption(
            "Carga archivos CEN Conexiones para enriquecer proyectos existentes "
            "con NUP y fechas PES/EO normalizadas."
        )
        st.caption(
            "Módulo CEN Conexiones: inspección, preview de cruce y enriquecimiento seguro."
        )

        tab_entry, tab_construction = st.tabs(
            ["Entrada en Operación", "Declarados en Construcción"]
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
        col1.metric("Filas normalizadas", summary.get("total_rows", 0))
        col2.metric("Filas con NUP", summary.get("rows_with_nup", 0))
        col3.metric("Filas con fechas", summary.get("rows_with_any_date", 0))
        col4.metric("Advertencias", summary.get("warnings", 0))

        with st.expander("Resumen por hoja", expanded=True):
            if result.sheet_summaries.empty:
                st.warning("No hay resumen de hojas disponible.")
            else:
                st.dataframe(result.sheet_summaries, width="stretch", hide_index=True)

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
        CENConnectionView._render_match_preview_and_apply(records, result.profile_key)
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
        coverage["coverage_pct"] = (
            coverage["non_empty_rows"] / coverage["total_rows"] * 100
        ).round(1)

        with st.expander("Cobertura de fechas", expanded=True):
            st.dataframe(coverage, width="stretch", hide_index=True)

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
            st.dataframe(coverage, width="stretch", hide_index=True)

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
        st.dataframe(records[cols].head(300), width="stretch", hide_index=True)
        if len(records) > 300:
            st.caption(f"Mostrando 300 de {len(records)} filas normalizadas.")

    @staticmethod
    def _render_match_preview_and_apply(records: pd.DataFrame, profile_key: str) -> None:
        st.divider()
        st.markdown("#### Preview de cruce con BD")
        st.caption(
            "Intenta primero NUP + tipo compatible. Si la BD no tiene NUP, usa "
            "nombre normalizado + tipo compatible. No crea proyectos nuevos."
        )

        should_match = st.checkbox(
            "Cruzar archivo normalizado contra proyectos existentes",
            value=False,
            key=f"cen_connection_match_preview_{profile_key}",
        )
        if not should_match:
            return

        st.info("Ejecutando cruce contra la base de datos...")
        try:
            with st.spinner("Cruzando proyectos existentes..."):
                service = CENConnectionEnrichmentService()
                preview = service.build_match_preview(records)
        except Exception as exc:
            st.error(f"No fue posible cruzar contra la base de datos: {exc}")
            return

        if preview.empty:
            st.warning("No se generó preview de cruce.")
            return

        st.success(f"Preview de cruce generado: {len(preview)} filas.")
        CENConnectionView._render_match_summary(preview)
        CENConnectionView._render_match_table(preview)
        CENConnectionView._render_match_download(preview, profile_key)
        CENConnectionView._render_apply_controls(service, preview, profile_key)

    @staticmethod
    def _render_match_summary(preview: pd.DataFrame) -> None:
        safe_count = int(
            preview.get("is_safe_to_apply", pd.Series(dtype=bool)).fillna(False).sum()
        )
        proposed_date_changes = int(
            preview.get("date_changes_proposed", pd.Series(dtype=int)).fillna(0).sum()
        )
        proposed_nup_changes = int(
            preview.get("would_update_nup", pd.Series(dtype=bool)).fillna(False).sum()
        )

        col1, col2, col3 = st.columns(3)
        col1.metric("Filas cruzadas", len(preview))
        col2.metric("Matches seguros", safe_count)
        col3.metric("Cambios propuestos", proposed_date_changes + proposed_nup_changes)

        if "match_status" in preview.columns:
            status_counts = (
                preview.groupby("match_status", dropna=False)
                .size()
                .reset_index(name="rows")
                .sort_values("rows", ascending=False)
            )
            with st.expander("Resumen de estados de match", expanded=True):
                st.dataframe(status_counts, width="stretch", hide_index=True)

    @staticmethod
    def _render_match_table(preview: pd.DataFrame) -> None:
        preferred_cols = [
            "match_status",
            "action_proposed",
            "is_safe_to_apply",
            "date_changes_proposed",
            "would_update_nup",
            "source_sheet",
            "row_number",
            "connection_project_type",
            "nup",
            "project_name",
            "matched_project_id",
            "matched_project_name",
            "matched_project_type",
            "matched_project_nup",
            "name_score",
            "candidate_count",
            "top_candidates",
            "match_comment",
            "commissioning_actual",
            "commissioning_estimated",
            "cod_actual",
            "cod_estimated",
        ]
        cols = [col for col in preferred_cols if col in preview.columns]

        st.markdown("##### Tabla de preview")
        st.dataframe(preview[cols].head(500), width="stretch", hide_index=True)
        if len(preview) > 500:
            st.caption(f"Mostrando 500 de {len(preview)} filas de preview.")

    @staticmethod
    def _render_match_download(preview: pd.DataFrame, profile_key: str) -> None:
        csv_data = preview.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Descargar CSV de preview de cruce",
            data=csv_data,
            file_name=f"cen_connection_{profile_key}_match_preview.csv",
            mime="text/csv",
            width="stretch",
        )

    @staticmethod
    def _render_apply_controls(
        service: CENConnectionEnrichmentService,
        preview: pd.DataFrame,
        profile_key: str,
    ) -> None:
        safe_count = int(
            preview.get("is_safe_to_apply", pd.Series(dtype=bool)).fillna(False).sum()
        )
        if safe_count == 0:
            st.info("No hay matches seguros para aplicar en esta carga.")
            return

        with st.expander("Aplicar enriquecimiento", expanded=False):
            st.warning(
                "Esta acción escribe en la BD solo para matches seguros. No crea proyectos "
                "y omite casos ambiguos, candidatos débiles o no encontrados."
            )
            confirm = st.checkbox(
                "Confirmo aplicar solo matches seguros",
                value=False,
                key=f"cen_connection_confirm_apply_{profile_key}",
            )
            if not confirm:
                return

            if st.button(
                "Aplicar enriquecimiento seguro",
                key=f"cen_connection_apply_{profile_key}",
                type="primary",
                width="stretch",
            ):
                try:
                    with st.spinner("Aplicando enriquecimiento seguro..."):
                        result = service.apply_safe_enrichment(preview)
                except Exception as exc:
                    st.error(f"No fue posible aplicar el enriquecimiento: {exc}")
                    return

                summary = result.get("summary", {})
                st.success("Enriquecimiento aplicado.")
                st.json(summary)

                details = result.get("details")
                if isinstance(details, pd.DataFrame) and not details.empty:
                    with st.expander("Detalle de aplicación", expanded=False):
                        st.dataframe(details, width="stretch", hide_index=True)

    @staticmethod
    def _render_download(records: pd.DataFrame, profile_key: str) -> None:
        csv_data = records.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Descargar CSV normalizado",
            data=csv_data,
            file_name=f"cen_connection_{profile_key}_normalized.csv",
            mime="text/csv",
            width="stretch",
        )
