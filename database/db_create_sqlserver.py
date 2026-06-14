# SQL Server Schema Creator
"""
Creates database schema in SQL Server based on SQLAlchemy models
Does NOT populate data - use db_populate.py (modified for SQL Server) to populate from Excel
"""

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from database.db_orm_model import Base


class SQLServerSchemaCreator:
    """Creates SQL Server database schema from SQLAlchemy models"""

    def __init__(self, sqlserver_conn_string: str):
        """
        Args:
            sqlserver_conn_string: SQL Server connection string
                Example with Windows Auth:
                'mssql+pyodbc://server/database?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes'
        """
        print("🔌 Connecting to SQL Server...")

        self.engine = create_engine(sqlserver_conn_string, echo=False)

        print("✅ Connected\n")

    def create_schema(self):
        """Create all tables in SQL Server"""
        print("🔧 Creating database schema...")
        try:
            Base.metadata.create_all(self.engine)
            print("✅ Schema created successfully\n")

            # List created tables
            print("📋 Tables created:")
            for table in Base.metadata.sorted_tables:
                print(f"  • {table.name}")

        except SQLAlchemyError as e:
            print(f"❌ Error creating schema: {e}")
            raise

    def drop_schema(self):
        """Drop all tables (use with caution!)"""
        print("⚠️  Dropping all tables...")
        try:
            Base.metadata.drop_all(self.engine)
            print("✅ All tables dropped\n")
        except SQLAlchemyError as e:
            print(f"❌ Error dropping schema: {e}")
            raise


if __name__ == "__main__":

    """Main process"""

    # Configuration
    SERVER = "SIGSANCHEZ0N04"  # ← CHANGE THIS to your server name
    DATABASE = "ProjectDB"  # ← CHANGE THIS if needed

    sqlserver_conn = (
        f"mssql+pyodbc://{SERVER}/{DATABASE}"
        "?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes"
    )

    print("=" * 60)
    print("SQL Server Schema Creator")
    print("=" * 60)
    print(f"Server: {SERVER}")
    print(f"Database: {DATABASE}")

    try:
        creator = SQLServerSchemaCreator(sqlserver_conn)

        # Create schema
        creator.create_schema()

        print("=" * 60)
        print("✅ Schema creation completed")
        print("=" * 60)
        print()
        print("📝 Next steps:")
        print("  1. Modify db_populate.py to use SQL Server connection")
        print("  2. Run: python db_populate.py")
        print("  3. Run: python db_scraper.py --source pgp")

    except Exception as e:
        print(f"\n❌ Schema creation failed: {e}")
        import traceback

        traceback.print_exc()
