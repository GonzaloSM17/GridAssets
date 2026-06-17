from __future__ import annotations

import pandas as pd
import streamlit as st

from services.project_data_service import ProjectDataService
from ui.app_config import AppConfig
from views.project_detail_view import ProjectDetailView
from views.project_edit_view import ProjectEditView
from views.project_modeling_view import ProjectModelingView
from views.project_table_utils import ProjectTableUtils


class ProjectView:
    """Main project dashboard view."""

    @staticmethod
    def get_project_type_label(project_type: str) -> str:
        return AppConfig.PROJECT_TYPE_LABELS.get(
            project_type,
            str(project_type).title(),
        )

    @staticmethod
    def render_header() -> None:
        st.markdown(
            f"""
# {AppConfig.APP_TITLE}

{AppConfig.APP_SUBTITLE}
""",
            unsafe_allow_html=True,
        )

    @staticmethod
    def render_summary(df: pd.DataFrame, project_types: list[str]) -> None:
        metric_columns = st.columns(len(project_types) + 1)
        with metric_columns[0]:
            st.metric("Total", len(df))

        for column, project_type in zip(metric_columns[1:], project_types):
            with column:
                project_count = len(df[df["project_discriminator"] == project_type])
                st.metric(
                    ProjectView.get_project_type_label(project_type),
                    project_count,
                )

    @staticmethod
    def render_summary_panel(df: pd.DataFrame, project_types: list[str]) -> None:
        """Render a compact project summary for the top-row column."""
        st.markdown("### Resumen de proyectos")
        st.metric("Total proyectos", len(df))

        pairs = [project_types[i : i + 2] for i in range(0, len(project_types), 2)]
        for pair in pairs:
            sub_cols = st.columns(len(pair))
            for col, project_type in zip(sub_cols, pair):
                with col:
                    count = len(df[df["project_discriminator"] == project_type])
                    st.metric(
                        ProjectView.get_project_type_label(project_type),
                        count,
                    )

    @staticmethod
    def render_export_button(
        overview_df: pd.DataFrame,
        features_df: pd.DataFrame,
        legal_documents_df: pd.DataFrame,
        dates_df: pd.DataFrame,
    ) -> None:
        """Render the Excel export controls."""
        from services.excel_export_service import (
            build_projects_excel,
            list_electrical_models_for_export,
            suggested_filename,
        )

        st.markdown("#### Exportar")
        st.caption(
            "Exporta una planilla Excel con una vista macro del portafolio, una vista general de proyectos, hojas separadas por tipo, fechas y documentos."
        )

        models_df = list_electrical_models_for_export()
        selected_electrical_model_id = None

        include_electrical_modeling = st.checkbox(
            "Incluir modelo eléctrico",
            value=False,
            key="export_include_electrical_modeling",
            help=(
                "Permite seleccionar un modelo eléctrico y agrega una columna True/False "
                "indicando si cada proyecto está o estará considerado en la modelación."
            ),
        )

        if include_electrical_modeling:
            if models_df.empty:
                st.info("No hay modelos eléctricos activos disponibles para exportar.")
            else:
                export_models = models_df.copy()
                export_models["OptionLabel"] = (
                    export_models["SoftwareName"].astype(str)
                    + " - "
                    + export_models["ElectricalModelName"].astype(str)
                )
                model_options = {
                    row["OptionLabel"]: int(row["ElectricalModelID"])
                    for _, row in export_models.iterrows()
                }
                selected_label = st.selectbox(
                    "Modelo eléctrico",
                    options=list(model_options.keys()),
                    key="export_selected_electrical_model",
                )
                selected_electrical_model_id = model_options.get(selected_label)

        data = build_projects_excel(
            overview_df=overview_df,
            features_df=features_df,
            documents_df=legal_documents_df,
            dates_df=dates_df,
            include_electrical_modeling=include_electrical_modeling,
            electrical_model_id=selected_electrical_model_id,
        )
        st.download_button(
            label="Exportar Excel",
            data=data,
            file_name=suggested_filename(),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            help=(
                "Incluye Summary, Overview_All, hojas por tipo, Dates y Documents. "
                "Export_Info aparece solo si se selecciona un modelo eléctrico."
            ),
        )

    @staticmethod
    def render_project_tabs(
        df: pd.DataFrame,
        project_types: list[str],
        features_df: pd.DataFrame,
        dates_df: pd.DataFrame,
        legal_documents_df: pd.DataFrame,
    ) -> None:
        tab_labels = [
            ProjectView.get_project_type_label(project_type)
            for project_type in project_types
        ]
        tabs = st.tabs(tab_labels)
        for tab, project_type in zip(tabs, project_types):
            with tab:
                ProjectView._render_project_type_tab(
                    df=df,
                    project_type=project_type,
                    features_df=features_df,
                    dates_df=dates_df,
                    legal_documents_df=legal_documents_df,
                )

    @staticmethod
    def _render_project_type_tab(
        df: pd.DataFrame,
        project_type: str,
        features_df: pd.DataFrame,
        dates_df: pd.DataFrame,
        legal_documents_df: pd.DataFrame,
    ) -> None:
        filtered_df = ProjectDataService.filter_by_project_type(df, project_type)
        display_df = ProjectDataService.prepare_display_dataframe(filtered_df)

        st.markdown(f"""
### {ProjectView.get_project_type_label(project_type)}

{len(display_df)} proyectos registrados en esta categoría.
Selecciona una fila para revisar su detalle en el panel derecho.
""")

        if display_df.empty:
            st.info("No hay proyectos disponibles para esta categoría.")
            return

        table_df = ProjectView._apply_filters(display_df, project_type)
        table_df = ProjectTableUtils.select_project_type_columns(
            table_df,
            project_type=project_type,
            include_reference_columns=True,
        )
        table_df = table_df.reset_index(drop=True)

        table_column, detail_column = st.columns([2.35, 1.15], gap="large")

        with table_column:
            table_event = st.dataframe(
                table_df,
                width="stretch",
                height=590,
                hide_index=True,
                column_config=ProjectTableUtils.build_column_config(table_df),
                selection_mode="single-row",
                on_select="rerun",
                key=f"projects_table_{project_type}",
            )

        with detail_column:
            ProjectView._render_selected_project_detail(
                table_df=table_df,
                selected_rows=table_event.selection.rows,
                project_type=project_type,
                features_df=features_df,
                dates_df=dates_df,
                legal_documents_df=legal_documents_df,
            )

    @staticmethod
    def _apply_filters(display_df: pd.DataFrame, project_type: str) -> pd.DataFrame:
        fc1, fc2, fc3 = st.columns([3, 2, 1])

        with fc1:
            name_filter = st.text_input(
                "Buscar",
                key=f"filter_name_{project_type}",
                placeholder="Buscar por nombre...",
                label_visibility="collapsed",
            )

        with fc2:
            entity_options = (
                sorted(display_df["ProjectEntityName"].dropna().unique().tolist())
                if "ProjectEntityName" in display_df.columns
                else []
            )
            entity_filter = st.multiselect(
                "Titular",
                options=entity_options,
                key=f"filter_entity_{project_type}",
                placeholder="Filtrar por titular...",
                label_visibility="collapsed",
            )

        with fc3:
            no_nup_filter = st.checkbox(
                "Sin NUP",
                key=f"filter_no_nup_{project_type}",
                help="Mostrar solo proyectos sin NUP asignado.",
            )

        table_df = display_df.copy()
        if name_filter:
            table_df = table_df[
                table_df["ProjectName"].str.contains(
                    name_filter,
                    case=False,
                    na=False,
                )
            ]
        if entity_filter:
            table_df = table_df[table_df["ProjectEntityName"].isin(entity_filter)]
        if no_nup_filter and "NUP" in table_df.columns:
            table_df = table_df[table_df["NUP"].isna()]
        return table_df

    @staticmethod
    def _render_selected_project_detail(
        table_df: pd.DataFrame,
        selected_rows: list[int],
        project_type: str,
        features_df: pd.DataFrame,
        dates_df: pd.DataFrame,
        legal_documents_df: pd.DataFrame,
    ) -> None:
        if not selected_rows:
            ProjectDetailView.render_empty_detail_panel()
            return

        selected_row_position = selected_rows[0]
        selected_project_id = int(table_df.iloc[selected_row_position]["ProjectID"])
        selected_project_name = str(table_df.iloc[selected_row_position]["ProjectName"])

        nup_raw = (
            table_df.iloc[selected_row_position]["NUP"]
            if "NUP" in table_df.columns
            else None
        )
        current_nup = (
            int(nup_raw) if nup_raw is not None and pd.notna(nup_raw) else None
        )

        status_raw = (
            table_df.iloc[selected_row_position]["StatusName"]
            if "StatusName" in table_df.columns
            else None
        )
        current_status = (
            str(status_raw).strip()
            if status_raw is not None
            and pd.notna(status_raw)
            and str(status_raw).strip()
            else None
        )

        current_project_type = str(project_type or "").strip().lower() or None

        ProjectDetailView.render_selected_project_header(
            selected_project_id=selected_project_id,
            selected_project_name=selected_project_name,
        )
        ProjectDetailView.render_project_detail(
            selected_project_id=selected_project_id,
            current_nup=current_nup,
            current_status=current_status,
            current_project_type=current_project_type,
            features_df=features_df,
            dates_df=dates_df,
            legal_documents_df=legal_documents_df,
        )

    # Backward-compatible aliases for older imports/calls.
    render_empty_detail_panel = staticmethod(
        ProjectDetailView.render_empty_detail_panel
    )
    render_selected_project_header = staticmethod(
        ProjectDetailView.render_selected_project_header
    )
    render_project_detail = staticmethod(ProjectDetailView.render_project_detail)
    render_date_editor = staticmethod(ProjectEditView.render_date_editor)
    render_nup_editor = staticmethod(ProjectEditView.render_nup_editor)
    render_modeling_editor = staticmethod(ProjectModelingView.render_modeling_editor)
    render_vertical_record = staticmethod(ProjectTableUtils.render_vertical_record)
    build_column_config = staticmethod(ProjectTableUtils.build_column_config)

    @staticmethod
    def render_error(error: Exception) -> None:
        st.error("Ocurrió un error al cargar la información de proyectos.")
        st.exception(error)
