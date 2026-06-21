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


VALID_INCLUSION_MODES = {"operation", "operation_projected"}
DEFAULT_APPLY_CAPACITY_FILTER = True
DEFAULT_MIN_MODELING_CAPACITY_MW = 20.0


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



def activate_model(electrical_model_id: int) -> None:
    """Activate an existing electrical model."""

    session = _get_session()
    try:
        model = session.get(ElectricalModel, int(electrical_model_id))

        if not model:
            raise ValueError(
                f"No existe el modelo eléctrico con ID {electrical_model_id}."
            )

        model.IsActive = True
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_model_usage(electrical_model_id: int) -> dict[str, int]:
    """Return how many project-model records are linked to an electrical model."""

    session = _get_session()
    try:
        model = session.get(ElectricalModel, int(electrical_model_id))

        if not model:
            raise ValueError(
                f"No existe el modelo eléctrico con ID {electrical_model_id}."
            )

        project_links_count = (
            session.query(ProjectElectricalModel)
            .filter(
                ProjectElectricalModel.ElectricalModelID == int(electrical_model_id)
            )
            .count()
        )

        modeled_links_count = (
            session.query(ProjectElectricalModel)
            .filter(
                ProjectElectricalModel.ElectricalModelID == int(electrical_model_id),
                ProjectElectricalModel.IsModeled == 1,
            )
            .count()
        )

        return {
            "project_links": int(project_links_count),
            "modeled_links": int(modeled_links_count),
        }
    finally:
        session.close()


