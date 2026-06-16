import pandas as pd
import streamlit as st

from services.project_data_service import ProjectDataService
from ui.app_config import AppConfig


class ProjectView:
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
            <div class="header-shell">
                <div class="main-title">{AppConfig.APP_TITLE}</div>
                <div class="subtitle">{AppConfig.APP_SUBTITLE}</div>
                <div class="header-accent"></div>
            </div>
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
        """Project summary designed for use inside a column."""
        st.markdown(
            """
            <div class="section-card section-card-blue">
                <div class="section-title">Resumen de proyectos</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.metric("Total proyectos", len(df))

        # Render type counts in pairs (2 per row) to fit column width
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
        """Download button that exports all project data as a per-type Excel."""
        from services.excel_export_service import (
            build_projects_excel,
            suggested_filename,
        )

        data = build_projects_excel(
            overview_df, features_df, legal_documents_df, dates_df
        )

        st.download_button(
            label="📥 Exportar Excel",
            data=data,
            file_name=suggested_filename(),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            help="Una hoja por tipo de proyecto con todos los datos.",
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
                filtered_df = ProjectDataService.filter_by_project_type(
                    df,
                    project_type,
                )

                display_df = ProjectDataService.prepare_display_dataframe(filtered_df)

                st.markdown(
                    f"""
                    <div class="section-card section-card-blue">
                        <div class="section-title">
                            {ProjectView.get_project_type_label(project_type)}
                        </div>
                        <div class="section-caption">
                            {len(display_df)} proyectos registrados en esta categoría.
                            Selecciona una fila para revisar su detalle en el panel derecho.
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                if display_df.empty:
                    st.info("No projects are available for this category.")
                    continue

                # ── Filters ────────────────────────────────────────────
                fc1, fc2, fc3 = st.columns([3, 2, 1])

                with fc1:
                    name_filter = st.text_input(
                        "Buscar",
                        key=f"filter_name_{project_type}",
                        placeholder="🔍 Buscar por nombre...",
                        label_visibility="collapsed",
                    )

                with fc2:
                    entity_options = (
                        sorted(
                            display_df["ProjectEntityName"].dropna().unique().tolist()
                        )
                        if "ProjectEntityName" in display_df.columns
                        else []
                    )
                    entity_filter = st.multiselect(
                        "Entidad",
                        options=entity_options,
                        key=f"filter_entity_{project_type}",
                        placeholder="Filtrar por entidad...",
                        label_visibility="collapsed",
                    )

                with fc3:
                    no_nup_filter = st.checkbox(
                        "Sin NUP",
                        key=f"filter_no_nup_{project_type}",
                        help="Mostrar solo proyectos sin NUP asignado",
                    )

                # Apply filters
                table_df = display_df.copy()
                if name_filter:
                    table_df = table_df[
                        table_df["ProjectName"].str.contains(
                            name_filter, case=False, na=False
                        )
                    ]
                if entity_filter:
                    table_df = table_df[
                        table_df["ProjectEntityName"].isin(entity_filter)
                    ]
                if no_nup_filter and "NUP" in table_df.columns:
                    table_df = table_df[table_df["NUP"].isna()]

                # Reset index so iloc positions match visual row positions
                table_df = table_df.reset_index(drop=True)

                # ── Table + detail panel ────────────────────────────────
                table_column, detail_column = st.columns([2.35, 1.15], gap="large")

                with table_column:
                    table_event = st.dataframe(
                        table_df,
                        width="stretch",
                        height=590,
                        hide_index=True,
                        column_config=ProjectView.build_column_config(table_df),
                        selection_mode="single-row",
                        on_select="rerun",
                        key=f"projects_table_{project_type}",
                    )

                selected_rows = table_event.selection.rows

                with detail_column:
                    if not selected_rows:
                        ProjectView.render_empty_detail_panel()
                        continue

                    selected_row_position = selected_rows[0]

                    selected_project_id = int(
                        table_df.iloc[selected_row_position]["ProjectID"]
                    )

                    selected_project_name = str(
                        table_df.iloc[selected_row_position]["ProjectName"]
                    )

                    nup_raw = (
                        table_df.iloc[selected_row_position]["NUP"]
                        if "NUP" in table_df.columns
                        else None
                    )
                    current_nup = (
                        int(nup_raw)
                        if nup_raw is not None and pd.notna(nup_raw)
                        else None
                    )

                    ProjectView.render_selected_project_header(
                        selected_project_id=selected_project_id,
                        selected_project_name=selected_project_name,
                    )

                    ProjectView.render_project_detail(
                        selected_project_id=selected_project_id,
                        current_nup=current_nup,
                        features_df=features_df,
                        dates_df=dates_df,
                        legal_documents_df=legal_documents_df,
                    )

    @staticmethod
    def render_empty_detail_panel() -> None:
        st.markdown(
            """
            <div class="detail-panel">
                <div class="section-title">Detalle del proyecto</div>
                <div class="section-caption">
                    Selecciona una fila de la tabla para desplegar sus características,
                    documentos legales y fechas relevantes.
                </div>
                <br>
                <div class="detail-empty">
                    No hay proyecto seleccionado.
                </div>
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
            <div class="section-card section-card-green">
                <div class="section-title">Detalle del proyecto</div>
                <div class="section-caption">
                    {selected_project_id} - {selected_project_name}
                </div>
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
            if project_features.empty:
                st.info("No feature data is available for this project.")
            else:
                ProjectView.render_vertical_record(project_features)

        with detail_tabs[1]:
            if project_legal_documents.empty:
                st.info("No legal documents are available for this project.")
            else:
                st.dataframe(
                    project_legal_documents,
                    width="stretch",
                    height=280,
                    hide_index=True,
                    column_config=ProjectView.build_column_config(
                        project_legal_documents
                    ),
                )

        with detail_tabs[2]:
            if project_dates.empty:
                st.info("No relevant dates are available for this project.")
            else:
                st.dataframe(
                    project_dates,
                    width="stretch",
                    height=280,
                    hide_index=True,
                    column_config=ProjectView.build_column_config(project_dates),
                )

        with detail_tabs[3]:
            ProjectView.render_modeling_editor(
                project_id=selected_project_id,
            )

        with detail_tabs[4]:
            ProjectView.render_nup_editor(
                project_id=selected_project_id,
                current_nup=current_nup,
            )
            st.divider()
            ProjectView.render_date_editor(
                project_id=selected_project_id,
                project_dates=project_dates,
            )

    @staticmethod
    def render_date_editor(
        project_id: int,
        project_dates: pd.DataFrame,
    ) -> None:
        """Form to add, update, or delete a RelevantDate with source='User'."""
        from services.project_edit_service import (
            get_milestone_types,
            update_project_date,
        )

        st.markdown("**Fechas de hito — fuente User**")
        st.caption(
            "Agrega o modifica fechas manualmente. Se guardan con fuente 'User'."
        )

        # Show existing User dates for this project
        if not project_dates.empty and "SourceName" in project_dates.columns:
            user_dates = project_dates[project_dates["SourceName"] == "User"]
            if not user_dates.empty:
                display_cols = [
                    c for c in ["MilestoneName", "DateValue"] if c in user_dates.columns
                ]
                st.dataframe(
                    user_dates[display_cols],
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.caption("Sin fechas de fuente 'User' para este proyecto.")
        else:
            st.caption("Sin fechas de fuente 'User' para este proyecto.")

        # Message from previous submission
        msg_key = f"date_msg_{project_id}"
        if msg_key in st.session_state:
            msg = st.session_state.pop(msg_key)
            if msg["type"] == "success":
                st.success(msg["text"])
            else:
                st.error(msg["text"])

        try:
            milestone_options = get_milestone_types()
        except Exception as exc:
            st.error(f"No se pudieron cargar los tipos de hito: {exc}")
            return

        with st.form(key=f"date_form_{project_id}"):
            milestone = st.selectbox(
                "Tipo de hito",
                options=milestone_options,
                label_visibility="collapsed",
            )
            date_value = st.date_input(
                "Fecha",
                value=None,
                format="DD/MM/YYYY",
                label_visibility="collapsed",
            )
            col_save, col_delete = st.columns(2)
            with col_save:
                submitted = st.form_submit_button(
                    "💾 Guardar fecha",
                    type="primary",
                    use_container_width=True,
                )
            with col_delete:
                delete = st.form_submit_button(
                    "🗑 Eliminar",
                    use_container_width=True,
                )

        if submitted or delete:
            new_date = date_value if submitted and date_value else None
            try:
                update_project_date(project_id, milestone, new_date)
                if new_date:
                    text = (
                        f"Fecha guardada: {milestone} → {new_date.strftime('%d/%m/%Y')}"
                    )
                else:
                    text = f"Fecha eliminada: {milestone}"
                st.session_state[msg_key] = {"type": "success", "text": f"✅ {text}"}
                ProjectDataService.clear_loaded_data()
                st.rerun()
            except Exception as exc:
                st.session_state[msg_key] = {"type": "error", "text": f"❌ {exc}"}
                st.rerun()

    @staticmethod
    def render_nup_editor(project_id: int, current_nup: int | None) -> None:
        """Form to manually set or clear the NUP of a project."""
        from services.project_edit_service import update_project_nup

        st.markdown("**Número Único de Proyecto (NUP)**")
        st.caption(
            "El NUP vincula el proyecto al portal PGP del Coordinador. "
            "Ingresa 0 para dejar el campo vacío."
        )

        if current_nup:
            st.info(f"NUP actual: **{current_nup}**")
        else:
            st.warning("Sin NUP asignado.")

        # Show success/error from a previous submission (survives st.rerun)
        msg_key = f"nup_msg_{project_id}"
        if msg_key in st.session_state:
            msg = st.session_state.pop(msg_key)
            if msg["type"] == "success":
                st.success(msg["text"])
            else:
                st.error(msg["text"])

        with st.form(key=f"nup_form_{project_id}"):
            nup_input = st.number_input(
                "Nuevo NUP",
                value=current_nup or 0,
                min_value=0,
                step=1,
                label_visibility="collapsed",
            )
            col_save, col_clear = st.columns(2)
            with col_save:
                submitted = st.form_submit_button(
                    "💾 Guardar",
                    type="primary",
                    use_container_width=True,
                )
            with col_clear:
                clear_nup = st.form_submit_button(
                    "🗑 Limpiar NUP",
                    use_container_width=True,
                )

        if submitted or clear_nup:
            new_nup = int(nup_input) if submitted and int(nup_input) > 0 else None
            try:
                update_project_nup(project_id, new_nup)
                text = f"NUP actualizado: **{new_nup}**" if new_nup else "NUP limpiado."
                st.session_state[msg_key] = {"type": "success", "text": f"✅ {text}"}
                ProjectDataService.clear_loaded_data()
                st.rerun()
            except Exception as exc:
                st.session_state[msg_key] = {
                    "type": "error",
                    "text": f"❌ Error: {exc}",
                }
                st.rerun()

    @staticmethod
    def render_vertical_record(df: pd.DataFrame) -> None:
        if df.empty:
            return

        record = df.iloc[0].to_dict()

        hidden_fields = {
            "ProjectID",
        }

        for field_name, field_value in record.items():
            if field_name in hidden_fields:
                continue

            if pd.isna(field_value):
                continue

            if str(field_value).strip() in ["", "None", "nan", "NaT"]:
                continue

            st.markdown(
                f"""
                <div style="
                    padding: 0.55rem 0.65rem;
                    margin-bottom: 0.45rem;
                    border-radius: 0.65rem;
                    background-color: {AppConfig.COLORS["surface"]};
                    border: 1px solid {AppConfig.COLORS["border"]};
                ">
                    <div style="
                        font-size: 0.75rem;
                        color: {AppConfig.COLORS["text_muted"]};
                        font-weight: 650;
                        margin-bottom: 0.15rem;
                    ">
                        {field_name}
                    </div>
                    <div style="
                        font-size: 0.92rem;
                        color: {AppConfig.COLORS["text"]};
                        font-weight: 500;
                        word-break: break-word;
                    ">
                        {field_value}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    @staticmethod
    def build_column_config(df: pd.DataFrame) -> dict:
        column_config = {}

        if df.empty:
            return column_config

        min_width = 90
        default_max_width = 380
        char_width = 8
        padding = 28

        custom_max_width = {
            "ProjectID": 90,
            "NUP": 110,
            "ProjectName": 520,
            "ProjectEntityName": 360,
            "LastMilestoneName": 220,
            "LastMilestoneSource": 180,
            "LastMilestoneDate": 160,
            "ProjectType": 120,
            "VoltageLevel": 140,
            "TransmissionTotalCapacity": 210,
            "BayName": 240,
            "Technology": 170,
            "PowerCapacity": 150,
            "GenerationTotalCapacity": 210,
            "StorageCapacity": 190,
            "Location": 240,
            "DocumentType": 170,
            "DocumentName": 360,
            "DocumentYear": 140,
            "DateValue": 130,
            "ExtractedAt": 130,
            "MilestoneName": 220,
            "SourceName": 160,
            "URL": 320,
            "PGP_URL": 320,
            "SEO_URL": 280,
        }

        # Columns rendered as clickable links
        link_columns = {"URL", "PGP_URL", "SEO_URL"}

        for column in df.columns:
            series_as_text = (
                df[column]
                .astype(str)
                .replace("None", "")
                .replace("nan", "")
                .replace("NaT", "")
            )

            max_content_length = int(series_as_text.map(len).max())
            header_length = len(str(column))

            estimated_width = int(
                max(header_length, max_content_length) * char_width + padding
            )

            max_width = custom_max_width.get(str(column), default_max_width)
            final_width = int(max(min_width, min(estimated_width, max_width)))

            if str(column) in link_columns:
                column_config[column] = st.column_config.LinkColumn(
                    label=str(column),
                    width=final_width,
                )
            else:
                column_config[column] = st.column_config.Column(
                    label=str(column),
                    width=final_width,
                )

        return column_config

    @staticmethod
    def render_modeling_editor(project_id: int) -> None:
        """Render editable project electrical modeling status."""

        from services.electrical_model_service import (
            get_project_modeling_status,
            update_project_modeling_status,
        )

        st.markdown("**Estado de modelación eléctrica**")
        st.caption(
            "Marca los modelos eléctricos donde este proyecto ya está representado."
        )

        msg_key = f"modeling_msg_{project_id}"

        if msg_key in st.session_state:
            msg = st.session_state.pop(msg_key)

            if msg["type"] == "success":
                st.success(msg["text"])
            else:
                st.error(msg["text"])

        try:
            status_df = get_project_modeling_status(project_id)
        except Exception as exc:
            st.error(f"No se pudo cargar el estado de modelación: {exc}")
            return

        if status_df.empty:
            st.info(
                "No hay modelos eléctricos activos. "
                "Agrega modelos desde el panel global de modelos eléctricos."
            )
            return

        editor_df = status_df.copy()

        edited_df = st.data_editor(
            editor_df,
            hide_index=True,
            width="stretch",
            height=260,
            disabled=[
                "ElectricalModelID",
                "SoftwareName",
                "ElectricalModelName",
            ],
            column_order=[
                "SoftwareName",
                "ElectricalModelName",
                "IsModeled",
            ],
            column_config={
                "ElectricalModelID": None,
                "SoftwareName": st.column_config.TextColumn(
                    "Software",
                    width=160,
                    disabled=True,
                ),
                "ElectricalModelName": st.column_config.TextColumn(
                    "Modelo eléctrico",
                    width=240,
                    disabled=True,
                ),
                "IsModeled": st.column_config.CheckboxColumn(
                    "Modelado",
                    width=100,
                    help="Marca si el proyecto está incluido en este modelo eléctrico.",
                ),
            },
            key=f"project_modeling_editor_{project_id}",
        )

        if st.button(
            "Guardar modelación",
            type="primary",
            use_container_width=True,
            key=f"save_project_modeling_{project_id}",
        ):
            try:
                original_by_id = {
                    int(row["ElectricalModelID"]): bool(row["IsModeled"])
                    for _, row in status_df.iterrows()
                }

                updates_count = 0

                for _, row in edited_df.iterrows():
                    electrical_model_id = int(row["ElectricalModelID"])
                    new_value = bool(row["IsModeled"])

                    if original_by_id.get(electrical_model_id) != new_value:
                        update_project_modeling_status(
                            project_id=project_id,
                            electrical_model_id=electrical_model_id,
                            is_modeled=new_value,
                        )
                        updates_count += 1

                st.session_state[msg_key] = {
                    "type": "success",
                    "text": f"✅ Modelación actualizada ({updates_count} cambios).",
                }
                st.rerun()

            except Exception as exc:
                st.session_state[msg_key] = {
                    "type": "error",
                    "text": f"❌ Error al guardar modelación: {exc}",
                }
                st.rerun()

    @staticmethod
    def render_error(error: Exception) -> None:
        st.error("An error occurred while loading project data.")
        st.exception(error)
