import streamlit as st

from ui.app_config import AppConfig


class AppStyle:
    @staticmethod
    def apply() -> None:
        colors = AppConfig.COLORS

        custom_css = f"""
        <style>
            .stApp {{
                background:
                    radial-gradient(
                        circle at top left,
                        rgba(111, 174, 46, 0.16),
                        transparent 28%
                    ),
                    radial-gradient(
                        circle at top right,
                        rgba(0, 124, 146, 0.12),
                        transparent 30%
                    ),
                    linear-gradient(
                        135deg,
                        {colors["background"]} 0%,
                        {colors["background_alt"]} 100%
                    );
            }}

            section[data-testid="stSidebar"] {{
                background-color: {colors["surface"]};
                border-right: 1px solid {colors["border"]};
            }}

            .block-container {{
                padding-top: 1.25rem;
                padding-bottom: 1.5rem;
                max-width: 96%;
            }}

            .main-title {{
                font-size: 2.25rem;
                font-weight: 850;
                color: {colors["text"]};
                letter-spacing: -0.045em;
                margin-bottom: 0.2rem;
                line-height: 1.08;
            }}

            .subtitle {{
                font-size: 1.02rem;
                color: {colors["text_muted"]};
                margin-bottom: 1.1rem;
            }}

            .subtitle em {{
                font-style: italic;
                text-transform: lowercase;
            }}

            .header-shell {{
                padding: 1.35rem 1.55rem;
                border-radius: 1.25rem;
                background:
                    linear-gradient(
                        135deg,
                        rgba(250, 251, 250, 0.94),
                        rgba(240, 245, 242, 0.92)
                    );
                border: 1px solid {colors["border"]};
                box-shadow: 0 10px 30px rgba(47, 52, 55, 0.06);
                margin-bottom: 1rem;
                position: relative;
                overflow: hidden;
            }}

            .header-shell::before {{
                content: "";
                position: absolute;
                left: 0;
                top: 0;
                width: 7px;
                height: 100%;
                background: linear-gradient(
                    180deg,
                    {colors["red_orange"]},
                    {colors["orange"]},
                    {colors["yellow_green"]},
                    {colors["green"]},
                    {colors["blue"]}
                );
            }}

            .header-accent {{
                width: 96px;
                height: 6px;
                background: linear-gradient(
                    90deg,
                    {colors["green"]},
                    {colors["blue"]},
                    {colors["orange"]}
                );
                border-radius: 999px;
                margin-top: 0.9rem;
            }}

            .section-card {{
                padding: 0.95rem 1.1rem;
                border-radius: 1rem;
                background:
                    linear-gradient(
                        135deg,
                        rgba(250, 251, 250, 0.96),
                        rgba(240, 245, 242, 0.88)
                    );
                border: 1px solid {colors["border"]};
                margin-bottom: 0.9rem;
                box-shadow: 0 6px 18px rgba(47, 52, 55, 0.045);
            }}

            .section-card-green {{
                border-left: 5px solid {colors["green"]};
            }}

            .section-card-blue {{
                border-left: 5px solid {colors["blue"]};
            }}

            .section-card-orange {{
                border-left: 5px solid {colors["orange"]};
            }}

            .section-title {{
                font-size: 1.08rem;
                font-weight: 760;
                color: {colors["text"]};
                margin-bottom: 0.2rem;
            }}

            .section-caption {{
                font-size: 0.9rem;
                color: {colors["text_muted"]};
                margin-bottom: 0;
            }}

            div[data-testid="stMetric"] {{
                background:
                    linear-gradient(
                        145deg,
                        {colors["surface"]},
                        {colors["surface_soft"]}
                    );
                border: 1px solid {colors["border"]};
                padding: 0.75rem 0.85rem;
                border-radius: 1rem;
                box-shadow: 0 6px 16px rgba(47, 52, 55, 0.045);
            }}

            div[data-testid="stMetric"] label {{
                color: {colors["text_muted"]};
                font-weight: 600;
            }}

            div[data-testid="stMetricValue"] {{
                color: {colors["green_dark"]};
                font-weight: 800;
            }}

            div[data-testid="stHorizontalBlock"] > div:nth-child(2) div[data-testid="stMetricValue"] {{
                color: {colors["green"]};
            }}

            div[data-testid="stHorizontalBlock"] > div:nth-child(3) div[data-testid="stMetricValue"] {{
                color: {colors["blue"]};
            }}

            div[data-testid="stHorizontalBlock"] > div:nth-child(4) div[data-testid="stMetricValue"] {{
                color: {colors["orange"]};
            }}

            div[data-testid="stHorizontalBlock"] > div:nth-child(5) div[data-testid="stMetricValue"] {{
                color: {colors["blue_dark"]};
            }}

            div[data-testid="stTabs"] {{
                background-color: rgba(250, 251, 250, 0.55);
                padding: 0.3rem;
                border-radius: 1rem;
                border: 1px solid rgba(216, 224, 220, 0.75);
            }}

            button[data-baseweb="tab"] {{
                font-weight: 680;
                border-radius: 0.75rem;
                color: {colors["text_muted"]};
            }}

            button[data-baseweb="tab"][aria-selected="true"] {{
                color: {colors["green_dark"]};
                background-color: {colors["surface_green"]};
            }}

            div[data-testid="stDataFrame"] {{
                border: 1px solid {colors["border"]};
                border-radius: 1rem;
                overflow: hidden;
                background-color: {colors["surface"]};
                box-shadow: 0 8px 24px rgba(47, 52, 55, 0.045);
            }}

            .detail-panel {{
                min-height: 560px;
                padding: 1rem;
                border-radius: 1rem;
                background:
                    linear-gradient(
                        145deg,
                        rgba(250, 251, 250, 0.96),
                        rgba(240, 245, 242, 0.88)
                    );
                border: 1px solid {colors["border"]};
                box-shadow: 0 8px 24px rgba(47, 52, 55, 0.045);
            }}

            .detail-empty {{
                padding: 1.2rem;
                border-radius: 1rem;
                background-color: {colors["surface_blue"]};
                border: 1px solid {colors["border"]};
                color: {colors["text_muted"]};
                font-size: 0.95rem;
            }}

            a {{
                color: {colors["blue"]};
            }}

            hr {{
                border-color: {colors["border"]};
            }}
        </style>
        """

        st.markdown(custom_css, unsafe_allow_html=True)
