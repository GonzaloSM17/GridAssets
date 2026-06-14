# database/db_connection.py

from dotenv import load_dotenv
from sqlalchemy import create_engine
import os

load_dotenv()


def get_sqlserver_engine(echo: bool = False):
    server = os.getenv("SQL_SERVER")
    database = os.getenv("SQL_DATABASE")
    driver = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")

    if not server:
        raise ValueError("Missing SQL_SERVER in .env file")

    if not database:
        raise ValueError("Missing SQL_DATABASE in .env file")

    connection_string = (
        f"mssql+pyodbc://{server}/{database}"
        f"?driver={driver.replace(' ', '+')}"
        "&trusted_connection=yes"
    )

    return create_engine(connection_string, echo=echo)


def get_connection_string():
    server = os.getenv("SQL_SERVER")
    database = os.getenv("SQL_DATABASE")
    driver = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")

    if not server:
        raise ValueError("Missing SQL_SERVER in .env file")

    if not database:
        raise ValueError("Missing SQL_DATABASE in .env file")

    return (
        f"mssql+pyodbc://{server}/{database}"
        f"?driver={driver.replace(' ', '+')}"
        "&trusted_connection=yes"
    )


if __name__ == "__main__":
    engine = get_sqlserver_engine()

    with engine.connect():
        print("Successful connection to SQL Server")
