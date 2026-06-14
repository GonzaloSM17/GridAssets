import pandas as pd
import streamlit as st

from database.db_connection import get_sqlserver_engine
from services.db_queries import (
    PROJECTS_OVERVIEW_QUERY,
    PROJECT_DATES_QUERY,
    PROJECT_FEATURES_QUERY,
    PROJECT_LEGAL_DOCUMENTS_QUERY,
)
from ui.app_config import AppConfig


class ProjectDataService:
    @staticmethod
    @st.cache_data
    def load_projects() -> pd.DataFrame:
        engine = get_sqlserver_engine()

        with engine.connect() as connection:
            df = pd.read_sql_query(PROJECTS_OVERVIEW_QUERY, connection)

        df = ProjectDataService.format_date_columns(df)

        return df

    @staticmethod
    @st.cache_data
    def load_project_features() -> pd.DataFrame:
        engine = get_sqlserver_engine()

        with engine.connect() as connection:
            df = pd.read_sql_query(PROJECT_FEATURES_QUERY, connection)

        return df

    @staticmethod
    @st.cache_data
    def load_project_dates() -> pd.DataFrame:
        engine = get_sqlserver_engine()

        with engine.connect() as connection:
            df = pd.read_sql_query(PROJECT_DATES_QUERY, connection)

        df = ProjectDataService.format_multiple_date_columns(
            df,
            ["DateValue", "ExtractedAt"],
        )

        return df

    @staticmethod
    @st.cache_data
    def load_project_legal_documents() -> pd.DataFrame:
        engine = get_sqlserver_engine()

        with engine.connect() as connection:
            df = pd.read_sql_query(PROJECT_LEGAL_DOCUMENTS_QUERY, connection)

        return df

    @staticmethod
    def get_available_project_types(df: pd.DataFrame) -> list[str]:
        available_types = set(
            df["project_discriminator"].dropna().astype(str).unique().tolist()
        )

        ordered_types = [
            project_type
            for project_type in AppConfig.PROJECT_TYPE_ORDER
            if project_type in available_types
        ]

        extra_types = sorted(
            project_type
            for project_type in available_types
            if project_type not in AppConfig.PROJECT_TYPE_ORDER
        )

        return ordered_types + extra_types

    @staticmethod
    def filter_by_project_type(df: pd.DataFrame, project_type: str) -> pd.DataFrame:
        return df[df["project_discriminator"] == project_type].copy()

    @staticmethod
    def filter_by_project_id(df: pd.DataFrame, project_id: int) -> pd.DataFrame:
        if df.empty or "ProjectID" not in df.columns:
            return pd.DataFrame()

        return df[df["ProjectID"] == project_id].copy()

    @staticmethod
    def prepare_display_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        return df.drop(columns=AppConfig.TABLE_HIDDEN_COLUMNS, errors="ignore")

    @staticmethod
    def format_date_columns(df: pd.DataFrame) -> pd.DataFrame:
        date_columns = [
            "LastMilestoneDate",
        ]

        for column in date_columns:
            if column in df.columns:
                df[column] = pd.to_datetime(df[column], errors="coerce").dt.strftime(
                    "%d-%m-%Y"
                )

        return df

    @staticmethod
    def format_multiple_date_columns(
        df: pd.DataFrame,
        date_columns: list[str],
    ) -> pd.DataFrame:
        for column in date_columns:
            if column in df.columns:
                df[column] = pd.to_datetime(df[column], errors="coerce").dt.strftime(
                    "%d-%m-%Y"
                )

        return df

    @staticmethod
    def clean_empty_columns(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df

        clean_df = df.copy()
        clean_df = clean_df.dropna(axis=1, how="all")

        for column in clean_df.columns:
            if clean_df[column].astype(str).str.strip().eq("").all():
                clean_df = clean_df.drop(columns=[column])

        return clean_df

    @staticmethod
    def clear_loaded_data() -> None:
        cache_loaders = [
            ProjectDataService.load_projects,
            ProjectDataService.load_project_features,
            ProjectDataService.load_project_dates,
            ProjectDataService.load_project_legal_documents,
        ]

        for loader in cache_loaders:
            try:
                loader.clear()
            except Exception:
                pass
