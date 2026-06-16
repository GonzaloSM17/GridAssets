"""Database status view and creation actions for SQL Server."""

from __future__ import annotations

import os

import streamlit as st
from sqlalchemy import create_engine, inspect as sa_inspect, text

from database.db_connection import get_connection_string, get_sqlserver_engine
from database.db_create_sqlserver import SQLServerSchemaCreator
from database.db_orm_model import Base


EXPECTED_TABLES: list[str] = sorted({table.name for table in Base.metadata.sorted_tables})


def _master_connection_string() -> str:
    """Build a connection string targeting the master database."""
    server = os.getenv("SQL_SERVER", "")
    driver = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
    return (
        f"mssql+pyodbc://{server}/master"
        f"?driver={driver.replace(' ', '+')}"
        "&trusted_connection=yes"
    )


def _quote_database_name(database_name: str) -> str:
    """Quote a SQL Server database name using bracket quoting."""
    return f"[{database_name.replace(']', ']]')}]"


class DBStatusView:
    """Compact database health indicator and database/schema actions."""

    @staticmethod
    def get_status(force_refresh: bool = False) -> dict:
        """Return cached DB status, refreshing it when requested."""
        if force_refresh or "db_status" not in st.session_state:
            with st.spinner("Verificando..."):
                st.session_state["db_status"] = DBStatusView._check()
        return st.session_state.get("db_status", {})

    @staticmethod
    def is_ready(status: dict | None = None) -> bool:
        """Return True when the target database and ORM schema are ready."""
        status = status or DBStatusView.get_status()
        return status.get("state") == "ready"

    @staticmethod
    def _check() -> dict:
        """Return the current server, database and ORM schema state."""
        db_name = os.getenv("SQL_DATABASE", "")

        try:
            master_engine = create_engine(_master_connection_string(), echo=False)
            with master_engine.connect() as conn:
                row = conn.execute(
                    text("SELECT name FROM sys.databases WHERE name = :n"),
                    {"n": db_name},
                ).fetchone()
                db_exists = row is not None
        except Exception as exc:
            return {
                "state": "server_unreachable",
                "tables_present": [],
                "tables_missing": EXPECTED_TABLES,
                "error": str(exc),
                "db_name": db_name,
            }

        if not db_exists:
            return {
                "state": "db_missing",
                "tables_present": [],
                "tables_missing": EXPECTED_TABLES,
                "error": None,
                "db_name": db_name,
            }

        try:
            engine = get_sqlserver_engine()
            with engine.connect():
                existing = set(sa_inspect(engine).get_table_names())
        except Exception as exc:
            return {
                "state": "server_unreachable",
                "tables_present": [],
                "tables_missing": EXPECTED_TABLES,
                "error": str(exc),
                "db_name": db_name,
            }

        expected = set(EXPECTED_TABLES)
        missing = sorted(expected - existing)
        present = sorted(expected & existing)

        return {
            "state": "ready" if not missing else "schema_missing",
            "tables_present": present,
            "tables_missing": missing,
            "error": None,
            "db_name": db_name,
        }

    @staticmethod
    def _create_schema() -> None:
        """Create ORM tables on an existing database."""
        try:
            with st.spinner("Creando schema en SQL Server..."):
                creator = SQLServerSchemaCreator(get_connection_string())
                creator.create_schema()

            st.success("✅ Schema creado correctamente.")
            st.session_state.pop("db_status", None)
            st.rerun()
        except Exception as exc:
            st.error(f"❌ Error creando schema: {exc}")

    @staticmethod
    def _create_database_and_schema() -> None:
        """Create the target SQL Server database and then the ORM schema."""
        db_name = os.getenv("SQL_DATABASE", "")
        quoted_db_name = _quote_database_name(db_name)

        try:
            with st.spinner(f"Creando base de datos '{db_name}'..."):
                master_engine = create_engine(
                    _master_connection_string(),
                    echo=False,
                    isolation_level="AUTOCOMMIT",
                )
                with master_engine.connect() as conn:
                    conn.execute(text(f"CREATE DATABASE {quoted_db_name}"))

            st.success(f"✅ Base de datos '{db_name}' creada.")
        except Exception as exc:
            if "already exists" in str(exc).lower():
                st.info(f"La base de datos '{db_name}' ya existía. Verificando schema...")
            else:
                st.error(f"❌ No se pudo crear la base de datos: {exc}")
                st.caption(
                    "El usuario de Windows puede no tener permisos para crear bases "
                    "de datos. Se requiere rol sysadmin o dbcreator en SQL Server. "
                    "Solicita al DBA que cree la base de datos manualmente y vuelve "
                    "a intentarlo."
                )
                return

        DBStatusView._create_schema()

    @staticmethod
    def render_status_panel() -> None:
        """Render the database status panel."""
        st.markdown("**Base de datos**")

        verify = st.button(
            " Verificar",
            use_container_width=True,
            key="db_verify_button",
            help="Comprueba la conexión a SQL Server y verifica las tablas ORM.",
        )

        status = DBStatusView.get_status(force_refresh=verify)
        if not status:
            return

        state = status.get("state")
        db_name = status.get("db_name", "")

        if state == "server_unreachable":
            st.error(" Sin conexión")
            with st.expander("Ver error"):
                st.caption(status.get("error"))
            return

        if state == "db_missing":
            st.warning(" BD no existe")
            st.caption(f"`{db_name}`")
            if st.button(
                "➕ Crear BD + Schema",
                type="primary",
                use_container_width=True,
                key="db_create_full_button",
                help=f"Crea '{db_name}' en SQL Server y genera las tablas ORM.",
            ):
                DBStatusView._create_database_and_schema()
            return

        if state == "schema_missing":
            missing_tables = status.get("tables_missing", [])
            st.warning(f" {len(missing_tables)} tablas faltantes")
            with st.expander("Ver tablas"):
                for table in missing_tables:
                    st.caption(f"• {table}")
            if st.button(
                "⚙️ Crear schema",
                type="primary",
                use_container_width=True,
                key="db_create_schema_button",
                help="Ejecuta Base.metadata.create_all() en la BD configurada.",
            ):
                DBStatusView._create_schema()
            return

        st.success(" BD operativa")
        st.caption(f"`{db_name}` — {len(status.get('tables_present', []))} tablas")
