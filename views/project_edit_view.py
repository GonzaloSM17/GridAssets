import pandas as pd
import streamlit as st

from services.project_data_service import ProjectDataService


class ProjectEditView:
    """Project editing forms shown inside the selected-project detail panel."""

    @staticmethod
    def render_nup_editor(project_id: int, current_nup: int | None) -> None:
        """Render a form to manually set or clear the NUP of a project."""
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

        msg_key = f"nup_msg_{project_id}"
        ProjectEditView._render_session_message(msg_key)

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
                    " Guardar",
                    type="primary",
                    use_container_width=True,
                )
            with col_clear:
                clear_nup = st.form_submit_button(
                    " Limpiar NUP",
                    use_container_width=True,
                )

            if submitted or clear_nup:
                new_nup = int(nup_input) if submitted and int(nup_input) > 0 else None
                try:
                    update_project_nup(project_id, new_nup)
                    text = (
                        f"NUP actualizado: **{new_nup}**"
                        if new_nup
                        else "NUP limpiado."
                    )
                    st.session_state[msg_key] = {
                        "type": "success",
                        "text": f"✅ {text}",
                    }
                    ProjectDataService.clear_loaded_data()
                    st.rerun()
                except Exception as exc:
                    st.session_state[msg_key] = {
                        "type": "error",
                        "text": f"❌ Error: {exc}",
                    }
                    st.rerun()


    @staticmethod
    def render_status_editor(project_id: int, current_status: str | None) -> None:
        """Render a form to manually set or clear the project status."""
        from services.project_edit_service import (
            get_project_status_options,
            update_project_status,
        )

        st.markdown("**Status del proyecto**")
        st.caption(
            "Edita el estado consolidado del proyecto. "
            "Internamente se guarda en Project.StatusID."
        )

        if current_status:
            st.info(f"Status actual: **{current_status}**")
        else:
            st.warning("Sin status asignado.")

        msg_key = f"status_msg_{project_id}"
        ProjectEditView._render_session_message(msg_key)

        try:
            status_options = get_project_status_options()
        except Exception as exc:
            st.error(f"No se pudieron cargar los status: {exc}")
            return

        options = ["Sin status", *status_options]
        current_index = (
            options.index(current_status)
            if current_status in options
            else 0
        )

        with st.form(key=f"status_form_{project_id}"):
            selected_status = st.selectbox(
                "Status",
                options=options,
                index=current_index,
                label_visibility="collapsed",
            )
            custom_status = st.text_input(
                "Nuevo status personalizado",
                value="",
                placeholder="Opcional: escribir un nuevo status",
                label_visibility="collapsed",
            )

            col_save, col_clear = st.columns(2)
            with col_save:
                submitted = st.form_submit_button(
                    " Guardar status",
                    type="primary",
                    use_container_width=True,
                )
            with col_clear:
                clear_status = st.form_submit_button(
                    " Limpiar status",
                    use_container_width=True,
                )

            if submitted or clear_status:
                if clear_status:
                    new_status = None
                else:
                    new_status = custom_status.strip() or selected_status
                    if new_status == "Sin status":
                        new_status = None

                try:
                    update_project_status(project_id, new_status)
                    text = (
                        f"Status actualizado: **{new_status}**"
                        if new_status
                        else "Status limpiado."
                    )
                    st.session_state[msg_key] = {
                        "type": "success",
                        "text": f"✅ {text}",
                    }
                    ProjectDataService.clear_loaded_data()
                    st.rerun()
                except Exception as exc:
                    st.session_state[msg_key] = {
                        "type": "error",
                        "text": f"❌ Error: {exc}",
                    }
                    st.rerun()

    @staticmethod
    def render_date_editor(project_id: int, project_dates: pd.DataFrame) -> None:
        """Render a form to add, update, or delete a User-source RelevantDate."""
        from services.project_edit_service import get_milestone_types, update_project_date

        st.markdown("**Fechas de hito — fuente User**")
        st.caption("Agrega o modifica fechas manualmente. Se guardan con fuente 'User'.")

        ProjectEditView._render_existing_user_dates(project_dates)

        msg_key = f"date_msg_{project_id}"
        ProjectEditView._render_session_message(msg_key)

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
                    " Guardar fecha",
                    type="primary",
                    use_container_width=True,
                )
            with col_delete:
                delete = st.form_submit_button(
                    " Eliminar",
                    use_container_width=True,
                )

            if submitted or delete:
                new_date = date_value if submitted and date_value else None
                try:
                    update_project_date(project_id, milestone, new_date)
                    if new_date:
                        text = (
                            f"Fecha guardada: {milestone} → "
                            f"{new_date.strftime('%d/%m/%Y')}"
                        )
                    else:
                        text = f"Fecha eliminada: {milestone}"

                    st.session_state[msg_key] = {
                        "type": "success",
                        "text": f"✅ {text}",
                    }
                    ProjectDataService.clear_loaded_data()
                    st.rerun()
                except Exception as exc:
                    st.session_state[msg_key] = {
                        "type": "error",
                        "text": f"❌ {exc}",
                    }
                    st.rerun()

    @staticmethod
    def _render_existing_user_dates(project_dates: pd.DataFrame) -> None:
        if project_dates.empty or "SourceName" not in project_dates.columns:
            st.caption("Sin fechas de fuente 'User' para este proyecto.")
            return

        user_dates = project_dates[project_dates["SourceName"] == "User"]
        if user_dates.empty:
            st.caption("Sin fechas de fuente 'User' para este proyecto.")
            return

        display_cols = [
            column
            for column in ["MilestoneName", "DateValue"]
            if column in user_dates.columns
        ]
        st.dataframe(
            user_dates[display_cols],
            hide_index=True,
            use_container_width=True,
        )

    @staticmethod
    def _render_session_message(msg_key: str) -> None:
        if msg_key not in st.session_state:
            return

        msg = st.session_state.pop(msg_key)
        if msg["type"] == "success":
            st.success(msg["text"])
        else:
            st.error(msg["text"])
