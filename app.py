from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from services.project_data_service import ProjectDataService
from ui.app_config import AppConfig
from ui.app_style import AppStyle
from views.cne_ingestion_view import CNEIngestionView
from views.db_status_view import DBStatusView
from views.electrical_model_view import ElectricalModelView
from views.project_view import ProjectView
from views.scraper_view import ScraperView


class ProjectApp:
    """Main Streamlit application orchestrator."""

    def __init__(self) -> None:
        st.set_page_config(
            page_title=AppConfig.PAGE_TITLE,
            page_icon=AppConfig.PAGE_ICON,
            layout="wide",
        )

    def run(self) -> None:
        """Render the app, gating project data loads until the DB is ready."""
        AppStyle.apply()
        ProjectView.render_header()

        db_status = DBStatusView.get_status()
        db_ready = DBStatusView.is_ready(db_status)

        if not db_ready:
            self._render_top_row(
                db_ready=False,
                df=None,
                project_types=[],
                features_df=None,
                dates_df=None,
                legal_documents_df=None,
            )
            st.divider()
            st.info(
                "Primero valida o crea la base de datos y el schema desde "
                "Gestión de base de datos. Luego se cargarán los proyectos."
            )
            return

        try:
            df = ProjectDataService.load_projects()
            features_df = ProjectDataService.load_project_features()
            dates_df = ProjectDataService.load_project_dates()
            legal_documents_df = ProjectDataService.load_project_legal_documents()
            project_types = ProjectDataService.get_available_project_types(df)

            self._render_top_row(
                db_ready=True,
                df=df,
                project_types=project_types,
                features_df=features_df,
                dates_df=dates_df,
                legal_documents_df=legal_documents_df,
            )

            st.divider()
            ScraperView.render_web_scraper_panel()

            ProjectView.render_project_tabs(
                df=df,
                project_types=project_types,
                features_df=features_df,
                dates_df=dates_df,
                legal_documents_df=legal_documents_df,
            )
        except Exception as error:
            st.error("No se pudo cargar la información de proyectos.")
            ProjectView.render_error(error)

    @staticmethod
    def _render_top_row(
        db_ready: bool,
        df: Optional[pd.DataFrame],
        project_types: list[str],
        features_df: Optional[pd.DataFrame],
        dates_df: Optional[pd.DataFrame],
        legal_documents_df: Optional[pd.DataFrame],
    ) -> None:
        """Render summary, database management and electrical model panels."""
        summary_col, db_col, model_col = st.columns([1, 1, 1], gap="large")

        with summary_col:
            with st.expander(" Resumen", expanded=True):
                if db_ready and df is not None:
                    ProjectView.render_summary_panel(df, project_types)
                    st.divider()
                    ProjectView.render_export_button(
                        df,
                        features_df if features_df is not None else pd.DataFrame(),
                        (
                            legal_documents_df
                            if legal_documents_df is not None
                            else pd.DataFrame()
                        ),
                        dates_df if dates_df is not None else pd.DataFrame(),
                    )
                else:
                    st.info("Resumen disponible cuando la base esté operativa.")

        with db_col:
            with st.expander(" Gestión de base de datos", expanded=True):
                st.caption(
                    "Verificación de conexión, creación de esquema y carga "
                    "de archivos CNE."
                )
                st.divider()
                DBStatusView.render_status_panel()
                st.divider()

                if db_ready:
                    CNEIngestionView.render_cne_panel_column()
                else:
                    st.info(
                        "Carga CNE disponible después de crear la base y el schema."
                    )

        with model_col:
            if db_ready:
                ElectricalModelView.render_electrical_model_management_panel(
                    compact=True,
                    expanded=True,
                )
            else:
                with st.expander(" Gestión modelos eléctricos", expanded=True):
                    st.info(
                        "Gestión de modelos disponible cuando la base esté operativa."
                    )


if __name__ == "__main__":
    app = ProjectApp()
    app.run()
