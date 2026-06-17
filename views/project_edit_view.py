from __future__ import annotations

import pandas as pd
import streamlit as st

from services.project_data_service import ProjectDataService


class ProjectEditView:
    """Compact project editing forms shown inside the selected-project detail panel."""

    @staticmethod
    def render_nup_editor(project_id: int, current_nup: int | None) -> None:
        """Render a compact form to manually set or clear the NUP of a project."""
        from services.project_edit_service import update_project_nup

        st.markdown("**NUP**")
        st.caption(f"Actual: {current_nup}" if current_nup else "Actual: Empty")

        msg_key = f"nup_msg_{project_id}"
        ProjectEditView._render_session_message(msg_key)

        with st.form(key=f"nup_form_{project_id}"):
            col_input, col_save, col_clear = st.columns([2.4, 1.1, 1.2])
            with col_input:
                nup_input = st.number_input(
                    "Nuevo NUP",
                    value=current_nup or 0,
                    min_value=0,
                    step=1,
                    help="Usa 0 para dejar el NUP vacío.",
                )
            with col_save:
                st.write("")
                submitted = st.form_submit_button(
                    "Guardar",
                    type="primary",
                    use_container_width=True,
                )
            with col_clear:
                st.write("")
                clear_nup = st.form_submit_button(
                    "Limpiar",
                    use_container_width=True,
                )

            if submitted or clear_nup:
                new_nup = int(nup_input) if submitted and int(nup_input) > 0 else None
                try:
                    update_project_nup(project_id, new_nup)
                    text = f"NUP actualizado: {new_nup}" if new_nup else "NUP limpiado."
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
        """Render a compact form to add, update, or delete a User-source RelevantDate."""
        from services.project_edit_service import get_milestone_types, update_project_date

        st.markdown("**Fechas manuales**")
        ProjectEditView._render_existing_user_dates(project_dates)

        msg_key = f"date_msg_{project_id}"
        ProjectEditView._render_session_message(msg_key)

        try:
            milestone_options = get_milestone_types()
        except Exception as exc:
            st.error(f"No se pudieron cargar los tipos de hito: {exc}")
            return

        if not milestone_options:
            st.info("No hay tipos de hito disponibles.")
            return

        with st.form(key=f"date_form_{project_id}"):
            col_milestone, col_date, col_save, col_delete = st.columns(
                [2.2, 1.25, 1.0, 1.0]
            )
            with col_milestone:
                milestone = st.selectbox("Hito", options=milestone_options)
            with col_date:
                date_value = st.date_input(
                    "Fecha",
                    value=None,
                    format="DD/MM/YYYY",
                )
            with col_save:
                st.write("")
                submitted = st.form_submit_button(
                    "Guardar",
                    type="primary",
                    use_container_width=True,
                )
            with col_delete:
                st.write("")
                delete = st.form_submit_button(
                    "Eliminar",
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
    def render_status_editor(project_id: int, current_status: str | None) -> None:
        """Render a compact form to manually set or clear the project status."""
        from services.project_edit_service import (
            get_project_status_options,
            update_project_status,
        )

        st.markdown("**Estado**")
        st.caption(f"Actual: {current_status}" if current_status else "Actual: Empty")

        msg_key = f"status_msg_{project_id}"
        ProjectEditView._render_session_message(msg_key)

        try:
            status_options = get_project_status_options()
        except Exception as exc:
            st.error(f"No se pudieron cargar los estados: {exc}")
            return

        options = ["Sin estado", *status_options]
        current_index = options.index(current_status) if current_status in options else 0

        with st.form(key=f"status_form_{project_id}"):
            col_status, col_custom, col_save, col_clear = st.columns(
                [1.6, 1.9, 1.0, 1.0]
            )
            with col_status:
                selected_status = st.selectbox(
                    "Estado",
                    options=options,
                    index=current_index,
                )
            with col_custom:
                custom_status = st.text_input(
                    "Personalizado",
                    value="",
                    placeholder="Opcional",
                )
            with col_save:
                st.write("")
                submitted = st.form_submit_button(
                    "Guardar",
                    type="primary",
                    use_container_width=True,
                )
            with col_clear:
                st.write("")
                clear_status = st.form_submit_button(
                    "Limpiar",
                    use_container_width=True,
                )

            if submitted or clear_status:
                if clear_status:
                    new_status = None
                else:
                    new_status = custom_status.strip() or selected_status
                    if new_status == "Sin estado":
                        new_status = None

                try:
                    update_project_status(project_id, new_status)
                    text = (
                        f"Estado actualizado: {new_status}"
                        if new_status
                        else "Estado limpiado."
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
    def _render_existing_user_dates(project_dates: pd.DataFrame) -> None:
        if project_dates.empty or "SourceName" not in project_dates.columns:
            st.caption("Fechas User: Empty")
            return

        user_dates = project_dates[project_dates["SourceName"] == "User"]
        if user_dates.empty:
            st.caption("Fechas User: Empty")
            return

        display_cols = [
            column
            for column in ["MilestoneName", "DateValue"]
            if column in user_dates.columns
        ]
        if not display_cols:
            st.caption("Fechas User: Empty")
            return

        st.dataframe(
            user_dates[display_cols],
            hide_index=True,
            width="stretch",
            height=min(150, 38 + 35 * max(len(user_dates), 1)),
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
