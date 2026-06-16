from datetime import date

import pandas as pd
import streamlit as st

from services.electrical_model_service import (
    bulk_set_modeled_by_cod_date,
    create_model,
    deactivate_model,
    list_models,
    list_software,
    preview_projects_for_bulk_modeling_by_cod,
)


class ElectricalModelView:
    """Global UI panel for electrical model management."""

    @staticmethod
    def render_electrical_model_management_panel(
        compact: bool = False,
        expanded: bool = True,
    ) -> None:
        """Render the electrical model management section."""

        with st.expander("⚡ Gestión modelos eléctricos", expanded=expanded):
            st.caption(
                "Catálogo y modelación masiva."
                if compact
                else (
                    "Catálogo de modelos eléctricos y herramientas de modelación "
                    "individual o masiva."
                )
            )
            st.divider()

            tab_models, tab_bulk = st.tabs(
                ["Modelos", "Gestión Masiva"]
                if compact
                else ["Modelos Eléctricos", "Gestión Masiva"]
            )

            with tab_models:
                ElectricalModelView.render_models_management_tab(compact=compact)

            with tab_bulk:
                ElectricalModelView.render_bulk_modeling_tab(compact=compact)

    @staticmethod
    def render_database_management_panel() -> None:
        """Backward-compatible entrypoint from previous layouts."""

        ElectricalModelView.render_electrical_model_management_panel()

    @staticmethod
    def render_global_panel() -> None:
        """Backward-compatible entrypoint used by older app.py versions."""

        ElectricalModelView.render_electrical_model_management_panel()

    @staticmethod
    def render_models_management_tab(compact: bool = False) -> None:
        """Render the model catalog management tab."""

        ElectricalModelView.render_models_table(compact=compact)

        st.divider()

        if compact:
            ElectricalModelView.render_create_model_form()
            st.divider()
            ElectricalModelView.render_deactivate_model_form()
        else:
            col_create, col_deactivate = st.columns([2, 1], gap="large")

            with col_create:
                ElectricalModelView.render_create_model_form()

            with col_deactivate:
                ElectricalModelView.render_deactivate_model_form()

    @staticmethod
    def render_bulk_modeling_tab(compact: bool = False) -> None:
        """Render bulk modeling operations."""

        st.markdown("#### Marcar proyectos modelados")
        st.caption(
            "Permite marcar como modelados los proyectos que cumplen un "
            "criterio de fecha relevante."
        )

        try:
            models_df = list_models(include_inactive=False)
        except Exception as exc:
            st.error(f"No se pudieron cargar los modelos eléctricos: {exc}")
            return

        if models_df.empty:
            st.warning("No hay modelos eléctricos activos.")
            return

        models_df = models_df.copy()
        models_df["OptionLabel"] = (
            models_df["SoftwareName"].astype(str)
            + " — "
            + models_df["ElectricalModelName"].astype(str)
        )

        model_options = {
            row["OptionLabel"]: int(row["ElectricalModelID"])
            for _, row in models_df.iterrows()
        }

        selected_label = st.selectbox(
            "Modelo eléctrico",
            options=list(model_options.keys()),
            key="bulk_modeling_selected_model",
        )

        inclusion_label = st.selectbox(
            "Criterio de inclusión",
            options=[
                "Obras en operación",
                "Obras en operación + proyectadas",
            ],
            key="bulk_modeling_inclusion_mode",
            help=(
                "Obras en operación usa solo COD_Actual. "
                "Obras en operación + proyectadas usa COD_Actual, luego "
                "COD_Estimated, luego Commissioning_Actual y finalmente "
                "Commissioning_Estimated."
            ),
        )

        st.caption(
            "Prioridad del modo proyectado: "
            "`COD_Actual > COD_Estimated > Commissioning_Actual > "
            "Commissioning_Estimated`."
        )

        if compact:
            cutoff_date = st.date_input(
                "Fecha límite",
                value=date.today(),
                key="bulk_modeling_cod_cutoff",
            )

            project_type_label = st.selectbox(
                "Tipo",
                options=["Todos", "Transmisión", "Generación", "BESS", "DER"],
                key="bulk_modeling_project_type",
            )

            only_unmodeled = st.checkbox(
                "Solo no modelados",
                value=True,
                key="bulk_modeling_only_unmodeled",
            )
        else:
            col_date, col_type, col_filter = st.columns([1, 1, 1], gap="large")

            with col_date:
                cutoff_date = st.date_input(
                    "Fecha límite",
                    value=date.today(),
                    key="bulk_modeling_cod_cutoff",
                )

            with col_type:
                project_type_label = st.selectbox(
                    "Tipo de proyecto",
                    options=["Todos", "Transmisión", "Generación", "BESS", "DER"],
                    key="bulk_modeling_project_type",
                )

            with col_filter:
                only_unmodeled = st.checkbox(
                    "Solo no modelados",
                    value=True,
                    key="bulk_modeling_only_unmodeled",
                    help=(
                        "Si está marcado, excluye proyectos que ya están "
                        "modelados en el modelo seleccionado."
                    ),
                )

        project_type_map = {
            "Todos": "all",
            "Transmisión": "transmission",
            "Generación": "generation",
            "BESS": "bess",
            "DER": "der",
        }

        inclusion_mode_map = {
            "Obras en operación": "operation",
            "Obras en operación + proyectadas": "operation_projected",
        }

        electrical_model_id = model_options[selected_label]
        project_type = project_type_map[project_type_label]
        inclusion_mode = inclusion_mode_map[inclusion_label]

        preview_key = "bulk_modeling_preview_df"
        params_key = "bulk_modeling_preview_params"
        msg_key = "bulk_modeling_msg"

        if msg_key in st.session_state:
            msg = st.session_state.pop(msg_key)
            if msg["type"] == "success":
                st.success(msg["text"])
            else:
                st.error(msg["text"])

        if compact:
            preview_clicked = st.button(
                "Previsualizar",
                type="secondary",
                use_container_width=True,
                key="bulk_modeling_preview_button",
            )
        else:
            col_preview, _ = st.columns([1, 1], gap="large")
            with col_preview:
                preview_clicked = st.button(
                    "Previsualizar proyectos",
                    type="secondary",
                    use_container_width=True,
                    key="bulk_modeling_preview_button",
                )

        if preview_clicked:
            try:
                preview_df = preview_projects_for_bulk_modeling_by_cod(
                    electrical_model_id=electrical_model_id,
                    cod_cutoff_date=cutoff_date,
                    project_type=project_type,
                    only_unmodeled=only_unmodeled,
                    inclusion_mode=inclusion_mode,
                )
                st.session_state[preview_key] = preview_df
                st.session_state[params_key] = {
                    "electrical_model_id": electrical_model_id,
                    "cod_cutoff_date": cutoff_date,
                    "project_type": project_type,
                    "only_unmodeled": only_unmodeled,
                    "selected_label": selected_label,
                    "inclusion_mode": inclusion_mode,
                    "inclusion_label": inclusion_label,
                }
                st.rerun()
            except Exception as exc:
                st.session_state[msg_key] = {
                    "type": "error",
                    "text": f"❌ Error en previsualización: {exc}",
                }
                st.rerun()

        preview_df = st.session_state.get(preview_key, pd.DataFrame())
        preview_params = st.session_state.get(params_key, {})

        can_apply = (
            isinstance(preview_df, pd.DataFrame)
            and not preview_df.empty
            and preview_params.get("electrical_model_id") == electrical_model_id
            and preview_params.get("cod_cutoff_date") == cutoff_date
            and preview_params.get("project_type") == project_type
            and preview_params.get("only_unmodeled") == only_unmodeled
            and preview_params.get("inclusion_mode") == inclusion_mode
        )

        if st.button(
            "Aplicar modelación masiva",
            type="primary",
            use_container_width=True,
            key="bulk_modeling_apply_button",
            disabled=not can_apply,
        ):
            try:
                result = bulk_set_modeled_by_cod_date(
                    electrical_model_id=electrical_model_id,
                    cod_cutoff_date=cutoff_date,
                    project_type=project_type,
                    only_unmodeled=only_unmodeled,
                    inclusion_mode=inclusion_mode,
                )

                try:
                    from services.project_data_service import ProjectDataService

                    ProjectDataService.clear_loaded_data()
                except Exception:
                    pass

                st.session_state.pop(preview_key, None)
                st.session_state.pop(params_key, None)
                st.session_state[msg_key] = {
                    "type": "success",
                    "text": (
                        "✅ Modelación masiva aplicada. "
                        f"Coincidencias: {result['matched']}. "
                        f"Cambios: {result['changed']} "
                        f"({result['created']} nuevos, "
                        f"{result['updated']} actualizados)."
                    ),
                }
                st.rerun()
            except Exception as exc:
                st.session_state[msg_key] = {
                    "type": "error",
                    "text": f"❌ Error al aplicar modelación masiva: {exc}",
                }
                st.rerun()

        if isinstance(preview_df, pd.DataFrame) and not preview_df.empty:
            st.markdown("**Preview**")
            st.caption(
                f"{len(preview_df)} proyectos encontrados. "
                f"Criterio: `{preview_params.get('inclusion_label', inclusion_label)}`."
            )

            display_df = preview_df.copy()

            date_columns = [
                "COD_Actual",
                "COD_Estimated",
                "Commissioning_Actual",
                "Commissioning_Estimated",
                "ReferenceDate",
            ]

            for column in date_columns:
                if column in display_df.columns:
                    display_df[column] = pd.to_datetime(
                        display_df[column],
                        errors="coerce",
                    ).dt.strftime("%d-%m-%Y")

            preview_columns = [
                "ProjectID",
                "ProjectName",
                "NUP",
                "ReferenceDate",
                "ReferenceDateSource",
                "IsCurrentlyModeled",
            ]

            if not compact:
                preview_columns = [
                    "ProjectID",
                    "ProjectName",
                    "NUP",
                    "ProjectEntityName",
                    "project_discriminator",
                    "COD_Actual",
                    "COD_Estimated",
                    "Commissioning_Actual",
                    "Commissioning_Estimated",
                    "ReferenceDate",
                    "ReferenceDateSource",
                    "IsCurrentlyModeled",
                ]

            st.dataframe(
                display_df[preview_columns],
                hide_index=True,
                width="stretch",
                height=180 if compact else 260,
                column_config={
                    "ProjectID": st.column_config.NumberColumn("ID", width=70),
                    "ProjectName": st.column_config.TextColumn(
                        "Proyecto",
                        width=260 if compact else 360,
                    ),
                    "NUP": st.column_config.NumberColumn("NUP", width=80),
                    "ProjectEntityName": st.column_config.TextColumn(
                        "Empresa",
                        width=220,
                    ),
                    "project_discriminator": st.column_config.TextColumn(
                        "Tipo",
                        width=110,
                    ),
                    "COD_Actual": st.column_config.TextColumn(
                        "COD Actual",
                        width=105,
                    ),
                    "COD_Estimated": st.column_config.TextColumn(
                        "COD Est.",
                        width=105,
                    ),
                    "Commissioning_Actual": st.column_config.TextColumn(
                        "Com. Actual",
                        width=110,
                    ),
                    "Commissioning_Estimated": st.column_config.TextColumn(
                        "Com. Est.",
                        width=110,
                    ),
                    "ReferenceDate": st.column_config.TextColumn(
                        "Fecha usada",
                        width=110,
                    ),
                    "ReferenceDateSource": st.column_config.TextColumn(
                        "Fuente",
                        width=150,
                    ),
                    "IsCurrentlyModeled": st.column_config.CheckboxColumn(
                        "Modelado",
                        width=95,
                    ),
                },
            )
        else:
            st.info("Previsualiza antes de aplicar cambios.")

    @staticmethod
    def render_bulk_operations_tab() -> None:
        """Backward-compatible alias from previous layouts."""

        ElectricalModelView.render_bulk_modeling_tab()

    @staticmethod
    def render_models_table(compact: bool = False) -> None:
        """Render registered electrical models."""

        st.markdown("**Modelos registrados**")

        try:
            models_df = list_models(include_inactive=True)
        except Exception as exc:
            st.error(f"No se pudieron cargar los modelos eléctricos: {exc}")
            return

        if models_df.empty:
            st.info("No hay modelos eléctricos registrados.")
            return

        display_df = models_df[
            [
                "ElectricalModelID",
                "SoftwareName",
                "ElectricalModelName",
                "IsActive",
            ]
        ].copy()

        st.dataframe(
            display_df,
            hide_index=True,
            width="stretch",
            height=180 if compact else 220,
            column_config={
                "ElectricalModelID": st.column_config.NumberColumn(
                    "ID",
                    width=60,
                ),
                "SoftwareName": st.column_config.TextColumn(
                    "Software",
                    width=140 if compact else 180,
                ),
                "ElectricalModelName": st.column_config.TextColumn(
                    "Modelo",
                    width=240 if compact else 520,
                ),
                "IsActive": st.column_config.CheckboxColumn(
                    "Activo",
                    width=80,
                ),
            },
        )

    @staticmethod
    def render_create_model_form() -> None:
        """Render the electrical model creation form."""

        st.markdown("**Agregar modelo**")

        try:
            software_df = list_software()
        except Exception as exc:
            st.error(f"No se pudo cargar el catálogo de software: {exc}")
            return

        if software_df.empty:
            st.warning("No hay software disponible.")
            return

        software_options = {
            row["SoftwareName"]: int(row["SoftwareID"])
            for _, row in software_df.iterrows()
        }

        msg_key = "electrical_model_create_msg"
        if msg_key in st.session_state:
            msg = st.session_state.pop(msg_key)
            if msg["type"] == "success":
                st.success(msg["text"])
            else:
                st.error(msg["text"])

        with st.form("create_electrical_model_form"):
            model_name = st.text_input(
                "Nombre",
                placeholder="Ejemplo: Modelo SEN 2026",
            )

            software_name = st.selectbox(
                "Software",
                options=list(software_options.keys()),
            )

            submitted = st.form_submit_button(
                "Agregar",
                type="primary",
                use_container_width=True,
            )

            if submitted:
                try:
                    create_model(
                        electrical_model_name=model_name,
                        software_id=software_options[software_name],
                    )
                    st.session_state[msg_key] = {
                        "type": "success",
                        "text": "✅ Modelo eléctrico agregado.",
                    }
                    st.rerun()
                except Exception as exc:
                    st.session_state[msg_key] = {
                        "type": "error",
                        "text": f"❌ Error: {exc}",
                    }
                    st.rerun()

    @staticmethod
    def render_deactivate_model_form() -> None:
        """Render a soft-delete form for active electrical models."""

        st.markdown("**Desactivar modelo**")

        try:
            models_df = list_models(include_inactive=False)
        except Exception as exc:
            st.error(f"No se pudieron cargar los modelos activos: {exc}")
            return

        if models_df.empty:
            st.caption("No hay modelos activos para desactivar.")
            return

        models_df = models_df.copy()
        models_df["OptionLabel"] = (
            models_df["SoftwareName"].astype(str)
            + " — "
            + models_df["ElectricalModelName"].astype(str)
        )

        option_map = {
            row["OptionLabel"]: int(row["ElectricalModelID"])
            for _, row in models_df.iterrows()
        }

        msg_key = "electrical_model_deactivate_msg"
        if msg_key in st.session_state:
            msg = st.session_state.pop(msg_key)
            if msg["type"] == "success":
                st.success(msg["text"])
            else:
                st.error(msg["text"])

        with st.form("deactivate_electrical_model_form"):
            option = st.selectbox(
                "Modelo activo",
                options=list(option_map.keys()),
            )

            submitted = st.form_submit_button(
                "Desactivar",
                use_container_width=True,
            )

            if submitted:
                try:
                    deactivate_model(option_map[option])
                    st.session_state[msg_key] = {
                        "type": "success",
                        "text": "✅ Modelo eléctrico desactivado.",
                    }
                    st.rerun()
                except Exception as exc:
                    st.session_state[msg_key] = {
                        "type": "error",
                        "text": f"❌ Error: {exc}",
                    }
                    st.rerun()
