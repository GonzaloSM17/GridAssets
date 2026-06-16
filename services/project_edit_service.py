"""Project edit service.

Direct database write operations for individual project fields. These functions are
called from the Streamlit UI layer and are intentionally stateless.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy.orm import sessionmaker

from database.db_connection import get_sqlserver_engine
from database.db_orm_model import MilestoneType, Project, ProjectStatus, RelevantDate, Source


DEFAULT_STATUS_OPTIONS = [
    "InService",
    "NonStarted",
    "UnderConstruction",
    "OnHold",
    "Cancelled",
]


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def update_project_nup(project_id: int, nup_value: Optional[int]) -> None:
    """Update the NUP field for a single project."""
    engine = get_sqlserver_engine()
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        project = session.get(Project, project_id)
        if not project:
            raise ValueError(f"No se encontró el proyecto con ID {project_id}.")

        project.NUP = nup_value
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_project_status_options() -> List[str]:
    """Return existing status names plus default options."""
    engine = get_sqlserver_engine()
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        rows = session.query(ProjectStatus).order_by(ProjectStatus.StatusName).all()
        existing = [row.StatusName for row in rows]
        merged = list(dict.fromkeys([*existing, *DEFAULT_STATUS_OPTIONS]))
        return sorted(merged)
    finally:
        session.close()


def update_project_status(project_id: int, status_name: str | None) -> None:
    """Update the project status. Creates the lookup value if needed."""
    engine = get_sqlserver_engine()
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        project = session.get(Project, project_id)
        if not project:
            raise ValueError(f"No se encontró el proyecto con ID {project_id}.")

        clean_status = _clean_text(status_name)
        if clean_status is None:
            project.StatusID = None
        else:
            status = (
                session.query(ProjectStatus)
                .filter(ProjectStatus.StatusName == clean_status)
                .one_or_none()
            )
            if status is None:
                status = ProjectStatus(StatusName=clean_status)
                session.add(status)
                session.flush()

            project.StatusID = status.StatusID

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_milestone_types() -> List[str]:
    """Return all MilestoneType names available in the database."""
    engine = get_sqlserver_engine()
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        rows = session.query(MilestoneType).order_by(MilestoneType.MilestoneName).all()
        return [row.MilestoneName for row in rows]
    finally:
        session.close()


def update_project_date(
    project_id: int,
    milestone_name: str,
    date_value: Optional[date],
) -> None:
    """Insert, update, or delete a RelevantDate with source='User'."""
    engine = get_sqlserver_engine()
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        source = session.query(Source).filter(Source.SourceName == "User").one_or_none()
        if not source:
            source = Source(SourceName="User")
            session.add(source)
            session.flush()

        milestone = (
            session.query(MilestoneType)
            .filter(MilestoneType.MilestoneName == milestone_name)
            .one_or_none()
        )
        if not milestone:
            raise ValueError(f"MilestoneType '{milestone_name}' no existe en la base de datos.")

        existing = (
            session.query(RelevantDate)
            .filter(
                RelevantDate.ProjectID == project_id,
                RelevantDate.MilestoneTypeID == milestone.MilestoneTypeID,
                RelevantDate.SourceID == source.SourceID,
            )
            .one_or_none()
        )

        if date_value is None:
            if existing:
                session.delete(existing)
        else:
            dt_value = datetime.combine(date_value, datetime.min.time())
            if existing:
                existing.DateValue = dt_value
                existing.ExtractedAt = datetime.now()
            else:
                session.add(
                    RelevantDate(
                        ProjectID=project_id,
                        MilestoneTypeID=milestone.MilestoneTypeID,
                        SourceID=source.SourceID,
                        DateValue=dt_value,
                        ExtractedAt=datetime.now(),
                    )
                )

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
