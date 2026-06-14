"""Project edit service — direct DB write operations for individual project fields.

These functions are called from the UI layer (Streamlit) and should be
kept stateless: open session → write → commit/rollback → close.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy.orm import sessionmaker

from database.db_connection import get_sqlserver_engine
from database.db_orm_model import MilestoneType, Project, RelevantDate, Source


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
    """Insert, update, or delete a RelevantDate with source='User'.

    Pass date_value=None to delete the existing User record for that milestone.
    """
    engine = get_sqlserver_engine()
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Ensure the 'User' source exists
        source = session.query(Source).filter(Source.SourceName == "User").one_or_none()
        if not source:
            source = Source(SourceName="User")
            session.add(source)
            session.flush()

        # Resolve milestone type
        milestone = (
            session.query(MilestoneType)
            .filter(MilestoneType.MilestoneName == milestone_name)
            .one_or_none()
        )
        if not milestone:
            raise ValueError(
                f"MilestoneType '{milestone_name}' no existe en la base de datos."
            )

        # Find existing User record for this project + milestone
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
