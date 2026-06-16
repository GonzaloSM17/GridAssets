import pandas as pd
import streamlit as st

from html import escape

from services.project_data_service import ProjectDataService
from views.project_edit_view import ProjectEditView
from views.project_modeling_view import ProjectModelingView
from views.project_table_utils import ProjectTableUtils


class ProjectDetailView:
    """Right-side selected-project detail panel."""

    FEATURE_HIDDEN_FIELDS = {
        "ProjectID",
        "ProjectType",
        "Project Type",
        "project_type",
        "project_discriminator",
        "ProjectDiscriminator",
        "Type",
        "type",
        "TechnologyGroup",
        "technology_group",
    }

    @staticmethod
    def render_empty_detail_panel() -> None:
        st.markdown(
            """
            <div class="detail-card">
                <h4>Detalle del proyecto</h4>
                <p>
                    Selecciona una fila de la tabla para desplegar sus características,
                    documentos legales y fechas relevantes.
                </p>
                <p><strong>No hay proyecto seleccionado.</strong></p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    @staticmethod
    def render_selected_project_header(
        selected_project_id: int,
        selected_project_name: str,
    ) -> None:
        st.markdown(
            f"""
            <div class="detail-card selected-project-card">
                <h4>Detalle del proyecto</h4>
                <p><strong>{selected_project_id}</strong> - {selected_project_name}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    @staticmethod
    def render_project_detail(
        selected_project_id: int,
        current_nup: int | None,
        features_df: pd.DataFrame,
        dates_df: pd.DataFrame,
        legal_documents_df: pd.DataFrame,
    ) -> None:
        project_features = ProjectDataService.filter_by_project_id(
            features_df,
            selected_project_id,
        )
        project_dates = ProjectDataService.filter_by_project_id(
            dates_df,
            selected_project_id,
        )
        project_legal_documents = ProjectDataService.filter_by_project_id(
            legal_documents_df,
            selected_project_id,
        )

        project_features = ProjectDataService.clean_empty_columns(project_features)
        project_dates = ProjectDataService.clean_empty_columns(project_dates)
        project_legal_documents = ProjectDataService.clean_empty_columns(
            project_legal_documents
        )

        detail_tabs = st.tabs(
            [
                "Características",
                "Documentos",
                "Fechas",
                "Modelación",
                "✏️ Editar",
            ]
        )

        with detail_tabs[0]:
            ProjectDetailView._render_features_tab(project_features)

        with detail_tabs[1]:
            ProjectDetailView._render_documents_tab(project_legal_documents)

        with detail_tabs[2]:
            ProjectDetailView._render_dates_tab(project_dates)

        with detail_tabs[3]:
            ProjectModelingView.render_modeling_editor(project_id=selected_project_id)

        with detail_tabs[4]:
            ProjectEditView.render_nup_editor(
                project_id=selected_project_id,
                current_nup=current_nup,
            )
            st.divider()
            ProjectEditView.render_date_editor(
                project_id=selected_project_id,
                project_dates=project_dates,
            )

    @staticmethod
    def _render_features_tab(project_features: pd.DataFrame) -> None:
        empty_message = "Empty: no hay características adicionales para mostrar."

        if project_features.empty:
            st.empty()
            st.info(empty_message)
            return

        ProjectTableUtils.render_vertical_record(
            project_features,
            hidden_fields=ProjectDetailView.FEATURE_HIDDEN_FIELDS,
            empty_message=empty_message,
        )

    @staticmethod
    def _render_documents_tab(project_legal_documents: pd.DataFrame) -> None:
        if project_legal_documents.empty:
            st.info("No legal documents are available for this project.")
            return

        st.dataframe(
            project_legal_documents,
            width="stretch",
            height=280,
            hide_index=True,
            column_config=ProjectTableUtils.build_column_config(
                project_legal_documents
            ),
        )

    @staticmethod
    def _render_dates_tab(project_dates: pd.DataFrame) -> None:
        """Render project dates table with compact column widths."""
        if project_dates.empty:
            st.info("No relevant dates are available for this project.")
            return

        visible_columns = [
            column
            for column in [
                "MilestoneName",
                "DateValue",
                "SourceName",
                "ExtractedAt",
                "URL",
            ]
            if column in project_dates.columns
        ]

        if not visible_columns:
            st.info("No relevant dates are available for this project.")
            return

        dates_view = project_dates[visible_columns].copy()

        column_config = {
            "MilestoneName": st.column_config.TextColumn(
                "Hito",
                width="medium",
            ),
            "DateValue": st.column_config.DateColumn(
                "Fecha",
                width="small",
                format="DD-MM-YYYY",
            ),
            "SourceName": st.column_config.TextColumn(
                "Fuente",
                width="small",
            ),
            "ExtractedAt": st.column_config.DatetimeColumn(
                "Extraído",
                width="small",
                format="DD-MM-YYYY",
            ),
            "URL": st.column_config.LinkColumn(
                "Link",
                width="small",
                display_text="Abrir",
            ),
        }

        st.dataframe(
            dates_view,
            width="stretch",
            height=min(280, 38 + 35 * max(len(dates_view), 1)),
            hide_index=True,
            column_config={
                column: config
                for column, config in column_config.items()
                if column in dates_view.columns
            },
        )
