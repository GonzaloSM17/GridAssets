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
    def __init__(self) -> None:
        st.set_page_config(
            page_title=AppConfig.PAGE_TITLE,
            page_icon=AppConfig.PAGE_ICON,
            layout="wide",
        )

    def run(self) -> None:
        AppStyle.apply()
        ProjectView.render_header()

        try:
            df = ProjectDataService.load_projects()
            features_df = ProjectDataService.load_project_features()
            dates_df = ProjectDataService.load_project_dates()
            legal_documents_df = ProjectDataService.load_project_legal_documents()
            project_types = ProjectDataService.get_available_project_types(df)

            # ------------------------------------------------------------------
            # Row 1: summary/export, database management and model management
            # ------------------------------------------------------------------
            summary_col, db_col, model_col = st.columns([1, 1, 1], gap="large")

            with summary_col:
                with st.expander("📊 Resumen", expanded=True):
                    ProjectView.render_summary_panel(df, project_types)
                    st.divider()
                    ProjectView.render_export_button(
                        df,
                        features_df,
                        legal_documents_df,
                        dates_df,
                    )

            with db_col:
                with st.expander("📦 Gestión de base de datos", expanded=True):
                    st.caption(
                        "Verificación de conexión, creación de esquema y carga "
                        "de archivos CNE."
                    )
                    st.divider()
                    DBStatusView.render_status_panel()
                    st.divider()
                    CNEIngestionView.render_cne_panel_column()

            with model_col:
                ElectricalModelView.render_electrical_model_management_panel(
                    compact=True,
                    expanded=True,
                )

            st.divider()

            # ------------------------------------------------------------------
            # Row 2: web update section
            # ------------------------------------------------------------------
            ScraperView.render_web_scraper_panel()

            # ------------------------------------------------------------------
            # Row 3: project table and detail
            # ------------------------------------------------------------------
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


if __name__ == "__main__":
    app = ProjectApp()
    app.run()
