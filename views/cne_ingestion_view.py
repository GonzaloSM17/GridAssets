"""CNE Ingestion View - file upload and ingestion panel.

This view manages the CNE monthly Excel workbook ingestion flow:

1. Upload CNE Excel file.
2. Read deterministic workbook structure.
3. Optionally validate structure with OpenAI.
4. Run deterministic ProjectParser.
5. Preview database population with dry-run.
6. Populate database after successful preview.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import streamlit as st
import traceback

from agents.cne_file_structure_agent import CNEFileStructureAgent
from parsers.db_projects_parse import ProjectParser
from services.cne_excel_structure_reader import CNEExcelStructureReader
from services.db_populate import DatabasePopulator


class CNEIngestionView:
    """Render CNE upload, structure validation, parser and population actions."""

    # ------------------------------------------------------------------
    # Public render methods
    # ------------------------------------------------------------------

    @staticmethod
    def render_cne_panel_column() -> None:
        """Render compact CNE file upload panel for the top bar column."""
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
                key="cne_file_uploader_column",
                help=(
                    "Archivo Excel mensual CNE: "
                    "Tablas-Declaracion-Construccion-<mes>-<año>.xlsx"
                ),
                label_visibility="collapsed",
            )

            CNEIngestionView._render_upload_state(uploaded)

        CNEIngestionView._render_disabled_connection_uploads()

    @staticmethod
    def render_cne_upload_panel() -> None:
        """Render full-width CNE file upload panel."""
        st.markdown(
            """
            <div class="section-card section-card-green">
                <div class="section-title">Archivo CNE — Declaración de Construcción</div>
                <div class="section-caption">
                    Sube el archivo Excel mensual de la CNE para actualizar la base de
                    datos de proyectos. El archivo será inspeccionado antes de ejecutar
                    la población.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        with st.expander("Cargar archivo CNE", expanded=False):
            uploaded = st.file_uploader(
                "Declaración de Construcción CNE (.xlsx / .xlsm)",
                type=["xlsx", "xlsm", "xls"],
                key="cne_file_uploader_full",
                help=(
                    "Archivo Excel mensual publicado por la CNE. "
                    "Nombre esperado: Tablas-Declaracion-Construccion-<mes>-<año>.xlsx"
                ),
            )

            CNEIngestionView._render_upload_state(uploaded)

    # ------------------------------------------------------------------
    # Upload state
    # ------------------------------------------------------------------

    @staticmethod
    def _render_upload_state(uploaded: Any | None) -> None:
        """Render the uploaded file if available, or restore it from session state."""
        if uploaded is not None:
            path = CNEIngestionView._handle_upload(uploaded)
            CNEIngestionView._show_loaded_file(path, uploaded.name)
            return

        path_value = st.session_state.get("cne_temp_path")
        if not path_value:
            st.info("No hay archivo CNE cargado.")
            return

        path = Path(path_value)
        if not path.exists():
            st.info("No hay archivo CNE cargado.")
            return

        CNEIngestionView._show_loaded_file(
            path=path,
            filename=st.session_state.get("cne_filename", path.name),
        )

    @staticmethod
    def _handle_upload(uploaded: Any) -> Path:
        """Save uploaded file only when it changes and preserve inspection state."""
        tmp_dir = Path(tempfile.gettempdir()) / "sen_cne_uploads"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        tmp_path = tmp_dir / uploaded.name
        file_size = getattr(uploaded, "size", None)
        file_key = f"{uploaded.name}:{file_size}"

        previous_file_key = st.session_state.get("cne_uploaded_file_key")
        same_file = previous_file_key == file_key and tmp_path.exists()

        if same_file:
            return tmp_path

        with open(tmp_path, "wb") as file:
            file.write(uploaded.getbuffer())

        st.session_state["cne_uploaded_file_key"] = file_key
        st.session_state["cne_temp_path"] = str(tmp_path)
        st.session_state["cne_filename"] = uploaded.name

        CNEIngestionView._clear_ingestion_state()

        return tmp_path

    @staticmethod
    def _clear_ingestion_state() -> None:
        """Clear structure, AI, parser and population states."""
        for key in (
            "cne_structure_report",
            "cne_structure_ready",
            "cne_structure_error",
            "cne_structure_validation",
            "cne_ai_error",
            "cne_parser_object",
            "cne_parser_counts",
            "cne_parser_report",
            "cne_parser_ready",
            "cne_parser_error",
            "cne_population_preview",
            "cne_population_preview_ready",
            "cne_population_preview_error",
            "cne_population_result",
            "cne_population_done",
            "cne_population_error",
            "cne_last_message",
            "cne_last_message_type",
            "cne_population_preview_traceback",
        ):
            st.session_state.pop(key, None)

    @staticmethod
    def _clear_downstream_after_structure() -> None:
        """Clear AI, parser and population states after reading structure again."""
        for key in (
            "cne_structure_validation",
            "cne_ai_error",
            "cne_parser_object",
            "cne_parser_counts",
            "cne_parser_report",
            "cne_parser_ready",
            "cne_parser_error",
            "cne_population_preview",
            "cne_population_preview_ready",
            "cne_population_preview_error",
            "cne_population_result",
            "cne_population_done",
            "cne_population_error",
            "cne_population_preview_traceback",
        ):
            st.session_state.pop(key, None)

    @staticmethod
    def _clear_downstream_after_parser() -> None:
        """Clear population states after running parser again."""
        for key in (
            "cne_population_preview",
            "cne_population_preview_ready",
            "cne_population_preview_error",
            "cne_population_result",
            "cne_population_done",
            "cne_population_error",
        ):
            st.session_state.pop(key, None)

    # ------------------------------------------------------------------
    # Main loaded-file UI
    # ------------------------------------------------------------------

    @staticmethod
    def _show_loaded_file(path: Path, filename: str) -> None:
        """Show loaded CNE file and ordered ingestion actions."""
        st.markdown("#### Archivo CNE cargado")
        st.info(f"Archivo: **{filename}**")
        st.caption(f"Ruta temporal: `{path}`")

        st.markdown("### Secuencia de inspección y población")
        st.caption(
            "Primero lee la estructura. Luego ejecuta el parser. Finalmente, "
            "previsualiza la población antes de guardar en la base de datos."
        )

        structure_ready = CNEIngestionView._is_structure_ready()
        parser_ready = bool(st.session_state.get("cne_parser_ready"))
        preview_ready = bool(st.session_state.get("cne_population_preview_ready"))

        # --------------------------------------------------------------
        # Step buttons: inspection + parser
        # --------------------------------------------------------------
        col1, col2, col3 = st.columns(3)

        with col1:
            read_structure_clicked = st.button(
                "1. Leer estructura",
                type="primary",
                use_container_width=True,
                key="cne_read_structure_btn",
                help="Lee hojas, columnas, campos reconocidos y filas candidatas.",
            )

        with col2:
            validate_ai_clicked = st.button(
                "2. Validar con IA",
                type="secondary",
                use_container_width=True,
                key="cne_validate_ai_btn",
                disabled=not structure_ready,
                help="Valida la estructura con OpenAI. Requiere créditos API.",
            )

        with col3:
            run_parser_clicked = st.button(
                "3. Ejecutar parser",
                type="secondary",
                use_container_width=True,
                key="cne_run_parser_btn",
                disabled=not structure_ready,
                help="Ejecuta el parser determinístico sobre el Excel cargado.",
            )

        # --------------------------------------------------------------
        # Step buttons: database preview + population
        # --------------------------------------------------------------
        col4, col5 = st.columns(2)

        with col4:
            preview_clicked = st.button(
                "4. Previsualizar población",
                type="secondary",
                use_container_width=True,
                key="cne_preview_population_btn",
                disabled=not parser_ready,
                help="Ejecuta dry-run contra la base sin guardar cambios.",
            )

        with col5:
            populate_clicked = st.button(
                "5. Poblar BD",
                type="primary",
                use_container_width=True,
                key="cne_populate_database_btn",
                disabled=not preview_ready,
                help="Guarda los proyectos CNE en la base de datos.",
            )

        # --------------------------------------------------------------
        # Button actions
        # --------------------------------------------------------------
        if read_structure_clicked:
            CNEIngestionView._read_cne_structure(path)
            st.rerun()

        if validate_ai_clicked:
            CNEIngestionView._validate_cne_structure_with_ai()
            st.rerun()

        if run_parser_clicked:
            CNEIngestionView._run_cne_parser(path)
            st.rerun()

        if preview_clicked:
            CNEIngestionView._preview_cne_population()
            st.rerun()

        if populate_clicked:
            CNEIngestionView._populate_cne_database()
            st.rerun()

        # --------------------------------------------------------------
        # Results
        # --------------------------------------------------------------
        st.divider()

        CNEIngestionView._show_last_message()
        CNEIngestionView._show_structure_result()
        CNEIngestionView._show_structure_validation_result()
        CNEIngestionView._show_parser_result()
        CNEIngestionView._show_population_preview_result()
        CNEIngestionView._show_population_result()

    @staticmethod
    def _is_structure_ready() -> bool:
        """Return whether deterministic structure report is available."""
        return bool(
            st.session_state.get("cne_structure_ready")
            or st.session_state.get("cne_structure_report")
        )

    # ------------------------------------------------------------------
    # Step 1: deterministic structure reading
    # ------------------------------------------------------------------

    @staticmethod
    def _read_cne_structure(path: Path) -> None:
        """Read deterministic Excel structure before AI validation or parser execution."""
        try:
            with st.spinner("Leyendo estructura del archivo CNE..."):
                structure_report = CNEExcelStructureReader(path).read()

            st.session_state["cne_structure_report"] = structure_report
            st.session_state["cne_structure_ready"] = True
            st.session_state.pop("cne_structure_error", None)

            CNEIngestionView._clear_downstream_after_structure()
            CNEIngestionView._set_last_message(
                message="Estructura del Excel leída correctamente.",
                message_type="success",
            )

        except Exception as error:
            st.session_state["cne_structure_ready"] = False
            st.session_state["cne_structure_error"] = str(error)
            CNEIngestionView._set_last_message(
                message="No se pudo leer la estructura del archivo CNE.",
                message_type="error",
            )
            st.exception(error)

    # ------------------------------------------------------------------
    # Step 2: optional OpenAI validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_cne_structure_with_ai() -> None:
        """Validate previously read Excel structure with OpenAI."""
        structure_report = st.session_state.get("cne_structure_report")

        if not structure_report:
            CNEIngestionView._set_last_message(
                message="Primero debes leer la estructura del archivo.",
                message_type="warning",
            )
            return

        try:
            with st.spinner("Validando estructura con agente IA..."):
                validation = CNEFileStructureAgent().validate(structure_report)

            st.session_state["cne_structure_validation"] = validation
            st.session_state.pop("cne_ai_error", None)

            if validation.get("is_parse_ready"):
                CNEIngestionView._set_last_message(
                    message="Validación IA completada. El archivo está listo para parser.",
                    message_type="success",
                )
            else:
                CNEIngestionView._set_last_message(
                    message=(
                        "Validación IA completada. "
                        "El agente recomienda revisión antes del parser."
                    ),
                    message_type="warning",
                )

        except Exception as error:
            st.session_state["cne_ai_error"] = str(error)
            CNEIngestionView._set_last_message(
                message=(
                    "No se pudo completar la validación IA. "
                    "Puedes revisar la estructura determinística y ejecutar el parser "
                    "manualmente si corresponde."
                ),
                message_type="warning",
            )

    # ------------------------------------------------------------------
    # Step 3: deterministic parser
    # ------------------------------------------------------------------

    @staticmethod
    def _run_cne_parser(path: Path) -> None:
        """Run deterministic CNE parser after structure has been read."""
        if not CNEIngestionView._is_structure_ready():
            CNEIngestionView._set_last_message(
                message="Primero debes leer la estructura del archivo.",
                message_type="warning",
            )
            return

        validation = st.session_state.get("cne_structure_validation")
        ai_error = st.session_state.get("cne_ai_error")

        if validation and not validation.get("is_parse_ready"):
            CNEIngestionView._set_last_message(
                message=(
                    "El agente IA no marcó este archivo como listo para parser. "
                    "El parser se ejecutará bajo revisión manual."
                ),
                message_type="warning",
            )

        elif ai_error and not validation:
            CNEIngestionView._set_last_message(
                message=(
                    "El parser se ejecutará sin validación IA porque OpenAI no está "
                    "disponible o no tiene créditos activos."
                ),
                message_type="info",
            )

        try:
            with st.spinner("Ejecutando parser determinístico..."):
                parser = ProjectParser(file_path=path)

            st.session_state["cne_parser_object"] = parser
            st.session_state["cne_parser_counts"] = parser.get_project_counts()
            st.session_state["cne_parser_report"] = parser.get_report()
            st.session_state["cne_parser_ready"] = True
            st.session_state.pop("cne_parser_error", None)

            CNEIngestionView._clear_downstream_after_parser()

            if not st.session_state.get("cne_last_message"):
                CNEIngestionView._set_last_message(
                    message="Parser ejecutado correctamente.",
                    message_type="success",
                )

        except Exception as error:
            st.session_state["cne_parser_ready"] = False
            st.session_state["cne_parser_error"] = str(error)
            CNEIngestionView._set_last_message(
                message="No se pudo ejecutar el parser CNE.",
                message_type="error",
            )
            st.exception(error)

    # ------------------------------------------------------------------
    # Step 4: population preview
    # ------------------------------------------------------------------

    @staticmethod
    def _preview_cne_population() -> None:
        """Run database population preview without committing changes."""
        parser = st.session_state.get("cne_parser_object")

        if parser is None:
            CNEIngestionView._set_last_message(
                message="Primero debes ejecutar el parser.",
                message_type="warning",
            )
            return

        try:
            with st.spinner("Ejecutando preview de población contra la base..."):
                populator = DatabasePopulator(verbose=False)
                preview = populator.preview_all(parser)

            st.session_state["cne_population_preview"] = preview
            st.session_state["cne_population_preview_ready"] = (
                preview.get("total", {}).get("errors", 0) == 0
            )

            st.session_state.pop("cne_population_preview_error", None)
            st.session_state.pop("cne_population_preview_traceback", None)

            errors = preview.get("total", {}).get("errors", 0)

            if errors:
                CNEIngestionView._set_last_message(
                    message=(
                        f"Preview completado con {errors} error(es). "
                        "Revisa el detalle antes de poblar."
                    ),
                    message_type="warning",
                )
            else:
                CNEIngestionView._set_last_message(
                    message="Preview completado correctamente. La población está lista.",
                    message_type="success",
                )

        except Exception as error:
            st.session_state["cne_population_preview_ready"] = False
            st.session_state["cne_population_preview_error"] = str(error)
            st.session_state["cne_population_preview_traceback"] = (
                traceback.format_exc()
            )

            CNEIngestionView._set_last_message(
                message="No se pudo ejecutar el preview de población.",
                message_type="error",
            )

    # ------------------------------------------------------------------
    # Step 5: committed population
    # ------------------------------------------------------------------

    @staticmethod
    def _populate_cne_database() -> None:
        """Populate database after a successful preview."""
        parser = st.session_state.get("cne_parser_object")
        preview = st.session_state.get("cne_population_preview")

        if parser is None:
            CNEIngestionView._set_last_message(
                message="Primero debes ejecutar el parser.",
                message_type="warning",
            )
            return

        if not preview:
            CNEIngestionView._set_last_message(
                message="Primero debes ejecutar el preview de población.",
                message_type="warning",
            )
            return

        errors = preview.get("total", {}).get("errors", 0)

        if errors:
            CNEIngestionView._set_last_message(
                message="No se puede poblar la BD porque el preview tiene errores.",
                message_type="error",
            )
            return

        try:
            with st.spinner("Poblando base de datos..."):
                populator = DatabasePopulator(verbose=False)
                result = populator.populate_all(parser, dry_run=False)

            st.session_state["cne_population_result"] = result
            st.session_state["cne_population_done"] = True
            st.session_state.pop("cne_population_error", None)

            CNEIngestionView._set_last_message(
                message="Base de datos poblada correctamente.",
                message_type="success",
            )

        except Exception as error:
            st.session_state["cne_population_done"] = False
            st.session_state["cne_population_error"] = str(error)

            CNEIngestionView._set_last_message(
                message="No se pudo poblar la base de datos.",
                message_type="error",
            )
            st.exception(error)

    # ------------------------------------------------------------------
    # Message helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _show_last_message() -> None:
        """Render the last action message outside button columns."""
        message = st.session_state.get("cne_last_message")
        message_type = st.session_state.get("cne_last_message_type", "info")

        if not message:
            return

        if message_type == "success":
            st.success(message)
        elif message_type == "warning":
            st.warning(message)
        elif message_type == "error":
            st.error(message)
        else:
            st.info(message)

    @staticmethod
    def _set_last_message(message: str, message_type: str = "info") -> None:
        """Store a user-facing message to render after rerun."""
        st.session_state["cne_last_message"] = message
        st.session_state["cne_last_message_type"] = message_type

    # ------------------------------------------------------------------
    # Result rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _show_structure_result() -> None:
        """Render deterministic structure inspection result."""
        structure_report = st.session_state.get("cne_structure_report")

        if not structure_report:
            return

        st.markdown("### 1. Resultado lectura estructural")

        missing_sheets = structure_report.get("missing_expected_sheets", [])
        unexpected_sheets = structure_report.get("unexpected_sheets", [])
        row_counts = structure_report.get("row_counts_by_sheet", {})
        missing_fields = structure_report.get("missing_fields_by_sheet", {})

        if missing_sheets:
            st.warning(f"Hojas esperadas faltantes: {', '.join(missing_sheets)}")
        else:
            st.success("Todas las hojas esperadas fueron encontradas.")

        if unexpected_sheets:
            st.info(f"Hojas adicionales detectadas: {', '.join(unexpected_sheets)}")

        with st.expander("Ver detalle de estructura", expanded=False):
            st.markdown("**Filas candidatas por hoja**")
            st.json(row_counts)

            st.markdown("**Campos faltantes por hoja**")
            st.json(missing_fields)

            st.markdown("**Reporte estructural completo**")
            st.json(structure_report)

    @staticmethod
    def _show_structure_validation_result() -> None:
        """Render OpenAI structure validation result."""
        validation = st.session_state.get("cne_structure_validation")
        ai_error = st.session_state.get("cne_ai_error")

        if not validation and not ai_error:
            return

        st.markdown("### 2. Resultado validación IA")

        if ai_error and not validation:
            st.warning(
                "Validación IA no disponible. La API de OpenAI respondió con error "
                "o no hay créditos disponibles."
            )
            with st.expander("Ver detalle del error IA", expanded=False):
                st.code(ai_error)
            return

        status = validation.get("status")
        is_cne_file = validation.get("is_cne_file")
        is_parse_ready = validation.get("is_parse_ready")
        confidence = validation.get("confidence", 0)

        if status == "valid":
            st.success("Agente IA: archivo CNE válido y apto para parser.")
        elif status == "review_required":
            st.warning("Agente IA: archivo CNE requiere revisión antes de continuar.")
        else:
            st.error("Agente IA: archivo no apto para parser.")

        col1, col2, col3 = st.columns(3)
        col1.metric("Es CNE", "Sí" if is_cne_file else "No")
        col2.metric("Listo parser", "Sí" if is_parse_ready else "No")
        col3.metric("Confianza", f"{confidence:.0%}")

        summary = validation.get("summary")
        if summary:
            st.write(summary)

        with st.expander("Detalle validación IA", expanded=False):
            st.write(
                "Cambios estructurales:",
                validation.get("structural_changes") or "Ninguno",
            )
            st.write("Advertencias:", validation.get("warnings") or "Ninguna")
            st.write("Bloqueos:", validation.get("blocking_issues") or "Ninguno")
            st.write("Impacto parser:", validation.get("parser_impact", ""))
            st.write("Recomendación:", validation.get("recommendation", ""))
            st.json(validation)

    @staticmethod
    def _show_parser_result() -> None:
        """Render deterministic parser result."""
        if not st.session_state.get("cne_parser_ready"):
            return

        counts = st.session_state.get("cne_parser_counts")
        report = st.session_state.get("cne_parser_report")

        if not counts:
            return

        st.markdown("### 3. Resultado parser determinístico")

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Transmisión", counts.get("transmission", 0))
        col2.metric("Generación", counts.get("generation", 0))
        col3.metric("PMGD / DER", counts.get("der", 0))
        col4.metric("BESS", counts.get("bess", 0))
        col5.metric("Total", counts.get("total", 0))

        if report:
            with st.expander("Detalle del parser", expanded=False):
                st.json(report)

    @staticmethod
    def _show_population_preview_result() -> None:
        """Render population preview result or preview error."""
        preview_error = st.session_state.get("cne_population_preview_error")
        preview_traceback = st.session_state.get("cne_population_preview_traceback")

        if preview_error:
            st.markdown("### 4. Preview de población")
            st.error("No se pudo ejecutar el preview de población.")

            with st.expander("Ver detalle del error", expanded=True):
                st.code(preview_error)

            if preview_traceback:
                with st.expander("Ver traceback completo", expanded=False):
                    st.code(preview_traceback)

            return

        preview = st.session_state.get("cne_population_preview")

        if not preview:
            return

        st.markdown("### 4. Preview de población")

        total = preview.get("total", {})

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Procesados", total.get("processed", 0))
        col2.metric("Crear", total.get("created", 0))
        col3.metric("Actualizar", total.get("updated", 0))
        col4.metric("Sin cambios", total.get("unchanged", 0))
        col5.metric("Errores", total.get("errors", 0))

        warnings = total.get("warnings", [])
        errors = total.get("error_details", [])

        if errors:
            st.error("El preview contiene errores. No se puede poblar la BD.")
        elif warnings:
            st.warning("El preview contiene advertencias.")
        else:
            st.success("Preview sin errores.")

        with st.expander("Detalle preview de población", expanded=False):
            st.json(preview)

    @staticmethod
    def _show_population_result() -> None:
        """Render committed population result."""
        result = st.session_state.get("cne_population_result")

        if not result:
            return

        st.markdown("### 5. Resultado población BD")

        total = result.get("total", {})

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Procesados", total.get("processed", 0))
        col2.metric("Creados", total.get("created", 0))
        col3.metric("Actualizados", total.get("updated", 0))
        col4.metric("Sin cambios", total.get("unchanged", 0))
        col5.metric("Errores", total.get("errors", 0))

        if total.get("errors", 0):
            st.error("La población terminó con errores.")
        else:
            st.success("Población confirmada en base de datos.")

        with st.expander("Detalle población BD", expanded=False):
            st.json(result)

    # ------------------------------------------------------------------
    # Disabled future uploaders
    # ------------------------------------------------------------------

    @staticmethod
    def _render_disabled_connection_uploads() -> None:
        """Render disabled connection uploaders reserved for future use."""
        st.caption("─── Archivos de Conexión ───")

        with st.expander(
            "📂 Conexión — Proyectos con Entrada en Operación",
            expanded=False,
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
            "📂 Conexión — Proyectos Declarados en Construcción",
            expanded=False,
        ):
            st.caption("🔒 Función no habilitada")
            st.file_uploader(
                "Proyectos Declarados en Construcción (.xlsx)",
                type=["xlsx", "xlsm", "xls"],
                key="conexion_construccion_uploader",
                label_visibility="collapsed",
                disabled=True,
            )
