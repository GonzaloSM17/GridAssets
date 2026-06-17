from __future__ import annotations

import streamlit as st


class ProjectModelingView:
    """Selected-project electrical modeling editor."""

    @staticmethod
    def render_modeling_editor(project_id: int) -> None:
        """Render editable electrical modeling status for one project."""
        from services.electrical_model_service import get_project_modeling_status

        st.markdown("**Estado de modelación eléctrica**")
        st.caption("Marca los modelos eléctricos donde este proyecto ya está representado.")

        msg_key = f"modeling_msg_{project_id}"
        ProjectModelingView._render_session_message(msg_key)

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

        edited_df = st.data_editor(
            status_df.copy(),
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
            ProjectModelingView._save_modeling_changes(
                project_id=project_id,
                status_df=status_df,
                edited_df=edited_df,
                msg_key=msg_key,
            )

    @staticmethod
    def _save_modeling_changes(project_id: int, status_df, edited_df, msg_key: str) -> None:
        from services.electrical_model_service import update_project_modeling_status

        try:
            original_by_id = {
                int(row["ElectricalModelID"]): bool(row["IsModeled"])
                for _, row in status_df.iterrows()
            }
            updates_count = 0
            for _, row in edited_df.iterrows():
                electrical_model_id = int(row["ElectricalModelID"])
                new_value = bool(row["IsModeled"])

                if original_by_id.get(electrical_model_id) == new_value:
                    continue

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
    def _render_session_message(msg_key: str) -> None:
        if msg_key not in st.session_state:
            return
        msg = st.session_state.pop(msg_key)
        if msg["type"] == "success":
            st.success(msg["text"])
        else:
            st.error(msg["text"])
