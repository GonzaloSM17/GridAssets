"""Streamlit view for CEN Conexiones enrichment."""
from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Iterable

import pandas as pd
import streamlit as st

from parsers.cen_connection_files_parse import CENConnectionFileParser, ConnectionParseResult
from services.cen_connection_enrichment_service import CENConnectionEnrichmentService


class CENConnectionView:
    """UI for CEN Conexiones complementary enrichment."""

    UPLOAD_HELP = (
        "Archivos CEN Conexiones usados para complementar proyectos existentes. "
        "No crean proyectos nuevos."
    )

    AUTO_PROPOSED_STATUSES = {"matched_by_nup", "matched_by_name"}

    VALIDATION_STATUSES = {
        "candidate_by_name",
        "ambiguous_name",
        "ambiguous_nup",
        "nup_type_mismatch",
    }

    @staticmethod
    def render_expander(expanded: bool = False) -> None:
        with st.expander("CEN Conexiones — Enriquecimiento de BD", expanded=expanded):
            CENConnectionView.render()

    @staticmethod
    def render() -> None:
        st.markdown("### CEN Conexiones — Enriquecimiento de BD")
        st.caption(
            "Carga archivos CEN Conexiones para complementar proyectos existentes "
            "con NUP, fechas PES/EO y estados cancelados/desistidos. No crea proyectos nuevos."
        )

        tab_entry, tab_construction = st.tabs(
            ["Entrada en Operación", "Declarados en Construcción"]
        )

        with tab_entry:
            CENConnectionView._render_uploader(
                label="Proyectos con Entrada en Operación (.xlsx)",
                profile_key="entry_operation",
                uploader_key="cen_connection_entry_operation_uploader",
            )

        with tab_construction:
            CENConnectionView._render_uploader(
                label="Proyectos Declarados en Construcción (.xlsx)",
                profile_key="declared_construction",
                uploader_key="cen_connection_declared_construction_uploader",
            )

    @staticmethod
    def _render_uploader(label: str, profile_key: str, uploader_key: str) -> None:
        uploaded = st.file_uploader(
            label,
            type=["xlsx", "xlsm", "xls"],
            key=uploader_key,
            help=CENConnectionView.UPLOAD_HELP,
        )

        if uploaded is None:
            st.info("Sube un archivo para inspeccionar su estructura y fechas normalizadas.")
            return

        tmp_path = CENConnectionView._save_uploaded_file(uploaded)
        try:
            result = CENConnectionFileParser().parse(tmp_path, profile_key=profile_key)
        except Exception as exc:
            st.error(f"No fue posible leer el archivo: {exc}")
            return

        CENConnectionView._render_result(result)

    @staticmethod
    def _save_uploaded_file(uploaded) -> Path:
        suffix = Path(uploaded.name).suffix or ".xlsx"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded.getbuffer())
            return Path(tmp.name)

    @staticmethod
    def _render_result(result: ConnectionParseResult) -> None:
        st.success(f"Archivo reconocido: {result.profile_label}")

        summary = result.summary_dict()
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Filas", summary.get("total_rows", 0))
        col2.metric("Con NUP", summary.get("rows_with_nup", 0))
        col3.metric("Con fechas", summary.get("rows_with_any_date", 0))
        col4.metric("Advertencias", summary.get("warnings", 0))

        with st.expander("Resumen por hoja", expanded=True):
            if result.sheet_summaries.empty:
                st.warning("No hay resumen de hojas disponible.")
            else:
                st.dataframe(
                    CENConnectionView._prepare_display_dataframe(CENConnectionView._localize_dataframe(result.sheet_summaries)),
                    width="stretch",
                    hide_index=True,
                )

        if result.warnings:
            with st.expander("Advertencias", expanded=False):
                for warning in result.warnings:
                    st.warning(warning)

        records = result.records.copy()
        if records.empty:
            st.warning("No se normalizaron filas útiles desde este archivo.")
            return

        CENConnectionView._render_date_coverage(records)
        CENConnectionView._render_type_coverage(records)
        CENConnectionView._render_normalized_preview(records)
        CENConnectionView._render_match_preview_and_apply(records, result.profile_key)
        CENConnectionView._render_download(records, result.profile_key)

    @staticmethod
    def _render_date_coverage(records: pd.DataFrame) -> None:
        date_cols = [
            "commissioning_actual",
            "commissioning_estimated",
            "cod_actual",
            "cod_estimated",
        ]
        existing = [col for col in date_cols if col in records.columns]
        coverage = pd.DataFrame(
            {
                "Fecha": [CENConnectionView._date_label(col) for col in existing],
                "Filas con dato": [int(records[col].notna().sum()) for col in existing],
            }
        )
        coverage["Total"] = len(records)
        coverage["Cobertura"] = (
            coverage["Filas con dato"] / coverage["Total"] * 100
        ).round(1).astype(str) + "%"

        with st.expander("Cobertura de fechas", expanded=True):
            st.dataframe(CENConnectionView._prepare_display_dataframe(coverage), width="stretch", hide_index=True)

    @staticmethod
    def _render_type_coverage(records: pd.DataFrame) -> None:
        cols = ["source_sheet", "connection_project_type"]
        if not set(cols).issubset(records.columns):
            return

        coverage = (
            records.groupby(cols, dropna=False)
            .size()
            .reset_index(name="Filas")
            .sort_values(["source_sheet", "connection_project_type"])
            .rename(
                columns={
                    "source_sheet": "Hoja",
                    "connection_project_type": "Tipo normalizado",
                }
            )
        )
        if "Tipo normalizado" in coverage.columns:
            coverage["Tipo normalizado"] = coverage["Tipo normalizado"].apply(CENConnectionView._type_label)
        with st.expander("Cobertura por hoja y tipo", expanded=False):
            st.dataframe(CENConnectionView._prepare_display_dataframe(coverage), width="stretch", hide_index=True)

    @staticmethod
    def _render_normalized_preview(records: pd.DataFrame) -> None:
        preferred_cols = [
            "source_sheet",
            "record_action",
            "target_status",
            "connection_project_type",
            "nup",
            "project_name",
            "commissioning_actual",
            "commissioning_estimated",
            "cod_actual",
            "cod_estimated",
        ]
        cols = [col for col in preferred_cols if col in records.columns]
        preview = records[cols].head(100).rename(
            columns={
                "source_sheet": "Hoja",
                "record_action": "Acción",
                "target_status": "Estado objetivo",
                "connection_project_type": "Tipo",
                "nup": "NUP",
                "project_name": "Proyecto archivo",
                "commissioning_actual": "PES real",
                "commissioning_estimated": "PES est.",
                "cod_actual": "EO real",
                "cod_estimated": "EO est.",
            }
        )

        if "Tipo" in preview.columns:
            preview["Tipo"] = preview["Tipo"].apply(CENConnectionView._type_label)

        with st.expander("Previsualización normalizada", expanded=False):
            st.dataframe(CENConnectionView._prepare_display_dataframe(preview), width="stretch", hide_index=True)
            if len(records) > 100:
                st.caption(f"Mostrando 100 de {len(records)} filas normalizadas.")

    @staticmethod
    def _render_match_preview_and_apply(records: pd.DataFrame, profile_key: str) -> None:
        st.divider()
        st.markdown("#### Previsualización de cruce con BD")
        st.caption(
            "Cruza el archivo normalizado completo contra proyectos existentes. "
            "Usa NUP cuando exista en ambas fuentes; si no, usa nombre tratado solo "
            "para matching y tipo compatible. No crea proyectos."
        )

        preview_key = f"cen_connection_match_preview_df_{profile_key}"
        signature_key = f"cen_connection_match_preview_signature_{profile_key}"
        selected_key = f"cen_connection_selected_row_ids_{profile_key}"
        selected_candidates_key = f"cen_connection_selected_candidate_project_ids_{profile_key}"
        signature = CENConnectionView._records_signature(records, profile_key)

        if st.session_state.get(signature_key) != signature:
            st.session_state.pop(preview_key, None)
            st.session_state.pop(selected_key, None)
            st.session_state.pop(selected_candidates_key, None)
            st.session_state[signature_key] = signature

        preview = st.session_state.get(preview_key)
        has_preview = isinstance(preview, pd.DataFrame) and not preview.empty
        selected_row_ids = st.session_state.get(selected_key, []) if has_preview else []
        selected_count = len(selected_row_ids or [])

        col_run, col_apply, col_clear = st.columns([2.2, 1.8, 1.2])
        with col_run:
            run_preview = st.button(
                "Generar previsualización de cruce con BD",
                key=f"cen_connection_run_match_preview_{profile_key}",
                type="primary",
                width="stretch",
            )
        with col_apply:
            apply_clicked = st.button(
                "Aplicar en base de datos",
                key=f"cen_connection_apply_database_top_{profile_key}",
                type="primary",
                width="stretch",
                disabled=not has_preview,
                help=(
                    "Primero genera la previsualización de cruce."
                    if not has_preview
                    else "Aplica solo las filas marcadas con Aplicar = True. Si no hay filas marcadas, se mostrará un aviso."
                ),
            )
        with col_clear:
            clear_preview = st.button(
                "Limpiar previsualización",
                key=f"cen_connection_clear_match_preview_{profile_key}",
                width="stretch",
                disabled=not has_preview,
            )

        if clear_preview:
            st.session_state.pop(preview_key, None)
            st.session_state.pop(selected_key, None)
            st.session_state.pop(selected_candidates_key, None)
            st.info("Previsualización de cruce limpiada.")
            return

        if run_preview:
            try:
                with st.spinner("Cruzando proyectos existentes..."):
                    service = CENConnectionEnrichmentService()
                    preview = service.build_match_preview(records)
                    preview = CENConnectionView._ensure_preview_ui_fields(preview)
                    st.session_state[preview_key] = preview
                    # Default selection is set only for automatically proposed rows.
                    default_selected = preview.loc[
                        preview.get("default_apply", pd.Series([False] * len(preview), index=preview.index)).fillna(False),
                        "preview_row_id",
                    ].dropna().astype(int).tolist()
                    st.session_state[selected_key] = sorted(set(default_selected))
                    st.session_state[selected_candidates_key] = {}
            except Exception as exc:
                st.error(f"No fue posible cruzar contra la base de datos: {exc}")
                st.session_state.pop(preview_key, None)
                st.session_state.pop(selected_key, None)
                st.session_state.pop(selected_candidates_key, None)
                return

        preview = st.session_state.get(preview_key)
        if preview is None:
            st.info("Presiona el botón para generar la previsualización de cruce contra la BD.")
            return

        if not isinstance(preview, pd.DataFrame) or preview.empty:
            st.warning("No se generó previsualización de cruce.")
            return

        preview = CENConnectionView._ensure_preview_ui_fields(preview)
        st.session_state[preview_key] = preview

        if apply_clicked:
            service = CENConnectionEnrichmentService()
            selected_ids_for_apply = st.session_state.get(selected_key, [])
            if not selected_ids_for_apply:
                st.warning("No hay filas marcadas para aplicar en base de datos.")
            else:
                try:
                    with st.spinner("Aplicando enriquecimiento en base de datos..."):
                        result = service.apply_selected_enrichment(
                            preview,
                            selected_ids_for_apply,
                            st.session_state.get(selected_candidates_key, {}),
                        )
                except Exception as exc:
                    st.error(f"No fue posible aplicar el enriquecimiento: {exc}")
                    return

                summary = result.get("summary", {})
                st.success("Aplicación completada.")
                CENConnectionView._render_apply_summary(summary)

                details = result.get("details")
                if isinstance(details, pd.DataFrame) and not details.empty:
                    with st.expander("Detalle de aplicación", expanded=False):
                        detail_display = details.rename(
                            columns={
                                "status": "Estado",
                                "message": "Mensaje",
                                "matched_project_id": "ID proyecto BD",
                                "matched_project_name": "Proyecto sugerido",
                                "source_sheet": "Hoja",
                                "row_number": "Fila archivo",
                                "record_action": "Acción",
                                "target_status": "Estado objetivo",
                                "project_name": "Proyecto archivo",
                                "nup": "NUP archivo",
                            }
                        )
                        if "Estado" in detail_display.columns:
                            detail_display["Estado"] = detail_display["Estado"].apply(CENConnectionView._apply_status_label)
                        st.dataframe(
                            CENConnectionView._prepare_display_dataframe(CENConnectionView._localize_dataframe(detail_display)),
                            width="stretch",
                            hide_index=True,
                        )

        st.success(f"Previsualización de cruce generada: {len(preview)} filas.")
        CENConnectionView._render_match_summary(preview)
        selected_row_ids = CENConnectionView._render_match_tables(preview, profile_key)
        CENConnectionView._render_match_download(preview, profile_key)
        CENConnectionView._render_apply_status(preview, selected_row_ids)

    @staticmethod
    def _records_signature(records: pd.DataFrame, profile_key: str) -> str:
        """Return a lightweight signature to avoid reusing previews for another file."""
        parts = [profile_key, str(len(records))]
        for col in ["source_sheet", "connection_project_type", "nup", "project_name"]:
            if col in records.columns:
                sample = records[col].head(20).fillna("").astype(str).tolist()
                parts.append(col + "=" + "|".join(sample))
        return "::".join(parts)


    @staticmethod
    def _ensure_preview_ui_fields(preview: pd.DataFrame) -> pd.DataFrame:
        """Return a copy with UI grouping fields, even if the service is older."""
        result = preview.copy().reset_index(drop=True)

        if "preview_row_id" not in result.columns:
            result["preview_row_id"] = result.index.astype(int)

        status = result.get("match_status", pd.Series([""] * len(result), index=result.index)).fillna("").astype(str)

        if "match_group" not in result.columns:
            result["match_group"] = status.map(
                lambda value: (
                    "auto_proposed"
                    if value in {"matched_by_nup", "matched_by_name"}
                    else "needs_validation"
                    if value in {"candidate_by_name", "ambiguous_name", "ambiguous_nup", "nup_type_mismatch"}
                    else "no_candidate"
                )
            )
        else:
            result["match_group"] = result["match_group"].fillna("").astype(str)

        if "match_group_label" not in result.columns:
            result["match_group_label"] = result["match_group"].map(
                {
                    "auto_proposed": "Propuestos automáticamente",
                    "needs_validation": "Requieren validación",
                    "no_candidate": "Sin candidato suficiente",
                }
            ).fillna("Completo")

        if "match_status_label" not in result.columns:
            result["match_status_label"] = status.map(
                {
                    "matched_by_nup": "Propuesto por NUP",
                    "matched_by_name": "Propuesto por nombre",
                    "candidate_by_name": "Validar candidato",
                    "ambiguous_name": "Ambiguo por nombre",
                    "ambiguous_nup": "Ambiguo por NUP",
                    "nup_type_mismatch": "NUP con tipo distinto",
                    "not_found": "Sin candidato",
                    "no_project_name": "Sin nombre usable",
                }
            ).fillna(status)

        if "score_display" not in result.columns:
            score = pd.to_numeric(result.get("name_score", pd.Series([None] * len(result), index=result.index)), errors="coerce")
            result["score_display"] = score.map(lambda value: "" if pd.isna(value) else f"{value:.3f}")

        if "date_changes_text" not in result.columns:
            date_cols = [
                ("commissioning_actual", "PES real"),
                ("commissioning_estimated", "PES est."),
                ("cod_actual", "EO real"),
                ("cod_estimated", "EO est."),
            ]
            texts = []
            for _, row in result.iterrows():
                labels = []
                for col, label in date_cols:
                    if col in row and pd.notna(row.get(col)) and str(row.get(col)).strip() != "":
                        labels.append(label)
                texts.append(", ".join(labels))
            result["date_changes_text"] = texts

        if "can_apply" not in result.columns:
            result["can_apply"] = status.isin({"matched_by_nup", "matched_by_name", "candidate_by_name"}) & result.get(
                "matched_project_id", pd.Series([None] * len(result), index=result.index)
            ).notna()

        if "default_apply" not in result.columns:
            result["default_apply"] = status.isin({"matched_by_nup", "matched_by_name"}) & result.get(
                "matched_project_id", pd.Series([None] * len(result), index=result.index)
            ).notna()

        return result

    @staticmethod
    def _render_match_summary(preview: pd.DataFrame) -> None:
        preview = CENConnectionView._ensure_preview_ui_fields(preview)
        group_col = preview["match_group"].fillna("").astype(str)
        proposed_mask = group_col.eq("auto_proposed")
        validation_mask = group_col.eq("needs_validation")
        no_candidate_mask = group_col.eq("no_candidate")
        proposed_date_changes = int(
            preview.get("date_changes_proposed", pd.Series(dtype=int)).fillna(0).sum()
        )
        proposed_nup_changes = int(
            preview.get("would_update_nup", pd.Series(dtype=bool)).fillna(False).sum()
        )

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Filas cruzadas", len(preview))
        col2.metric("Propuestos", int(proposed_mask.sum()))
        col3.metric("Por validar", int(validation_mask.sum()))
        col4.metric("Sin candidato", int(no_candidate_mask.sum()))

        st.caption(
            f"Cambios detectados en el archivo: {proposed_nup_changes} NUP y "
            f"{proposed_date_changes} fechas. Solo se escriben filas marcadas con Aplicar."
        )

        if {"match_group_label", "match_status_label"}.issubset(preview.columns):
            status_counts = (
                preview.groupby(["match_group_label", "match_status_label"], dropna=False)
                .size()
                .reset_index(name="Filas")
                .sort_values("Filas", ascending=False)
                .rename(
                    columns={
                        "match_group_label": "Grupo",
                        "match_status_label": "Estado",
                    }
                )
            )
            with st.expander("Resumen de estados", expanded=True):
                st.dataframe(CENConnectionView._prepare_display_dataframe(status_counts), width="stretch", hide_index=True)

    @staticmethod
    def _render_match_tables(preview: pd.DataFrame, profile_key: str) -> list[int]:
        if preview.empty:
            st.info("No hay filas para mostrar.")
            return []

        preview = CENConnectionView._ensure_preview_ui_fields(preview)
        group = preview.get("match_group", pd.Series([""] * len(preview), index=preview.index)).fillna("").astype(str)
        auto_proposed = preview.loc[group.eq("auto_proposed")].copy()
        validation = preview.loc[group.eq("needs_validation")].copy()
        no_candidate = preview.loc[group.eq("no_candidate")].copy()

        selected_key = f"cen_connection_selected_row_ids_{profile_key}"
        selected_candidates_key = f"cen_connection_selected_candidate_project_ids_{profile_key}"
        current_selected = sorted(
            set(int(value) for value in st.session_state.get(selected_key, []) or [])
        )
        current_candidate_overrides = {
            int(key): int(value)
            for key, value in (st.session_state.get(selected_candidates_key, {}) or {}).items()
            if value is not None
        }

        st.markdown("##### Selección de aplicación")
        st.caption(
            "En Propuestos puedes confirmar o desmarcar. En Por validar elige el proyecto correcto "
            "en la columna Proyecto seleccionado; si queda en No aplicar, no se escribirá en la BD."
        )

        with st.form(f"cen_connection_selection_form_{profile_key}"):
            tab_proposed, tab_validation, tab_no_candidate, tab_all = st.tabs(
                [
                    f"Propuestos ({len(auto_proposed)})",
                    f"Por validar ({len(validation)})",
                    f"Sin candidato ({len(no_candidate)})",
                    f"Completo ({len(preview)})",
                ]
            )

            selected_ids: list[int] = []
            selected_candidate_overrides: dict[int, int] = dict(current_candidate_overrides)
            validation_has_errors = False

            with tab_proposed:
                st.caption("Filas propuestas automáticamente. Vienen marcadas por defecto, pero puedes desmarcarlas.")
                selected_ids.extend(
                    CENConnectionView._render_selectable_table(
                        auto_proposed,
                        profile_key=profile_key,
                        table_key="proposed",
                        default_apply=True,
                        max_rows=300,
                    )
                )

            with tab_validation:
                st.caption(
                    "Filas que requieren validación. Cada proyecto archivo puede mostrar hasta tres candidatos. "
                    "Marca solo un candidato por proyecto archivo; al aplicar, se actualizará ese Proyecto seleccionado."
                )
                validation_ids, validation_overrides, validation_has_errors = CENConnectionView._render_validation_table(
                    validation,
                    current_selected=current_selected,
                    current_candidate_overrides=current_candidate_overrides,
                    max_rows=250,
                )
                selected_ids.extend(validation_ids)
                selected_candidate_overrides.update(validation_overrides)

            with tab_no_candidate:
                st.caption("Filas sin candidato suficiente o sin ID de proyecto aplicable.")
                CENConnectionView._render_compact_table(no_candidate, max_rows=300, include_candidates=False)

            with tab_all:
                st.caption("Vista compacta para auditoría rápida. El CSV conserva el detalle técnico completo.")
                CENConnectionView._render_compact_table(preview, max_rows=500, include_candidates=False)

            save_selection = st.form_submit_button(
                "Guardar selección",
                type="primary",
                width="stretch",
            )

        if save_selection:
            if validation_has_errors:
                st.error("Hay proyectos con más de un candidato seleccionado. Deja solo un candidato por proyecto archivo antes de guardar.")
                return current_selected

            selected_ids = sorted(set(int(value) for value in selected_ids))
            # Keep only candidate overrides for selected validation rows.
            selected_candidate_overrides = {
                int(row_id): int(project_id)
                for row_id, project_id in selected_candidate_overrides.items()
                if int(row_id) in selected_ids and project_id is not None
            }
            st.session_state[selected_key] = selected_ids
            st.session_state[selected_candidates_key] = selected_candidate_overrides
            st.success(f"Selección guardada: {len(selected_ids)} filas marcadas para aplicar.")
            return selected_ids

        return current_selected

    @staticmethod
    def _render_selectable_table(
        preview: pd.DataFrame,
        profile_key: str,
        table_key: str,
        default_apply: bool,
        max_rows: int,
    ) -> list[int]:
        if preview.empty:
            st.info("No hay filas en esta categoría.")
            return []

        selected_key = f"cen_connection_selected_row_ids_{profile_key}"
        current_selected = set(int(value) for value in st.session_state.get(selected_key, []) or [])

        compact = CENConnectionView._build_compact_preview(preview, include_candidates=False)
        compact = compact.head(max_rows).copy()
        if "ID fila" in compact.columns:
            compact.insert(
                0,
                "Seleccionar",
                compact["ID fila"].apply(lambda value: int(value) in current_selected if pd.notna(value) else False),
            )
        else:
            compact.insert(0, "Seleccionar", compact.get("Aplicable", False).fillna(False) & default_apply)
        if "Aplicable" in compact.columns:
            compact = compact.drop(columns=["Aplicable"])

        # Place the selection checkbox between the source project and the target project.
        compact = CENConnectionView._move_column_after(compact, "Seleccionar", "Proyecto archivo")

        edited = st.data_editor(
            compact,
            key=f"cen_connection_select_{profile_key}_{table_key}",
            width="stretch",
            hide_index=True,
            disabled=[col for col in compact.columns if col != "Seleccionar"],
            column_config={
                "Seleccionar": st.column_config.CheckboxColumn(
                    "Seleccionar",
                    help="Marca esta fila para incluirla en la aplicación de enriquecimiento.",
                    default=False,
                ),
            },
        )

        if len(preview) > max_rows:
            st.caption(f"Mostrando {max_rows} de {len(preview)} filas.")

        if edited.empty or "Seleccionar" not in edited.columns or "ID fila" not in edited.columns:
            return []

        selected = edited.loc[edited["Seleccionar"].fillna(False)].copy()
        return [int(value) for value in selected["ID fila"].dropna().tolist()]


    @staticmethod
    def _render_validation_table(
        preview: pd.DataFrame,
        current_selected: list[int],
        current_candidate_overrides: dict[int, int],
        max_rows: int,
    ) -> tuple[list[int], dict[int, int], bool]:
        """Render validation candidates as a compact grouped table.

        Each candidate is shown as one selectable row. This keeps the review compact,
        allows choosing candidate 2 or 3, and avoids duplicated columns such as
        Proyecto BD / Candidato.
        """
        if preview.empty:
            st.info("No hay filas por validar.")
            return [], {}, False

        rows: list[dict] = []
        current_selected_set = set(int(value) for value in current_selected or [])

        working = preview.copy()
        if "name_score" in working.columns:
            working["_score_sort"] = pd.to_numeric(working["name_score"], errors="coerce")
            working = working.sort_values("_score_sort", ascending=False, na_position="last").drop(columns=["_score_sort"])

        source_rows_shown = 0
        for _, row in working.iterrows():
            if source_rows_shown >= max_rows:
                break
            row_id = CENConnectionView._to_int(row.get("preview_row_id"))
            if row_id is None:
                continue

            candidates = CENConnectionView._candidate_rows_for_row(row)
            if not candidates:
                continue

            source_rows_shown += 1
            selected_project_id = current_candidate_overrides.get(row_id)
            is_row_selected = row_id in current_selected_set

            for candidate_index, candidate in enumerate(candidates):
                project_id = candidate["project_id"]
                apply_value = bool(is_row_selected and selected_project_id == project_id)
                is_first_candidate = candidate_index == 0
                rows.append(
                    {
                        "Proyecto archivo": CENConnectionView._short_text(row.get("project_name"), 95) if is_first_candidate else "↳ mismo proyecto archivo",
                        "Seleccionar": apply_value,
                        "ID fila": row_id,
                        "ID proyecto seleccionado": project_id,
                        "Proyecto BD candidato": CENConnectionView._short_text(candidate["project_name"], 95),
                        "Opción": candidate["rank"],
                        "Puntaje": candidate["score_text"],
                        "Tipo BD": candidate["project_type"],
                        "NUP BD": candidate["nup"],
                        "Entidad BD": CENConnectionView._short_text(candidate["entity"], 55),
                        "Tecnología BD": CENConnectionView._short_text(candidate["technology"], 35),
                        "Capacidad BD": candidate["capacity"],
                        "Ubicación BD": CENConnectionView._short_text(candidate["location"], 45),
                        "Cambios sobre candidato": CENConnectionView._short_text(candidate["action_summary"], 95),
                        "Acción origen": CENConnectionView._action_label(row.get("record_action")) if is_first_candidate else "",
                        "Tipo origen": CENConnectionView._type_label(row.get("connection_project_type")) if is_first_candidate else "",
                        "Observación": CENConnectionView._short_text(
                            row.get("match_status_label") or row.get("match_comment") or "", 80
                        ) if is_first_candidate else "",
                    }
                )

        if not rows:
            st.info("No hay candidatos disponibles para estas filas.")
            return [], {}, False

        table = CENConnectionView._prepare_display_dataframe(pd.DataFrame(rows))
        edited = st.data_editor(
            table,
            key="cen_connection_validation_candidate_table",
            width="stretch",
            hide_index=True,
            disabled=[
                "ID fila",
                "ID proyecto seleccionado",
                "Proyecto archivo",
                "Proyecto BD candidato",
                "Opción",
                "Puntaje",
                "Tipo BD",
                "NUP BD",
                "Entidad BD",
                "Tecnología BD",
                "Capacidad BD",
                "Ubicación BD",
                "Cambios sobre candidato",
                "Acción origen",
                "Tipo origen",
                "Observación",
            ],
            column_config={
                "Seleccionar": st.column_config.CheckboxColumn(
                    "Seleccionar",
                    help="Marca solo un candidato por proyecto archivo. La aplicación usará el Proyecto seleccionado de esa fila.",
                    default=False,
                ),
                "ID fila": None,
                "ID proyecto seleccionado": None,
                "Opción": st.column_config.NumberColumn("Opción", format="%d"),
            },
        )

        if source_rows_shown >= max_rows and len(working) > max_rows:
            st.caption(f"Mostrando candidatos para {max_rows} de {len(working)} filas por validar.")

        if edited.empty or "Seleccionar" not in edited.columns:
            return [], {}, False

        selected = edited.loc[edited["Seleccionar"].fillna(False)].copy()
        if selected.empty:
            return [], {}, False

        duplicate_mask = selected.duplicated(subset=["ID fila"], keep=False)
        if duplicate_mask.any():
            duplicate_count = int(selected.loc[duplicate_mask, "ID fila"].nunique())
            st.error(
                f"Hay {duplicate_count} proyecto(s) archivo con más de un candidato seleccionado. "
                "Deja solo un candidato seleccionado por proyecto archivo antes de guardar."
            )
            return [], {}, True

        selected_ids: list[int] = []
        selected_candidate_overrides: dict[int, int] = {}
        for _, row in selected.iterrows():
            row_id = CENConnectionView._to_int(row.get("ID fila"))
            project_id = CENConnectionView._to_int(row.get("ID proyecto seleccionado"))
            if row_id is None or project_id is None:
                continue
            selected_ids.append(row_id)
            selected_candidate_overrides[row_id] = project_id

        return selected_ids, selected_candidate_overrides, False

    @staticmethod
    def _candidate_rows_for_row(row: pd.Series) -> list[dict]:
        """Return available candidate rows for compact validation table."""
        candidates: list[dict] = []
        seen: set[int] = set()
        for rank in (1, 2, 3):
            project_id = CENConnectionView._to_int(row.get(f"candidate_{rank}_project_id"))
            name = row.get(f"candidate_{rank}_project_name")
            score = row.get(f"candidate_{rank}_score")
            if project_id is None or project_id in seen or name is None or pd.isna(name):
                continue
            seen.add(project_id)
            score_text = "Sin puntaje"
            try:
                if pd.notna(score):
                    score_text = f"{float(score):.3f}"
            except Exception:
                score_text = str(score) if CENConnectionView._has_display_value(score) else "Sin puntaje"
            candidates.append(
                {
                    "rank": rank,
                    "project_id": int(project_id),
                    "project_name": CENConnectionView._display_value(name, "Sin nombre BD"),
                    "project_type": CENConnectionView._type_label(row.get(f"candidate_{rank}_project_type")) or "Sin tipo",
                    "nup": CENConnectionView._display_value(row.get(f"candidate_{rank}_project_nup"), "Sin NUP"),
                    "entity": CENConnectionView._display_value(row.get(f"candidate_{rank}_project_entity"), "Sin entidad"),
                    "technology": CENConnectionView._display_value(row.get(f"candidate_{rank}_technology"), "Sin tecnología"),
                    "capacity": CENConnectionView._display_value(row.get(f"candidate_{rank}_capacity"), "Sin capacidad"),
                    "location": CENConnectionView._display_value(row.get(f"candidate_{rank}_location"), "Sin ubicación"),
                    "bay": CENConnectionView._display_value(row.get(f"candidate_{rank}_bay"), "Sin paño"),
                    "action_summary": CENConnectionView._display_value(row.get(f"candidate_{rank}_action_summary"), "Sin cambios propuestos"),
                    "score_text": score_text,
                }
            )
        return candidates

    @staticmethod
    def _render_validation_cards(
        preview: pd.DataFrame,
        current_selected: list[int],
        current_candidate_overrides: dict[int, int],
        max_rows: int,
    ) -> tuple[list[int], dict[int, int]]:
        if preview.empty:
            st.info("No hay filas por validar.")
            return [], {}

        working = preview.copy()
        if "name_score" in working.columns:
            working["_score_sort"] = pd.to_numeric(working["name_score"], errors="coerce")
            working = working.sort_values("_score_sort", ascending=False, na_position="last").drop(columns=["_score_sort"])
        working = working.head(max_rows).copy()

        selected_ids: list[int] = []
        selected_candidate_overrides: dict[int, int] = {}
        current_selected_set = set(int(value) for value in current_selected or [])

        for _, row in working.iterrows():
            row_id = CENConnectionView._to_int(row.get("preview_row_id"))
            if row_id is None:
                continue

            options = CENConnectionView._candidate_options_for_row(row)
            if len(options) <= 1:
                CENConnectionView._render_compact_table(pd.DataFrame([row]), max_rows=1, include_candidates=False)
                continue

            current_project_id = current_candidate_overrides.get(row_id)
            default_option = "No aplicar"
            for label, project_id in options.items():
                if project_id == current_project_id:
                    default_option = label
                    break

            with st.container(border=True):
                st.markdown(f"**{CENConnectionView._short_text(row.get('project_name'), 120)}**")
                meta_left, meta_right = st.columns([2.2, 1.2])
                with meta_left:
                    action = CENConnectionView._action_label(row.get("record_action"))
                    changes = row.get("action_summary") or row.get("date_changes_text") or ""
                    st.caption(f"Acción: {action} · Cambios: {changes}")
                with meta_right:
                    score = row.get("score_display") or row.get("name_score") or ""
                    state = row.get("match_status_label") or row.get("match_status") or ""
                    st.caption(f"Estado: {CENConnectionView._status_label(state)} · Puntaje: {score}")

                sel_col, apply_col = st.columns([4, 1])
                with sel_col:
                    selected_label = st.selectbox(
                        "Proyecto seleccionado",
                        options=list(options.keys()),
                        index=list(options.keys()).index(default_option) if default_option in options else 0,
                        key=f"cen_connection_candidate_select_{row_id}",
                    )
                with apply_col:
                    apply_value = st.checkbox(
                        "Aplicar",
                        value=(row_id in current_selected_set and selected_label != "No aplicar"),
                        key=f"cen_connection_candidate_apply_{row_id}",
                    )

                selected_project_id = options.get(selected_label)
                if apply_value and selected_project_id is not None:
                    selected_ids.append(row_id)
                    selected_candidate_overrides[row_id] = int(selected_project_id)

        if len(preview) > max_rows:
            st.caption(f"Mostrando {max_rows} de {len(preview)} filas por validar.")

        return selected_ids, selected_candidate_overrides

    @staticmethod
    def _candidate_options_for_row(row: pd.Series) -> dict[str, int | None]:
        options: dict[str, int | None] = {"No aplicar": None}
        seen: set[int] = set()
        for rank in (1, 2, 3):
            project_id = CENConnectionView._to_int(row.get(f"candidate_{rank}_project_id"))
            name = row.get(f"candidate_{rank}_project_name")
            score = row.get(f"candidate_{rank}_score")
            if project_id is None or project_id in seen or pd.isna(name):
                continue
            seen.add(project_id)
            score_text = "s/p"
            try:
                if pd.notna(score):
                    score_text = f"{float(score):.3f}"
            except Exception:
                score_text = str(score)
            label = f"{rank} · {CENConnectionView._short_text(name, 95)} · {score_text}"
            options[label] = project_id
        return options

    @staticmethod
    def _to_int(value) -> int | None:
        try:
            if pd.isna(value):
                return None
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _render_compact_table(
        preview: pd.DataFrame,
        max_rows: int,
        include_candidates: bool = False,
    ) -> None:
        if preview.empty:
            st.info("No hay filas en esta categoría.")
            return

        compact = CENConnectionView._build_compact_preview(preview, include_candidates=False)
        st.dataframe(CENConnectionView._prepare_display_dataframe(compact.head(max_rows)), width="stretch", hide_index=True)
        if len(compact) > max_rows:
            st.caption(f"Mostrando {max_rows} de {len(compact)} filas.")

    @staticmethod
    def _build_compact_preview(preview: pd.DataFrame, include_candidates: bool) -> pd.DataFrame:
        working = preview.copy()
        if "name_score" in working.columns:
            working["_score_sort"] = pd.to_numeric(working["name_score"], errors="coerce")
            sort_cols = ["_score_sort"]
            ascending = [False]
            if "project_name" in working.columns:
                sort_cols.append("project_name")
                ascending.append(True)
            working = working.sort_values(sort_cols, ascending=ascending, na_position="last")
            working = working.drop(columns=["_score_sort"])
        if "preview_row_id" not in working.columns:
            working = working.reset_index(drop=True)
            working["preview_row_id"] = working.index.astype(int)
        if "can_apply" not in working.columns:
            working["can_apply"] = working.get("matched_project_id", pd.Series(dtype=object)).notna()

        base_cols = [
            "preview_row_id",
            "can_apply",
            "match_status_label",
            "record_action",
            "target_status",
            "action_summary",
            "connection_project_type",
            "nup",
            "project_name",
            "matched_project_name",
            "matched_project_nup",
            "score_display",
            "date_changes_text",
            "match_comment",
        ]
        cols = [col for col in base_cols if col in working.columns]
        compact = working[cols].copy()
        compact = compact.rename(
            columns={
                "preview_row_id": "ID fila",
                "can_apply": "Aplicable",
                "match_status_label": "Estado",
                "record_action": "Acción",
                "target_status": "Estado objetivo",
                "action_summary": "Cambios",
                "connection_project_type": "Tipo",
                "nup": "NUP archivo",
                "project_name": "Proyecto archivo",
                "matched_project_name": "Proyecto sugerido",
                "matched_project_nup": "NUP BD",
                "score_display": "Puntaje",
                "date_changes_text": "Fechas",
                "match_comment": "Comentario",
            }
        )
        if "Tipo" in compact.columns:
            compact["Tipo"] = compact["Tipo"].apply(CENConnectionView._type_label)
        if "Acción" in compact.columns:
            compact["Acción"] = compact["Acción"].apply(CENConnectionView._action_label)
        if "Estado objetivo" in compact.columns:
            compact["Estado objetivo"] = compact["Estado objetivo"].apply(CENConnectionView._target_status_label)
        if "Estado" in compact.columns:
            compact["Estado"] = compact["Estado"].apply(CENConnectionView._status_label)
        if "Comentario" in compact.columns:
            compact["Comentario"] = compact["Comentario"].apply(CENConnectionView._comment_label)
        for col in ["Proyecto archivo", "Proyecto sugerido", "Comentario", "Fechas"]:
            if col in compact.columns:
                compact[col] = compact[col].apply(lambda value: CENConnectionView._short_text(value, 95))
        return compact

    @staticmethod
    def _move_column_after(df: pd.DataFrame, column_name: str, after_column: str) -> pd.DataFrame:
        """Return a DataFrame with column_name placed immediately after after_column."""
        if column_name not in df.columns or after_column not in df.columns:
            return df
        columns = [col for col in df.columns if col != column_name]
        insert_at = columns.index(after_column) + 1
        columns.insert(insert_at, column_name)
        return df[columns]


    @staticmethod
    def _render_match_download(preview: pd.DataFrame, profile_key: str) -> None:
        csv_data = preview.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Descargar CSV de previsualización de cruce completo",
            data=csv_data,
            file_name=f"cen_connection_{profile_key}_match_preview.csv",
            mime="text/csv",
            width="stretch",
        )

    @staticmethod
    def _render_apply_status(preview: pd.DataFrame, selected_row_ids: list[int]) -> None:
        selected_count = len(selected_row_ids)
        selected_preview = preview.copy()
        if "preview_row_id" not in selected_preview.columns:
            selected_preview = selected_preview.reset_index(drop=True)
            selected_preview["preview_row_id"] = selected_preview.index.astype(int)

        if selected_count > 0:
            selected_preview = selected_preview.loc[
                selected_preview["preview_row_id"].isin(set(selected_row_ids))
            ].copy()
        else:
            selected_preview = selected_preview.iloc[0:0].copy()

        proposed_date_changes = int(
            selected_preview.get("date_changes_proposed", pd.Series(dtype=int)).fillna(0).sum()
        )
        proposed_nup_changes = int(
            selected_preview.get("would_update_nup", pd.Series(dtype=bool)).fillna(False).sum()
        )
        selected_without_project_id = int(
            selected_preview.get("matched_project_id", pd.Series(dtype=object)).isna().sum()
        )

        st.caption(
            f"Filas marcadas: {selected_count} · NUP a actualizar: {proposed_nup_changes} · "
            f"Fechas a escribir: {proposed_date_changes} · Sin ID proyecto: {selected_without_project_id}."
        )
        if selected_count == 0:
            st.info("Marca una o más filas en Propuestos o Por validar para habilitar la aplicación en base de datos.")
        elif selected_without_project_id > 0:
            st.warning("Hay filas marcadas sin ID de proyecto BD. Serán omitidas automáticamente al aplicar.")

    @staticmethod
    def _render_apply_summary(summary: dict) -> None:
        primary_rows = [
            ("Filas seleccionadas", summary.get("rows_received", 0)),
            ("Filas procesadas", summary.get("rows_processed", summary.get("rows_applied", 0) + summary.get("rows_skipped", 0))),
            ("Con cambios", summary.get("rows_with_changes", 0)),
            ("Sin cambios", summary.get("rows_without_changes", 0)),
            ("Omitidas", summary.get("rows_skipped", 0)),
            ("Errores", summary.get("errors", 0)),
        ]
        cols = st.columns(len(primary_rows))
        for col, (label, value) in zip(cols, primary_rows):
            col.metric(label, int(value or 0))

        detail_rows = [
            ("Proyectos enriquecidos", summary.get("projects_enriched", 0)),
            ("NUP actualizados", summary.get("nup_updated", 0)),
            ("Conflictos NUP", summary.get("nup_conflicts", 0)),
            ("Fechas insertadas", summary.get("dates_created", 0)),
            ("Fechas actualizadas", summary.get("dates_updated", 0)),
            ("Fechas sin cambios", summary.get("dates_unchanged", 0)),
            ("Estados a InService", summary.get("status_updated_to_in_service", 0)),
            ("Estados a UnderConstruction", summary.get("status_updated_to_under_construction", 0)),
            ("Estados a Planned", summary.get("status_updated_to_planned", 0)),
            ("Estados a Cancelled", summary.get("status_updated_to_cancelled", 0)),
            ("Cancelled omitidos por EO real", summary.get("status_cancelled_conflicts", 0)),
        ]
        with st.expander("Detalle del enriquecimiento", expanded=False):
            summary_df = pd.DataFrame(detail_rows, columns=["Métrica", "Valor"])
            st.dataframe(CENConnectionView._prepare_display_dataframe(summary_df), width="stretch", hide_index=True)

    @staticmethod
    def _render_download(records: pd.DataFrame, profile_key: str) -> None:
        csv_data = records.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Descargar CSV normalizado",
            data=csv_data,
            file_name=f"cen_connection_{profile_key}_normalized.csv",
            mime="text/csv",
            width="stretch",
        )


    @staticmethod
    def _type_label(value) -> str:
        labels = {
            "transmission": "Transmisión",
            "generation": "Generación",
            "generation_or_bess": "Generación / BESS",
            "bess": "BESS",
            "pmgd": "PMGD",
            "der": "DER",
            "unknown": "General",
        }
        if value is None or pd.isna(value):
            return ""
        return labels.get(str(value), str(value))


    @staticmethod
    def _action_label(value) -> str:
        labels = {
            "date_enrichment": "Fechas / NUP",
            "status_cancelled": "Estado cancelado",
        }
        if value is None or pd.isna(value):
            return ""
        return labels.get(str(value), str(value))

    @staticmethod
    def _target_status_label(value) -> str:
        labels = {
            "Cancelled": "Cancelled",
            "InService": "InService",
        }
        if value is None or pd.isna(value):
            return ""
        return labels.get(str(value), str(value))

    @staticmethod
    def _status_label(value) -> str:
        labels = {
            "matched_by_nup": "Propuesto por NUP",
            "matched_by_name": "Propuesto por nombre",
            "candidate_by_name": "Candidato por nombre",
            "ambiguous_name": "Ambiguo por nombre",
            "ambiguous_nup": "Ambiguo por NUP",
            "nup_type_mismatch": "NUP con tipo distinto",
            "not_found": "Sin candidato",
            "no_project_name": "Sin nombre usable",
            "auto_proposed": "Propuestos",
            "needs_validation": "Por validar",
            "no_candidate": "Sin candidato",
        }
        if value is None or pd.isna(value):
            return ""
        return labels.get(str(value), str(value))

    @staticmethod
    def _apply_status_label(value) -> str:
        labels = {
            "applied": "Aplicado",
            "omitted": "Omitido",
            "unchanged": "Sin cambios",
            "error": "Error",
            "conflict": "Conflicto",
        }
        if value is None or pd.isna(value):
            return ""
        return labels.get(str(value), str(value))

    @staticmethod
    def _comment_label(value) -> str:
        labels = {
            "High-confidence normalized-name match and compatible type.": "Coincidencia de nombre tratada con tipo compatible.",
            "NUP match with compatible type.": "Coincidencia por NUP con tipo compatible.",
            "Close name candidate. Review manually before applying.": "Candidato cercano por nombre. Revisar antes de aplicar.",
            "No sufficiently close compatible name candidate found.": "Sin candidato compatible suficientemente cercano.",
            "No project name available for name matching.": "Sin nombre disponible para comparar.",
            "Multiple close name candidates found.": "Existen varios candidatos cercanos por nombre.",
            "NUP found but project type is not compatible.": "NUP encontrado, pero el tipo de proyecto no es compatible.",
        }
        if value is None or pd.isna(value):
            return ""
        return labels.get(str(value), str(value))

    @staticmethod
    def _parser_status_label(value) -> str:
        labels = {
            "parsed": "Leída",
            "skipped": "Omitida",
            "ignored": "Ignorada",
            "empty": "Vacía",
            "error": "Error",
        }
        if value is None or pd.isna(value):
            return ""
        return labels.get(str(value), str(value))

    @staticmethod
    def _localize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        result = df.copy()
        column_labels = {
            "source_profile": "Perfil fuente",
            "source_detail": "Detalle fuente",
            "source_sheet": "Hoja",
            "sheet": "Hoja",
            "sheet_name": "Hoja",
            "row_number": "Fila archivo",
            "record_action": "Acción",
            "target_status": "Estado objetivo",
            "status": "Estado",
            "rows": "Filas",
            "rows_with_nup": "Filas con NUP",
            "rows_with_any_date": "Filas con fecha",
            "header_row": "Fila encabezado",
            "total_rows": "Filas",
            "parsed_rows": "Filas leídas",
            "normalized_rows": "Filas normalizadas",
            "skipped_rows": "Filas omitidas",
            "warnings": "Advertencias",
            "connection_project_type": "Tipo",
            "project_type": "Tipo",
            "nup": "NUP",
            "project_name": "Proyecto archivo",
            "matched_project_id": "ID proyecto BD",
            "matched_project_name": "Proyecto sugerido",
            "matched_project_type": "Tipo BD",
            "matched_project_nup": "NUP BD",
            "second_candidate_name": "Segundo candidato",
            "second_candidate_score": "Puntaje 2",
            "third_candidate_name": "Tercer candidato",
            "third_candidate_score": "Puntaje 3",
            "candidate_score_delta": "Diferencia",
            "match_status": "Estado",
            "match_status_label": "Estado",
            "match_group": "Grupo",
            "match_group_label": "Grupo",
            "name_score": "Puntaje",
            "candidate_count": "Candidatos",
            "top_candidates": "Top candidatos",
            "match_comment": "Comentario",
            "action_summary": "Cambios",
            "date_changes_proposed": "Fechas propuestas",
            "would_update_nup": "Actualiza NUP",
            "commissioning_actual": "PES real",
            "commissioning_estimated": "PES estimada",
            "cod_actual": "EO real",
            "cod_estimated": "EO estimada",
            "company": "Empresa",
            "region": "Región",
            "commune": "Comuna",
            "technology": "Tecnología",
        }
        for col in list(result.columns):
            if col in result.columns:
                if col in {"connection_project_type", "project_type", "matched_project_type"}:
                    result[col] = result[col].apply(CENConnectionView._type_label)
                elif col == "record_action":
                    result[col] = result[col].apply(CENConnectionView._action_label)
                elif col == "target_status":
                    result[col] = result[col].apply(CENConnectionView._target_status_label)
                elif col in {"match_status", "match_status_label", "match_group", "match_group_label"}:
                    result[col] = result[col].apply(CENConnectionView._status_label)
                elif col == "status":
                    result[col] = result[col].apply(CENConnectionView._parser_status_label)
        return CENConnectionView._prepare_display_dataframe(result.rename(columns=column_labels))

    @staticmethod
    def _prepare_display_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        """Return a dataframe safe for Streamlit display.

        PyArrow/Streamlit cannot render dataframes with duplicated column names.
        Some localized tables can naturally map multiple technical fields to the
        same Spanish label (for example status and match_status -> Estado). For
        visual tables we keep the first occurrence and drop duplicate display
        columns. The technical CSV downloads remain untouched.
        """
        if df is None or df.empty:
            return df
        result = df.copy()
        result = result.loc[:, ~pd.Index(result.columns).duplicated(keep="first")]
        return result.reset_index(drop=True)

    @staticmethod
    def _date_label(field: str) -> str:
        return {
            "commissioning_actual": "PES real",
            "commissioning_estimated": "PES estimada",
            "cod_actual": "EO real",
            "cod_estimated": "EO estimada",
        }.get(field, field)

    @staticmethod
    def _has_display_value(value) -> bool:
        if value is None:
            return False
        try:
            if pd.isna(value):
                return False
        except Exception:
            pass
        return str(value).strip() != ""

    @staticmethod
    def _display_value(value, fallback: str = "Sin información") -> str:
        if not CENConnectionView._has_display_value(value):
            return fallback
        return str(value)

    @staticmethod
    def _short_text(value, limit: int) -> str:
        if value is None or pd.isna(value):
            return ""
        text = str(value)
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "…"
