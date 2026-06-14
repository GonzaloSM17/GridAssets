"""CNE Ingestion View — file upload panel.

Accepts the CNE monthly Excel workbook, saves it to a session-persistent temp
path, and leaves it ready for the structural inspection + AI validation step.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from ui.app_config import AppConfig


class CNEIngestionView:

    # ------------------------------------------------------------------
    # Public: render (column version — used in 3-column top bar)
    # ------------------------------------------------------------------

    @staticmethod
    def render_cne_panel_column() -> None:
        """Compact CNE file upload panel designed for use inside a column."""
        st.markdown(
            """
            <div class="section-card section-card-green">
                <div class="section-title">Archivo CNE</div>
                <div class="section-caption">Declaración de Construcción mensual</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander("📂 Cargar archivo", expanded=False):
            uploaded = st.file_uploader(
                "Declaración de Construcción CNE (.xlsx)",
                type=["xlsx", "xlsm", "xls"],
                key="cne_file_uploader",
                help="Archivo Excel mensual CNE: Tablas-Declaracion-Construccion-<mes>-<año>.xlsx",
                label_visibility="collapsed",
            )

            if uploaded is not None:
                CNEIngestionView._handle_upload(uploaded)

            elif "cne_temp_path" in st.session_state:
                path = Path(st.session_state["cne_temp_path"])
                if path.exists():
                    CNEIngestionView._show_loaded_file(
                        path,
                        st.session_state.get("cne_filename", path.name),
                    )
                else:
                    st.info("No hay archivo CNE cargado.")

        # -- Conexión files (disabled — function not yet enabled) -------
        st.caption("─── Archivos de Conexión ───")

        with st.expander(
            "📂 Conexión — Proyectos con Entrada en Operación", expanded=False
        ):
            st.caption("🔒 Función no habilitada")
            st.file_uploader(
                "Proyectos con Entrada en Operación (.xlsx)",
                type=["xlsx", "xlsm", "xls"],
                key="conexion_operacion_uploader",
                label_visibility="collapsed",
                disabled=True,
            )

        with st.expander(
            "📂 Conexión — Proyectos Declarados en Construcción", expanded=False
        ):
            st.caption("🔒 Función no habilitada")
            st.file_uploader(
                "Proyectos Declarados en Construcción (.xlsx)",
                type=["xlsx", "xlsm", "xls"],
                key="conexion_construccion_uploader",
                label_visibility="collapsed",
                disabled=True,
            )

    # ------------------------------------------------------------------
    # Public: render (full-width version — kept for standalone use)
    # ------------------------------------------------------------------

    @staticmethod
    def render_cne_upload_panel() -> None:
        """Render the CNE file upload expander (full-width)."""
        st.markdown(
            """
            <div class="section-card section-card-green">
                <div class="section-title">Archivo CNE — Declaración de Construcción</div>
                <div class="section-caption">
                    Sube el archivo Excel mensual de la CNE para actualizar la base de
                    datos de proyectos. El archivo será inspeccionado y validado antes de
                    ejecutar la población.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander("Cargar archivo CNE", expanded=False):
            uploaded = st.file_uploader(
                "Declaración de Construcción CNE (.xlsx / .xlsm)",
                type=["xlsx", "xlsm", "xls"],
                key="cne_file_uploader",
                help=(
                    "Archivo Excel mensual publicado por la CNE. "
                    "Nombre esperado: Tablas-Declaracion-Construccion-<mes>-<año>.xlsx"
                ),
            )

            if uploaded is not None:
                CNEIngestionView._handle_upload(uploaded)

            elif "cne_temp_path" in st.session_state:
                path = Path(st.session_state["cne_temp_path"])
                if path.exists():
                    CNEIngestionView._show_loaded_file(
                        path,
                        st.session_state.get("cne_filename", path.name),
                    )
                else:
                    st.info("No hay archivo CNE cargado en esta sesión.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_upload(uploaded) -> None:
        """Save the uploaded file to a deterministic temp path."""
        tmp_dir = Path(tempfile.gettempdir()) / "sen_cne_uploads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / uploaded.name

        with open(tmp_path, "wb") as f:
            f.write(uploaded.getbuffer())

        st.session_state["cne_temp_path"] = str(tmp_path)
        st.session_state["cne_filename"] = uploaded.name

        # Clear stale inspection/validation results from a previous file
        for key in ("cne_structure_report", "cne_validation_result"):
            st.session_state.pop(key, None)

        CNEIngestionView._show_loaded_file(tmp_path, uploaded.name)

    @staticmethod
    def _show_loaded_file(path: Path, filename: str) -> None:
        """Display file info and the Inspect button."""
        size_mb = path.stat().st_size / (1024 * 1024)

        col_info, col_size, col_btn = st.columns([3, 1, 1])

        with col_info:
            st.success(f"📄 **{filename}**")

        with col_size:
            st.metric("Tamaño", f"{size_mb:.2f} MB")

        with col_btn:
            st.button(
                "Inspeccionar →",
                type="primary",
                use_container_width=True,
                key="cne_inspect_btn",
                help=(
                    "Ejecuta la inspección estructural (CNEExcelStructureReader) "
                    "seguida de validación con agente IA (CNEFileValidationAgent)."
                ),
            )

        st.caption(
            f"Ruta temporal: `{path}` — "
            "Listo para inspección estructural y validación con agente CNE."
        )