def delete_model(electrical_model_id: int) -> dict[str, Any]:
    """Permanently delete an electrical model and its project-model links."""

    session = _get_session()
    try:
        model = session.get(ElectricalModel, int(electrical_model_id))

        if not model:
            raise ValueError(
                f"No existe el modelo eléctrico con ID {electrical_model_id}."
            )

        deleted_links = (
            session.query(ProjectElectricalModel)
            .filter(
                ProjectElectricalModel.ElectricalModelID == int(electrical_model_id)
            )
            .delete(synchronize_session=False)
        )

        session.delete(model)
        session.commit()

        return {
            "deleted_model": True,
            "deleted_links": int(deleted_links),
        }

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
    inclusion_mode: str = "operation",
    apply_capacity_filter: bool = DEFAULT_APPLY_CAPACITY_FILTER,
    min_capacity_mw: float = DEFAULT_MIN_MODELING_CAPACITY_MW,
) -> pd.DataFrame:
    """Preview projects that match a bulk modeling date criterion.

    inclusion_mode values:
    - "operation": uses only COD_Actual.
    - "operation_projected": uses COD_Actual, then COD_Estimated,
      then Commissioning_Actual, then Commissioning_Estimated.

    Capacity criterion:
    - Transmission projects do not use a capacity threshold.
    - Generation and DER use TotalCapacity.
    - BESS uses PowerCapacity because the current ORM table has no TotalCapacity.
    - When enabled, generation/BESS/DER must be >= min_capacity_mw.
    """

    if inclusion_mode not in VALID_INCLUSION_MODES:
        raise ValueError(
            "inclusion_mode must be 'operation' or 'operation_projected'."
        )

    cutoff_datetime = _normalize_cutoff_datetime(cod_cutoff_date)

    selected_project_type = None
    if project_type and project_type != "all":
        selected_project_type = project_type

    only_unmodeled_int = 1 if only_unmodeled else 0
    apply_capacity_filter_int = 1 if apply_capacity_filter else 0
    min_capacity_mw = float(min_capacity_mw)

    query = text(
        """
        WITH RankedRelevantDates AS (
            SELECT
                p.ProjectID,
                p.ProjectName,
                p.NUP,
                p.project_discriminator,
                pe.ProjectEntityName,
                CASE
                    WHEN p.project_discriminator = 'generation'
                        THEN gp.TotalCapacity
                    WHEN p.project_discriminator = 'der'
                        THEN dp.TotalCapacity
                    WHEN p.project_discriminator = 'bess'
                        THEN bp.PowerCapacity
                    ELSE NULL
                END AS TotalCapacity,
                mt.MilestoneName,
                rd.DateValue,
                rd.ExtractedAt,
                ROW_NUMBER() OVER (
                    PARTITION BY p.ProjectID, mt.MilestoneName
                    ORDER BY rd.ExtractedAt DESC, rd.DateValue DESC
                ) AS RowNumber
            FROM Project p
            LEFT JOIN ProjectStatus ps
                ON p.StatusID = ps.StatusID
            INNER JOIN RelevantDate rd
                ON p.ProjectID = rd.ProjectID
            INNER JOIN MilestoneType mt
                ON rd.MilestoneTypeID = mt.MilestoneTypeID
            LEFT JOIN ProjectEntity pe
                ON p.ProjectEntityID = pe.ProjectEntityID
            LEFT JOIN GenerationProject gp
                ON p.ProjectID = gp.ProjectID
            LEFT JOIN DERProject dp
                ON p.ProjectID = dp.ProjectID
            LEFT JOIN BESSProject bp
                ON p.ProjectID = bp.ProjectID
            WHERE ISNULL(ps.StatusName, '') <> 'Cancelled'
                AND mt.MilestoneName IN (
                    'COD_Actual',
                    'COD_Estimated',
                    'Commissioning_Actual',
                    'Commissioning_Estimated'
                )
        ),
        ProjectDates AS (
            SELECT
                ProjectID,
                MAX(ProjectName) AS ProjectName,
                MAX(NUP) AS NUP,
                MAX(project_discriminator) AS project_discriminator,
                MAX(ProjectEntityName) AS ProjectEntityName,
                MAX(TotalCapacity) AS TotalCapacity,
                MAX(CASE
                    WHEN MilestoneName = 'COD_Actual' AND RowNumber = 1
                    THEN DateValue
                END) AS COD_Actual,
                MAX(CASE
                    WHEN MilestoneName = 'COD_Estimated' AND RowNumber = 1
                    THEN DateValue
                END) AS COD_Estimated,
                MAX(CASE
                    WHEN MilestoneName = 'Commissioning_Actual' AND RowNumber = 1
                    THEN DateValue
                END) AS Commissioning_Actual,
                MAX(CASE
                    WHEN MilestoneName = 'Commissioning_Estimated' AND RowNumber = 1
                    THEN DateValue
                END) AS Commissioning_Estimated
            FROM RankedRelevantDates
            GROUP BY ProjectID
        ),
        DateSelection AS (
            SELECT
                ProjectID,
                ProjectName,
                NUP,
                ProjectEntityName,
                project_discriminator,
                TotalCapacity,
                COD_Actual,
                COD_Estimated,
                Commissioning_Actual,
                Commissioning_Estimated,
                CASE
                    WHEN :inclusion_mode = 'operation' THEN COD_Actual
                    ELSE COALESCE(
                        COD_Actual,
                        COD_Estimated,
                        Commissioning_Actual,
                        Commissioning_Estimated
                    )
                END AS ReferenceDate,
                CASE
                    WHEN :inclusion_mode = 'operation'
                        AND COD_Actual IS NOT NULL
                        THEN 'COD_Actual'
                    WHEN :inclusion_mode = 'operation_projected'
                        AND COD_Actual IS NOT NULL
                        THEN 'COD_Actual'
                    WHEN :inclusion_mode = 'operation_projected'
                        AND COD_Estimated IS NOT NULL
                        THEN 'COD_Estimated'
                    WHEN :inclusion_mode = 'operation_projected'
                        AND Commissioning_Actual IS NOT NULL
                        THEN 'Commissioning_Actual'
                    WHEN :inclusion_mode = 'operation_projected'
                        AND Commissioning_Estimated IS NOT NULL
                        THEN 'Commissioning_Estimated'
                    ELSE NULL
                END AS ReferenceDateSource
            FROM ProjectDates
        )
        SELECT
            ds.ProjectID,
            ds.ProjectName,
            ds.NUP,
            ds.ProjectEntityName,
            ds.project_discriminator,
            ds.TotalCapacity,
            CAST(
                CASE
                    WHEN :apply_capacity_filter = 0 THEN 1
                    WHEN ds.project_discriminator NOT IN ('generation', 'bess', 'der')
                        THEN 1
                    WHEN ds.TotalCapacity >= :min_capacity_mw THEN 1
                    ELSE 0
                END AS int
            ) AS IsCapacityEligible,
            ds.COD_Actual,
            ds.COD_Estimated,
            ds.Commissioning_Actual,
            ds.Commissioning_Estimated,
            ds.ReferenceDate,
            ds.ReferenceDateSource,
            CAST(ISNULL(pem.IsModeled, 0) AS int) AS IsCurrentlyModeled
        FROM DateSelection ds
        LEFT JOIN ProjectElectricalModel pem
            ON ds.ProjectID = pem.ProjectID
            AND pem.ElectricalModelID = :electrical_model_id
        WHERE ds.ReferenceDate IS NOT NULL
            AND ds.ReferenceDate <= :cutoff_datetime
            AND (
                :project_type IS NULL
                OR ds.project_discriminator = :project_type
            )
            AND (
                :apply_capacity_filter = 0
                OR ds.project_discriminator NOT IN ('generation', 'bess', 'der')
                OR ds.TotalCapacity >= :min_capacity_mw
            )
            AND (
                :only_unmodeled = 0
                OR ISNULL(pem.IsModeled, 0) = 0
            )
        ORDER BY ds.ReferenceDate DESC, ds.ProjectID;
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
                "inclusion_mode": inclusion_mode,
                "apply_capacity_filter": apply_capacity_filter_int,
                "min_capacity_mw": min_capacity_mw,
            },
        )

    if df.empty:
        return df

    df["IsCurrentlyModeled"] = df["IsCurrentlyModeled"].astype(bool)

    if "IsCapacityEligible" in df.columns:
        df["IsCapacityEligible"] = df["IsCapacityEligible"].astype(bool)

    if "TotalCapacity" in df.columns:
        df["TotalCapacity"] = pd.to_numeric(df["TotalCapacity"], errors="coerce")

    date_columns = [
        "COD_Actual",
        "COD_Estimated",
        "Commissioning_Actual",
        "Commissioning_Estimated",
        "ReferenceDate",
    ]

    for column in date_columns:
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], errors="coerce")

    return df


def bulk_set_modeled_by_cod_date(
    electrical_model_id: int,
    cod_cutoff_date: date | datetime,
    project_type: str | None = None,
    only_unmodeled: bool = True,
    inclusion_mode: str = "operation",
    apply_capacity_filter: bool = DEFAULT_APPLY_CAPACITY_FILTER,
    min_capacity_mw: float = DEFAULT_MIN_MODELING_CAPACITY_MW,
) -> dict[str, Any]:
    """Mark projects as modeled using the selected bulk modeling criterion."""

    preview_df = preview_projects_for_bulk_modeling_by_cod(
        electrical_model_id=electrical_model_id,
        cod_cutoff_date=cod_cutoff_date,
        project_type=project_type,
        only_unmodeled=only_unmodeled,
        inclusion_mode=inclusion_mode,
        apply_capacity_filter=apply_capacity_filter,
        min_capacity_mw=min_capacity_mw,
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
