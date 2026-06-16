from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from database.db_connection import get_sqlserver_engine
from database.db_orm_model import ElectricalModel, Project, ProjectElectricalModel, Software


DEFAULT_SOFTWARE_NAMES = [
    "DIgSILENT PowerFactory",
    "PSS/E",
    "PSCAD",
    "EMTP-RV",
]


def _get_session():
    """Create a new SQLAlchemy session using the project SQL Server engine."""

    engine = get_sqlserver_engine()
    Session = sessionmaker(bind=engine)
    return Session()


def _normalize_cutoff_datetime(cod_cutoff_date: date | datetime) -> datetime:
    """Convert a date picker value into an inclusive end-of-day datetime."""

    if isinstance(cod_cutoff_date, datetime):
        return cod_cutoff_date

    return datetime.combine(cod_cutoff_date, time.max)


def ensure_default_software() -> None:
    """Create default software records if they do not exist yet."""

    session = _get_session()
    try:
        _ensure_default_software(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ensure_default_software(session) -> None:
    """Internal helper to create default software records inside an open session."""

    existing_names = {
        row.SoftwareName
        for row in session.query(Software).all()
    }

    for software_name in DEFAULT_SOFTWARE_NAMES:
        if software_name not in existing_names:
            session.add(
                Software(
                    SoftwareName=software_name,
                    IsActive=True,
                )
            )

    session.flush()


def list_software(include_inactive: bool = False) -> pd.DataFrame:
    """Return the software catalog."""

    session = _get_session()
    try:
        _ensure_default_software(session)
        session.commit()

        query = session.query(Software)

        if not include_inactive:
            query = query.filter(Software.IsActive == 1)

        rows = query.order_by(Software.SoftwareName).all()

        return pd.DataFrame(
            [
                {
                    "SoftwareID": row.SoftwareID,
                    "SoftwareName": row.SoftwareName,
                    "IsActive": bool(row.IsActive),
                }
                for row in rows
            ]
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_models(include_inactive: bool = False) -> pd.DataFrame:
    """Return electrical models with their software."""

    session = _get_session()
    try:
        _ensure_default_software(session)
        session.commit()

        query = (
            session.query(ElectricalModel, Software)
            .join(Software, ElectricalModel.SoftwareID == Software.SoftwareID)
        )

        if not include_inactive:
            query = query.filter(ElectricalModel.IsActive == 1)

        rows = (
            query
            .order_by(Software.SoftwareName, ElectricalModel.ElectricalModelName)
            .all()
        )

        return pd.DataFrame(
            [
                {
                    "ElectricalModelID": model.ElectricalModelID,
                    "ElectricalModelName": model.ElectricalModelName,
                    "SoftwareID": software.SoftwareID,
                    "SoftwareName": software.SoftwareName,
                    "IsActive": bool(model.IsActive),
                }
                for model, software in rows
            ]
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_model(electrical_model_name: str, software_id: int) -> None:
    """Create or reactivate an electrical model for a software."""

    cleaned_name = str(electrical_model_name or "").strip()

    if not cleaned_name:
        raise ValueError("El nombre del modelo eléctrico no puede estar vacío.")

    session = _get_session()
    try:
        _ensure_default_software(session)

        software = session.get(Software, int(software_id))
        if not software:
            raise ValueError(f"No existe el software con ID {software_id}.")

        existing = (
            session.query(ElectricalModel)
            .filter(
                ElectricalModel.SoftwareID == int(software_id),
                ElectricalModel.ElectricalModelName == cleaned_name,
            )
            .one_or_none()
        )

        if existing:
            existing.IsActive = True
        else:
            session.add(
                ElectricalModel(
                    ElectricalModelName=cleaned_name,
                    SoftwareID=int(software_id),
                    IsActive=True,
                )
            )

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def deactivate_model(electrical_model_id: int) -> None:
    """Soft-delete an electrical model."""

    session = _get_session()
    try:
        model = session.get(ElectricalModel, int(electrical_model_id))

        if not model:
            raise ValueError(
                f"No existe el modelo eléctrico con ID {electrical_model_id}."
            )

        model.IsActive = False
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_project_modeling_status(project_id: int) -> pd.DataFrame:
    """Return all active electrical models and the project True/False status."""

    session = _get_session()
    try:
        project = session.get(Project, int(project_id))
        if not project:
            raise ValueError(f"No se encontró el proyecto con ID {project_id}.")

        models = (
            session.query(ElectricalModel, Software)
            .join(Software, ElectricalModel.SoftwareID == Software.SoftwareID)
            .filter(ElectricalModel.IsActive == 1)
            .order_by(Software.SoftwareName, ElectricalModel.ElectricalModelName)
            .all()
        )

        links = (
            session.query(ProjectElectricalModel)
            .filter(ProjectElectricalModel.ProjectID == int(project_id))
            .all()
        )

        modeled_by_model_id = {
            int(link.ElectricalModelID): bool(link.IsModeled)
            for link in links
        }

        return pd.DataFrame(
            [
                {
                    "ElectricalModelID": model.ElectricalModelID,
                    "SoftwareName": software.SoftwareName,
                    "ElectricalModelName": model.ElectricalModelName,
                    "IsModeled": modeled_by_model_id.get(
                        int(model.ElectricalModelID),
                        False,
                    ),
                }
                for model, software in models
            ]
        )
    finally:
        session.close()


def update_project_modeling_status(
    project_id: int,
    electrical_model_id: int,
    is_modeled: bool,
) -> None:
    """Insert or update the modeling status for a project/model pair."""

    session = _get_session()
    try:
        project = session.get(Project, int(project_id))
        if not project:
            raise ValueError(f"No se encontró el proyecto con ID {project_id}.")

        electrical_model = session.get(ElectricalModel, int(electrical_model_id))
        if not electrical_model:
            raise ValueError(
                f"No existe el modelo eléctrico con ID {electrical_model_id}."
            )

        existing = (
            session.query(ProjectElectricalModel)
            .filter(
                ProjectElectricalModel.ProjectID == int(project_id),
                ProjectElectricalModel.ElectricalModelID == int(electrical_model_id),
            )
            .one_or_none()
        )

        if existing:
            existing.IsModeled = bool(is_modeled)
        else:
            session.add(
                ProjectElectricalModel(
                    ProjectID=int(project_id),
                    ElectricalModelID=int(electrical_model_id),
                    IsModeled=bool(is_modeled),
                )
            )

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def preview_projects_for_bulk_modeling_by_cod(
    electrical_model_id: int,
    cod_cutoff_date: date | datetime,
    project_type: str | None = None,
    only_unmodeled: bool = True,
) -> pd.DataFrame:
    """Preview projects whose latest COD_Actual is less than or equal to cutoff."""

    cutoff_datetime = _normalize_cutoff_datetime(cod_cutoff_date)

    selected_project_type = None
    if project_type and project_type != "all":
        selected_project_type = project_type

    only_unmodeled_int = 1 if only_unmodeled else 0

    query = text(
        """
        WITH LatestCOD AS (
            SELECT
                p.ProjectID,
                p.ProjectName,
                p.NUP,
                p.project_discriminator,
                pe.ProjectEntityName,
                rd.DateValue AS COD_Actual,
                rd.ExtractedAt,
                ROW_NUMBER() OVER (
                    PARTITION BY p.ProjectID
                    ORDER BY rd.ExtractedAt DESC, rd.DateValue DESC
                ) AS RowNumber
            FROM Project p
            INNER JOIN RelevantDate rd
                ON p.ProjectID = rd.ProjectID
            INNER JOIN MilestoneType mt
                ON rd.MilestoneTypeID = mt.MilestoneTypeID
            LEFT JOIN ProjectEntity pe
                ON p.ProjectEntityID = pe.ProjectEntityID
            WHERE mt.MilestoneName = 'COD_Actual'
        )
        SELECT
            c.ProjectID,
            c.ProjectName,
            c.NUP,
            c.ProjectEntityName,
            c.project_discriminator,
            c.COD_Actual,
            CAST(ISNULL(pem.IsModeled, 0) AS int) AS IsCurrentlyModeled
        FROM LatestCOD c
        LEFT JOIN ProjectElectricalModel pem
            ON c.ProjectID = pem.ProjectID
            AND pem.ElectricalModelID = :electrical_model_id
        WHERE c.RowNumber = 1
            AND c.COD_Actual <= :cutoff_datetime
            AND (
                :project_type IS NULL
                OR c.project_discriminator = :project_type
            )
            AND (
                :only_unmodeled = 0
                OR ISNULL(pem.IsModeled, 0) = 0
            )
        ORDER BY c.COD_Actual DESC, c.ProjectID;
        """
    )

    engine = get_sqlserver_engine()

    with engine.connect() as connection:
        df = pd.read_sql_query(
            query,
            connection,
            params={
                "electrical_model_id": int(electrical_model_id),
                "cutoff_datetime": cutoff_datetime,
                "project_type": selected_project_type,
                "only_unmodeled": only_unmodeled_int,
            },
        )

    if df.empty:
        return df

    df["IsCurrentlyModeled"] = df["IsCurrentlyModeled"].astype(bool)
    df["COD_Actual"] = pd.to_datetime(df["COD_Actual"], errors="coerce")

    return df


def bulk_set_modeled_by_cod_date(
    electrical_model_id: int,
    cod_cutoff_date: date | datetime,
    project_type: str | None = None,
    only_unmodeled: bool = True,
) -> dict[str, Any]:
    """Mark as modeled all projects whose latest COD_Actual is <= cutoff."""

    preview_df = preview_projects_for_bulk_modeling_by_cod(
        electrical_model_id=electrical_model_id,
        cod_cutoff_date=cod_cutoff_date,
        project_type=project_type,
        only_unmodeled=only_unmodeled,
    )

    if preview_df.empty:
        return {
            "matched": 0,
            "updated": 0,
            "created": 0,
            "changed": 0,
            "already_modeled": 0,
        }

    project_ids = [
        int(project_id)
        for project_id in preview_df["ProjectID"].dropna().astype(int).tolist()
    ]

    session = _get_session()
    try:
        electrical_model = session.get(ElectricalModel, int(electrical_model_id))
        if not electrical_model:
            raise ValueError(
                f"No existe el modelo eléctrico con ID {electrical_model_id}."
            )

        existing_links = (
            session.query(ProjectElectricalModel)
            .filter(
                ProjectElectricalModel.ElectricalModelID == int(electrical_model_id),
                ProjectElectricalModel.ProjectID.in_(project_ids),
            )
            .all()
        )

        links_by_project_id = {
            int(link.ProjectID): link
            for link in existing_links
        }

        created_count = 0
        updated_count = 0
        already_modeled_count = 0

        for project_id in project_ids:
            existing = links_by_project_id.get(project_id)

            if existing:
                if bool(existing.IsModeled):
                    already_modeled_count += 1
                else:
                    existing.IsModeled = True
                    updated_count += 1
            else:
                session.add(
                    ProjectElectricalModel(
                        ProjectID=project_id,
                        ElectricalModelID=int(electrical_model_id),
                        IsModeled=True,
                    )
                )
                created_count += 1

        session.commit()

        return {
            "matched": len(project_ids),
            "updated": updated_count,
            "created": created_count,
            "changed": updated_count + created_count,
            "already_modeled": already_modeled_count,
        }

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
