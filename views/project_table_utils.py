from html import escape
from textwrap import dedent
from typing import Iterable

import pandas as pd
import streamlit as st


class ProjectTableUtils:
    """Shared table and record rendering helpers for project views."""

    FEATURE_FIELD_ORDER = [
        "Technology",
        "PowerCapacity",
        "GenerationTotalCapacity",
        "TransmissionTotalCapacity",
        "StorageCapacity",
        "VoltageLevel",
        "BayName",
        "Location",
        "ProjectEntityName",
        "ProjectName",
    ]

    FEATURE_FIELD_LABELS = {
        "ProjectID": "ID proyecto",
        "ProjectName": "Proyecto",
        "ProjectEntityName": "Titular",
        "VoltageLevel": "Nivel de tensión",
        "TransmissionTotalCapacity": "Capacidad transmisión",
        "GenerationTotalCapacity": "Capacidad generación",
        "PowerCapacity": "Potencia",
        "StorageCapacity": "Almacenamiento",
        "BayName": "Paño",
        "Technology": "Tecnología",
        "Location": "Ubicación",
    }

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
            "StatusName": 130,
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
            "DateValue": 115,
            "ExtractedAt": 115,
            "MilestoneName": 190,
            "SourceName": 115,
            "URL": 320,
            "PGP_URL": 320,
            "SEO_URL": 280,
        }

        link_columns = {"URL", "PGP_URL", "SEO_URL"}
        label_map = {
            "StatusName": "Status",
            "ProjectName": "Proyecto",
            "ProjectEntityName": "Titular",
            "LastMilestoneName": "Último hito",
            "LastMilestoneSource": "Fuente hito",
            "LastMilestoneDate": "Fecha hito",
            "MilestoneName": "Hito",
            "DateValue": "Fecha",
            "SourceName": "Fuente",
            "ExtractedAt": "Extraído",
        }

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

            label = label_map.get(str(column), str(column))

            if str(column) in link_columns:
                column_config[column] = st.column_config.LinkColumn(
                    label=label,
                    width=final_width,
                    display_text="Abrir",
                )
            else:
                column_config[column] = st.column_config.Column(
                    label=label,
                    width=final_width,
                )

        return column_config

    @staticmethod
    def normalize_field_name(field_name: object) -> str:
        """Normalize a dataframe field name for comparison."""
        return str(field_name).strip().lower().replace(" ", "_")

    @staticmethod
    def is_hidden_field(field_name: object, hidden_fields: Iterable[str]) -> bool:
        """Return True when a field should be hidden, case-insensitively."""
        normalized_field_name = ProjectTableUtils.normalize_field_name(field_name)
        normalized_hidden_fields = {
            ProjectTableUtils.normalize_field_name(hidden_field)
            for hidden_field in hidden_fields
        }
        return normalized_field_name in normalized_hidden_fields

    @staticmethod
    def get_visible_record_fields(
        df: pd.DataFrame,
        hidden_fields: set[str] | None = None,
    ) -> list[tuple[str, object]]:
        """Return non-empty fields from the first row, excluding internal fields."""
        if df.empty:
            return []

        hidden_fields = hidden_fields or set()
        record = df.iloc[0].to_dict()
        visible_fields = []

        empty_markers = {"", "None", "none", "nan", "NaT", "nat", "<NA>", "NULL", "null"}

        for field_name, field_value in record.items():
            if ProjectTableUtils.is_hidden_field(field_name, hidden_fields):
                continue
            if pd.isna(field_value):
                continue

            field_value_text = str(field_value).strip()
            if field_value_text in empty_markers:
                continue

            visible_fields.append((str(field_name), field_value))

        return ProjectTableUtils.sort_feature_fields(visible_fields)

    @staticmethod
    def sort_feature_fields(fields: list[tuple[str, object]]) -> list[tuple[str, object]]:
        """Sort feature fields so generation/transmission values appear in a stable order."""
        order = {
            field_name: index
            for index, field_name in enumerate(ProjectTableUtils.FEATURE_FIELD_ORDER)
        }
        return sorted(fields, key=lambda item: (order.get(item[0], 999), item[0]))

    @staticmethod
    def humanize_field_name(field_name: str) -> str:
        """Convert internal field names into compact labels for the UI."""
        return ProjectTableUtils.FEATURE_FIELD_LABELS.get(field_name, field_name)

    @staticmethod
    def format_field_value(field_value: object) -> str:
        """Format raw values for compact feature cards."""
        if pd.isna(field_value):
            return ""

        if isinstance(field_value, bool):
            return "Sí" if field_value else "No"

        if isinstance(field_value, float):
            if field_value.is_integer():
                return str(int(field_value))
            return f"{field_value:,.2f}".rstrip("0").rstrip(".")

        return str(field_value).strip()

    @staticmethod
    def render_vertical_record(
        df: pd.DataFrame,
        hidden_fields: set[str] | None = None,
        empty_message: str = "No hay características para mostrar.",
    ) -> None:
        """Render a compact, responsive key-value grid for project features."""
        visible_fields = ProjectTableUtils.get_visible_record_fields(
            df=df,
            hidden_fields=hidden_fields,
        )

        if not visible_fields:
            st.info(empty_message)
            return

        cards_html = []
        for field_name, field_value in visible_fields:
            field_value_text = ProjectTableUtils.format_field_value(field_value)
            if not field_value_text:
                continue

            safe_field_name = escape(ProjectTableUtils.humanize_field_name(field_name))
            safe_field_value = escape(field_value_text)
            cards_html.append(
                (
                    '<div class="feature-card">'
                    f'<div class="feature-label">{safe_field_name}</div>'
                    f'<div class="feature-value">{safe_field_value}</div>'
                    '</div>'
                )
            )

        if not cards_html:
            st.info(empty_message)
            return

        styles = """
<style>
.features-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 0.55rem;
    width: 100%;
    margin-top: 0.25rem;
}
.feature-card {
    border: 1px solid rgba(49, 51, 63, 0.18);
    border-radius: 0.65rem;
    padding: 0.55rem 0.65rem;
    background: rgba(250, 250, 250, 0.72);
    min-width: 0;
    overflow-wrap: anywhere;
}
.feature-label {
    font-size: 0.72rem;
    line-height: 1.1;
    font-weight: 700;
    color: rgba(49, 51, 63, 0.68);
    text-transform: uppercase;
    letter-spacing: 0.02em;
    margin-bottom: 0.25rem;
}
.feature-value {
    font-size: 0.95rem;
    line-height: 1.25;
    font-weight: 600;
    color: rgb(49, 51, 63);
    word-break: normal;
    overflow-wrap: anywhere;
}
</style>
""".strip()
        html = f'{styles}<div class="features-grid">{"".join(cards_html)}</div>'
        st.markdown(html, unsafe_allow_html=True)
