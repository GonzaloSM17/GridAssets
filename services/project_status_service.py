"""Shared project status synchronization rules for GridAssets.

This module centralizes project status rules that can be triggered by any
process that creates or updates relevant dates.

Current domain rule:
- If a project has a valid COD_Actual date, its status must be InService.

The service only promotes projects to InService. It does not infer or revert
projects to earlier statuses such as UnderConstruction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text


IN_SERVICE_STATUS_NAME = "InService"
CANCELLED_STATUS_NAME = "Cancelled"
COD_ACTUAL_MILESTONE_NAME = "COD_Actual"


@dataclass(frozen=True)
class ProjectStatusSyncResult:
    """Result returned by a project status synchronization operation."""

    project_id: int
    cod_actual_exists: bool
    status_changed: bool
    previous_status_id: Optional[int]
    new_status_id: Optional[int]
    message: str
    blocked_by_cod_actual: bool = False


class ProjectStatusService:
    """Shared project status synchronization utilities."""

    @staticmethod
    def sync_project_status_from_dates(conn: Any, project_id: int) -> ProjectStatusSyncResult:
        """Synchronize project status using relevant dates.

        Current rule:
        - If the project has COD_Actual, set Project.StatusID to InService.
        - If COD_Actual does not exist, do not change status.
        - If the project is already InService, do not change status.

        Parameters
        ----------
        conn:
            Active SQLAlchemy connection or transaction connection.
        project_id:
            Existing Project.ProjectID to synchronize.
        """
        if project_id is None:
            return ProjectStatusSyncResult(
                project_id=-1,
                cod_actual_exists=False,
                status_changed=False,
                previous_status_id=None,
                new_status_id=None,
                message="Missing project_id.",
            )

        cod_actual_exists = ProjectStatusService._project_has_cod_actual(conn, project_id)
        current_status_id = ProjectStatusService._get_project_status_id(conn, project_id)

        if not cod_actual_exists:
            return ProjectStatusSyncResult(
                project_id=project_id,
                cod_actual_exists=False,
                status_changed=False,
                previous_status_id=current_status_id,
                new_status_id=current_status_id,
                message="Project has no COD_Actual date. Status was not changed.",
            )

        in_service_status_id = ProjectStatusService._ensure_status(conn, IN_SERVICE_STATUS_NAME)

        if current_status_id == in_service_status_id:
            return ProjectStatusSyncResult(
                project_id=project_id,
                cod_actual_exists=True,
                status_changed=False,
                previous_status_id=current_status_id,
                new_status_id=current_status_id,
                message="Project was already InService.",
            )

        conn.execute(
            text(
                """
                UPDATE Project
                SET StatusID = :status_id
                WHERE ProjectID = :project_id
                """
            ),
            {"status_id": in_service_status_id, "project_id": project_id},
        )

        return ProjectStatusSyncResult(
            project_id=project_id,
            cod_actual_exists=True,
            status_changed=True,
            previous_status_id=current_status_id,
            new_status_id=in_service_status_id,
            message="Project status updated to InService because COD_Actual exists.",
        )

    @staticmethod
    def set_in_service_when_cod_actual_exists(conn: Any, project_id: int) -> ProjectStatusSyncResult:
        """Backward-compatible alias for the current COD_Actual rule."""
        return ProjectStatusService.sync_project_status_from_dates(conn, project_id)


    @staticmethod
    def set_cancelled_if_no_cod_actual(conn: Any, project_id: int) -> ProjectStatusSyncResult:
        """Set project status to Cancelled unless COD_Actual already exists.

        This rule is used for CEN connection rows corresponding to withdrawn /
        cancelled projects. It never cancels a project that already has COD_Actual,
        because that case must be reviewed manually.
        """
        if project_id is None:
            return ProjectStatusSyncResult(
                project_id=-1,
                cod_actual_exists=False,
                status_changed=False,
                previous_status_id=None,
                new_status_id=None,
                message="Missing project_id.",
            )

        current_status_id = ProjectStatusService._get_project_status_id(conn, project_id)
        cod_actual_exists = ProjectStatusService._project_has_cod_actual(conn, project_id)
        if cod_actual_exists:
            return ProjectStatusSyncResult(
                project_id=project_id,
                cod_actual_exists=True,
                status_changed=False,
                previous_status_id=current_status_id,
                new_status_id=current_status_id,
                message="Project has COD_Actual. Cancelled status was not applied.",
                blocked_by_cod_actual=True,
            )

        cancelled_status_id = ProjectStatusService._ensure_status(conn, CANCELLED_STATUS_NAME)
        if current_status_id == cancelled_status_id:
            return ProjectStatusSyncResult(
                project_id=project_id,
                cod_actual_exists=False,
                status_changed=False,
                previous_status_id=current_status_id,
                new_status_id=current_status_id,
                message="Project was already Cancelled.",
            )

        conn.execute(
            text(
                """
                UPDATE Project
                SET StatusID = :status_id
                WHERE ProjectID = :project_id
                """
            ),
            {"status_id": cancelled_status_id, "project_id": project_id},
        )

        return ProjectStatusSyncResult(
            project_id=project_id,
            cod_actual_exists=False,
            status_changed=True,
            previous_status_id=current_status_id,
            new_status_id=cancelled_status_id,
            message="Project status updated to Cancelled.",
        )

    @staticmethod
    def _project_has_cod_actual(conn: Any, project_id: int) -> bool:
        value = conn.execute(
            text(
                """
                SELECT TOP 1 1
                FROM RelevantDate rd
                INNER JOIN MilestoneType mt
                    ON rd.MilestoneTypeID = mt.MilestoneTypeID
                WHERE rd.ProjectID = :project_id
                  AND mt.MilestoneName = :milestone_name
                  AND rd.DateValue IS NOT NULL
                """
            ),
            {"project_id": project_id, "milestone_name": COD_ACTUAL_MILESTONE_NAME},
        ).scalar()
        return value is not None

    @staticmethod
    def _get_project_status_id(conn: Any, project_id: int) -> Optional[int]:
        status_id = conn.execute(
            text("SELECT StatusID FROM Project WHERE ProjectID = :project_id"),
            {"project_id": project_id},
        ).scalar()
        return int(status_id) if status_id is not None else None

    @staticmethod
    def _ensure_status(conn: Any, status_name: str) -> int:
        status_id = conn.execute(
            text("SELECT StatusID FROM ProjectStatus WHERE StatusName = :status_name"),
            {"status_name": status_name},
        ).scalar()
        if status_id is not None:
            return int(status_id)

        conn.execute(
            text("INSERT INTO ProjectStatus (StatusName) VALUES (:status_name)"),
            {"status_name": status_name},
        )
        status_id = conn.execute(
            text("SELECT StatusID FROM ProjectStatus WHERE StatusName = :status_name"),
            {"status_name": status_name},
        ).scalar()
        return int(status_id)
