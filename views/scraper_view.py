from __future__ import annotations

import threading

import pandas as pd
import streamlit as st

from services.project_data_service import ProjectDataService
from services.scraper_service import run_web_scraping, validate_scraper_reference_data
from views.project_view import ProjectView


class ScraperView:
    """Web scraper UI for PGP and SEO enrichment."""

    @staticmethod
    def render_web_scraper_panel() -> None:
        st.markdown(
            """
### Actualización desde fuentes web

Ejecuta enriquecimiento desde PGP y Seguimiento Ejecución de Obras. El avance muestra qué procesador está trabajando cada proyecto.
""",
            unsafe_allow_html=True,
        )

        with st.expander("Ejecutar scraper PGP / SEO", expanded=False):
            validation_column, source_column, limit_column, workers_column = st.columns(
                [1.0, 1.0, 0.85, 0.85]
            )

            with validation_column:
                validate_now = st.button(
                    "Validar datos base",
                    use_container_width=True,
                    key="validate_scraper_reference_data_button",
                )

            with source_column:
                source = st.selectbox(
                    "Fuente",
                    options=["pgp", "seo", "all"],
                    format_func=lambda value: {
                        "pgp": "PGP",
                        "seo": "SEO",
                        "all": "PGP + SEO",
                    }[value],
                    key="scraper_source",
                )

            with limit_column:
                limit = st.number_input(
                    "Límite",
                    min_value=1,
                    max_value=500,
                    value=5,
                    step=1,
                    key="scraper_limit",
                )

            with workers_column:
                workers = st.number_input(
                    "Procesadores",
                    min_value=1,
                    max_value=4,
                    value=1,
                    step=1,
                    key="scraper_workers",
                    help=(
                        "Número de procesadores simultáneos. PGP usa hasta 4; "
                        "SEO normalmente conviene entre 1 y 3 por estabilidad."
                    ),
                )

            selected_project_types = ScraperView.render_project_type_selector(source)

            mode_column, pause_column = st.columns([1.0, 1.0])
            with mode_column:
                update_existing = st.checkbox(
                    "Actualizar registros existentes",
                    value=False,
                    key="scraper_update_existing",
                    help=(
                        "Si está desactivado, el scraper procesa solo proyectos "
                        "con datos objetivo faltantes."
                    ),
                )
            with pause_column:
                sleep_between_requests = st.checkbox(
                    "Pausar entre solicitudes",
                    value=True,
                    key="scraper_sleep_between_requests",
                    help="Reduce el riesgo de saturar los sitios consultados.",
                )

            if validate_now:
                ScraperView.render_scraper_validation()

            run_button = st.button(
                "Actualizar información desde PGP / SEO",
                type="primary",
                use_container_width=True,
                key="run_web_scraper_button",
            )
            if run_button:
                ScraperView.run_scraper(
                    source=source,
                    limit=int(limit),
                    project_types=selected_project_types,
                    update_existing=update_existing,
                    sleep_between_requests=sleep_between_requests,
                    workers=int(workers),
                )

    @staticmethod
    def render_project_type_selector(source: str) -> list[str]:
        project_type_labels = {
            "transmission": "Transmisión",
            "generation": "Generación",
            "bess": "Almacenamiento / BESS",
        }
        if source == "seo":
            st.info("SEO solo se ejecuta sobre proyectos de transmisión.")
            return ["transmission"]

        available_options = ["transmission", "generation", "bess"]
        selected_project_types = st.multiselect(
            "Tipos de proyecto para PGP",
            options=available_options,
            default=["transmission"],
            format_func=lambda value: project_type_labels[value],
            key="scraper_project_types",
            help="PGP puede procesar transmisión, generación y almacenamiento.",
        )

        if source == "all":
            st.caption(
                "PGP usará los tipos seleccionados. "
                "SEO se ejecutará solo sobre transmisión."
            )
            if "transmission" not in selected_project_types:
                st.warning(
                    "Seleccionaste PGP + SEO, pero no marcaste transmisión. "
                    "SEO se ejecutará igualmente solo sobre transmisión."
                )

        if not selected_project_types:
            st.warning("Selecciona al menos un tipo de proyecto para PGP.")

        return selected_project_types

    @staticmethod
    def render_scraper_validation() -> None:
        try:
            validation = validate_scraper_reference_data()
            missing_sources = validation.get("missing_sources", [])
            missing_milestones = validation.get("missing_milestones", [])

            if missing_sources or missing_milestones:
                st.error("Faltan datos base para ejecutar el scraper.")
                if missing_sources:
                    st.write("Fuentes faltantes:", missing_sources)
                if missing_milestones:
                    st.write("Hitos faltantes:", missing_milestones)
            else:
                st.success("Datos base válidos. El scraper puede ejecutarse.")
        except Exception as error:
            st.error("No se pudieron validar los datos base del scraper.")
            st.exception(error)

    @staticmethod
    def run_scraper(
        source: str,
        limit: int,
        project_types: list[str],
        update_existing: bool,
        sleep_between_requests: bool,
        workers: int,
    ) -> None:
        if source in ["pgp", "all"] and not project_types:
            st.error("Selecciona al menos un tipo de proyecto para PGP.")
            return

        progress_bar = st.progress(0)
        status_placeholder = st.empty()
        processor_placeholder = st.empty()
        result_placeholder = st.empty()
        processor_events = {}
        progress_lock = threading.Lock()

        def handle_progress(event: dict) -> None:
            processor_id = event.get("processor_id", event.get("worker_id", 1))
            source_name = str(event.get("source", "")).upper()
            index = event.get("index", 0) or 0
            total = event.get("total", 0) or 0
            status = event.get("status", "")
            project_id = event.get("project_id", "")
            project_name = event.get("project_name", "")
            project_type = event.get("project_type", "")
            message = event.get("message", "")
            search_mode = event.get("search_mode") or ""
            search_term = event.get("search_term") or ""

            with progress_lock:
                is_individual = processor_id != "all"
                if is_individual:
                    processor_key = str(processor_id)
                    processor_events[processor_key] = {
                        "Procesador": processor_key,
                        "Fuente": source_name,
                        "Avance": f"{index}/{total}",
                        "Estado": status,
                        "ProjectID": project_id,
                        "Tipo": project_type,
                        "Proyecto": project_name,
                        "ModoBusqueda": search_mode,
                        "TerminoBusqueda": search_term,
                        "Mensaje": message,
                    }

                try:
                    if total > 0:
                        progress_bar.progress(min(index / total, 1.0))

                    parts = [f"[{source_name}] Procesador {processor_id}"]
                    if total > 0:
                        parts.append(f"{index}/{total}")
                    if project_name:
                        type_label = f" [{project_type}]" if project_type else ""
                        parts.append(f"{project_name}{type_label}")
                    if search_mode or search_term:
                        search_info = search_mode
                        if search_term:
                            search_info += f': "{search_term}"'
                        parts.append(f"búsqueda {search_info}")
                    if message:
                        parts.append(f"→ {message}")

                    status_placeholder.info(" | ".join(parts))

                    if processor_events:
                        processor_df = pd.DataFrame(
                            list(processor_events.values())
                        ).sort_values("Procesador")
                        processor_placeholder.dataframe(
                            processor_df,
                            width="stretch",
                            hide_index=True,
                            column_config=ProjectView.build_column_config(processor_df),
                        )
                except Exception:
                    pass

        try:
            with st.spinner("Ejecutando actualización web..."):
                result = run_web_scraping(
                    source=source,
                    limit=limit,
                    project_types=project_types,
                    update_existing=update_existing,
                    progress_callback=handle_progress,
                    sleep_between_requests=sleep_between_requests,
                    workers=workers,
                )

            progress_bar.progress(1.0)
            status_placeholder.success("Actualización web completada.")
            ProjectDataService.clear_loaded_data()

            metric_total, metric_success, metric_failed = st.columns(3)
            with metric_total:
                st.metric("Procesados", result.get("total", 0))
            with metric_success:
                st.metric("Actualizados", result.get("success", 0))
            with metric_failed:
                st.metric("Sin actualización / error", result.get("failed", 0))

            items = result.get("items", [])
            if items:
                result_df = pd.DataFrame(items)
                result_placeholder.dataframe(
                    result_df,
                    width="stretch",
                    hide_index=True,
                    column_config=ProjectView.build_column_config(result_df),
                )

            st.info(
                "La base de datos fue actualizada. Si la tabla principal no cambia "
                "de inmediato, recarga la página de Streamlit."
            )
        except TypeError as error:
            progress_bar.empty()
            status_placeholder.error("El scraper falló por un error de tipo.")
            st.exception(error)
        except Exception as error:
            progress_bar.empty()
            status_placeholder.error("El scraper no pudo finalizar correctamente.")
            st.exception(error)
