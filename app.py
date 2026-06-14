import streamlit as st

from services.project_data_service import ProjectDataService
from ui.app_config import AppConfig
from ui.app_style import AppStyle
from views.cne_ingestion_view import CNEIngestionView
from views.db_status_view import DBStatusView
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

        # Top 3-column bar: always rendered, independent of data loading.
        # col_left  → project summary metrics
        # col_mid   → DB status + verification
        # col_right → CNE file upload
        col_left, col_mid, col_right = st.columns([2, 1, 2], gap="large")

        with col_mid:
            DBStatusView.render_status_panel()

        with col_right:
            CNEIngestionView.render_cne_panel_column()

        try:
            df = ProjectDataService.load_projects()
            features_df = ProjectDataService.load_project_features()
            dates_df = ProjectDataService.load_project_dates()
            legal_documents_df = ProjectDataService.load_project_legal_documents()

            project_types = ProjectDataService.get_available_project_types(df)

            with col_left:
                ProjectView.render_summary_panel(df, project_types)
                ProjectView.render_export_button(
                    df, features_df, legal_documents_df, dates_df
                )

            # Full-width sections below the 3-column bar
            ScraperView.render_web_scraper_panel()
            ProjectView.render_project_tabs(
                df=df,
                project_types=project_types,
                features_df=features_df,
                dates_df=dates_df,
                legal_documents_df=legal_documents_df,
            )

        except Exception as error:
            with col_left:
                st.error("No se pudo cargar la información de proyectos.")
            ProjectView.render_error(error)


if __name__ == "__main__":
    app = ProjectApp()
    app.run()
