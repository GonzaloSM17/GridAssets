"""DB Status View — compact top-right database health indicator.

Distinguishes four states:
  - server_unreachable : cannot reach the SQL Server instance at all
  - db_missing         : server OK but the target database does not exist
  - schema_missing     : database exists but one or more ORM tables are absent
  - ready              : all expected tables are present

Buttons:
  - schema_missing → "Crear schema"        (Base.metadata.create_all)
  - db_missing     → "Crear BD + Schema"   (CREATE DATABASE + create_all)
"""

from __future__ import annotations

import os

import streamlit as st
from sqlalchemy import create_engine, inspect as sa_inspect, text

from database.db_connection import get_connection_string, get_sqlserver_engine
from database.db_create_sqlserver import SQLServerSchemaCreator
from database.db_orm_model import Base

EXPECTED_TABLES: list[str] = sorted(
    {table.name for table in Base.metadata.sorted_tables}
)


def _master_connection_string() -> str:
    """Connection string targeting the 'master' database on the same server."""
    server = os.getenv("SQL_SERVER", "")
    driver = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
    return (
        f"mssql+pyodbc://{server}/master"
        f"?driver={driver.replace(' ', '+')}"
        "&trusted_connection=yes"
    )


class DBStatusView:

    # ------------------------------------------------------------------
    # Internal: DB check
    # ------------------------------------------------------------------

    @staticmethod
    def _check() -> dict:
        """Return a dict describing the current DB state.

        state values
        ------------
        server_unreachable : cannot reach the SQL Server instance
        db_missing         : server reachable but database does not exist
        schema_missing     : database exists but ORM tables are missing
        ready              : all expected tables are present
        """
        db_name = os.getenv("SQL_DATABASE", "")

        # Step 1: reach the server via master and check if the target DB exists
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

        # Step 2: database exists — check which ORM tables are present
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

    # ------------------------------------------------------------------
    # Internal: creation actions
    # ------------------------------------------------------------------

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
        """Create the SQL Server database and then the ORM schema.

        CREATE DATABASE must run outside a transaction (SQL Server requirement),
        so we use isolation_level='AUTOCOMMIT' on the master connection.
        """
        db_name = os.getenv("SQL_DATABASE", "")

        try:
            with st.spinner(f"Creando base de datos '{db_name}'..."):
                master_engine = create_engine(
                    _master_connection_string(),
                    echo=False,
                    isolation_level="AUTOCOMMIT",
                )
                with master_engine.connect() as conn:
                    conn.execute(text(f"CREATE DATABASE [{db_name}]"))

            st.success(f"✅ Base de datos '{db_name}' creada.")

        except Exception as exc:
            if "already exists" in str(exc).lower():
                st.info(
                    f"La base de datos '{db_name}' ya existía. Verificando schema..."
                )
            else:
                st.error(f"❌ No se pudo crear la base de datos: {exc}")
                st.caption(
                    "El usuario de Windows puede no tener permisos para crear bases de datos "
                    "(se requiere rol *sysadmin* o *dbcreator* en SQL Server). "
                    "Solicita al DBA que cree la base de datos manualmente y vuelve a intentarlo."
                )
                return

        DBStatusView._create_schema()

    # ------------------------------------------------------------------
    # Public: render
    # ------------------------------------------------------------------

    @staticmethod
    def render_status_panel() -> None:
        """DB status panel designed for use inside a column."""
        st.markdown(
            """
            <div class="section-card section-card-blue">
                <div class="section-title">Base de datos</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        verify = st.button(
            "🔍 Verificar",
            use_container_width=True,
            key="db_verify_button",
            help="Comprueba la conexión a SQL Server y verifica que las tablas del schema existan.",
        )

        if verify or "db_status" not in st.session_state:
            with st.spinner("Verificando..."):
                st.session_state["db_status"] = DBStatusView._check()

        status: dict = st.session_state.get("db_status", {})
        if not status:
            return

        state = status["state"]
        db_name = status.get("db_name", "")

        if state == "server_unreachable":
            st.error("🔴 Sin conexión")
            with st.expander("Ver error"):
                st.caption(status["error"])

        elif state == "db_missing":
            st.warning("🟡 BD no existe")
            st.caption(f"`{db_name}`")
            if st.button(
                "➕ Crear BD + Schema",
                type="primary",
                use_container_width=True,
                key="db_create_full_button",
                help=f"Crea '{db_name}' en SQL Server y genera todas las tablas ORM.",
            ):
                DBStatusView._create_database_and_schema()

        elif state == "schema_missing":
            n = len(status["tables_missing"])
            st.warning(f"🟡 {n} tablas faltantes")
            with st.expander("Ver tablas"):
                for table in status["tables_missing"]:
                    st.caption(f"• {table}")
            if st.button(
                "⚙️ Crear schema",
                type="primary",
                use_container_width=True,
                key="db_create_schema_button",
                help="Ejecuta Base.metadata.create_all() en la BD configurada.",
            ):
                DBStatusView._create_schema()

        else:  # ready
            st.success("🟢 BD operativa")
            st.caption(f"`{db_name}` — {len(status['tables_present'])} tablas")
