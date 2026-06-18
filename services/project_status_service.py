"""Shared project status synchronization rules for GridAssets.

This module centralizes project status rules triggered by any process that
creates or updates relevant dates.

Domain rules, evaluated against today's date by default:
- Cancelled is an explicit action and is never overwritten by derived dates.
- COD_Actual dated today or in the past promotes the project to InService.
- Start_Construction dated today or in the past promotes the project to UnderConstruction
  when there is no valid COD_Actual and the project is not already InService.
- Start_Construction dated in the future promotes the project to Planned when
  there is no valid COD_Actual and no past construction start.
- COD_Actual dated in the future is treated as a warning and does not promote
  the project to InService.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import text


IN_SERVICE_STATUS_NAME = "InService"
UNDER_CONSTRUCTION_STATUS_NAME = "UnderConstruction"
PLANNED_STATUS_NAME = "Planned"
CANCELLED_STATUS_NAME = "Cancelled"
COD_ACTUAL_MILESTONE_NAME = "COD_Actual"
START_CONSTRUCTION_MILESTONE_NAMES = ("Start_Construction", "Start_Construction_Actual")


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
    start_construction_exists: bool = False
    previous_status_name: Optional[str] = None
    new_status_name: Optional[str] = None
    cod_actual_date: Optional[date] = None
    start_construction_date: Optional[date] = None
    cod_actual_is_future: bool = False
    start_construction_is_future: bool = False


class ProjectStatusService:
    """Shared project status synchronization utilities."""

    @staticmethod
    def sync_project_status_from_dates(
        conn: Any,
        project_id: int,
        as_of_date: Optional[date] = None,
    ) -> ProjectStatusSyncResult:
        """Synchronize project status using relevant dates.

        Parameters
        ----------
        conn:
            Active SQLAlchemy connection, transaction connection or session.
        project_id:
            Existing Project.ProjectID to synchronize.
        as_of_date:
            Date used to decide whether a relevant date has already occurred.
            Defaults to ``date.today()``.
        """
        as_of_date = as_of_date or date.today()

        if project_id is None:
            return ProjectStatusSyncResult(
                project_id=-1,
                cod_actual_exists=False,
                start_construction_exists=False,
                status_changed=False,
                previous_status_id=None,
                previous_status_name=None,
                new_status_id=None,
                new_status_name=None,
                message="Missing project_id.",
            )

        current_status_id = ProjectStatusService._get_project_status_id(conn, project_id)
        current_status_name = ProjectStatusService._get_status_name_by_id(conn, current_status_id)

        cod_actual_any = ProjectStatusService._project_first_date(conn, project_id, [COD_ACTUAL_MILESTONE_NAME])
        cod_actual_due = ProjectStatusService._project_first_date(
            conn,
            project_id,
            [COD_ACTUAL_MILESTONE_NAME],
            operator="<=",
            as_of_date=as_of_date,
        )
        cod_actual_future = ProjectStatusService._project_first_date(
            conn,
            project_id,
            [COD_ACTUAL_MILESTONE_NAME],
            operator=">",
            as_of_date=as_of_date,
        )

        start_any = ProjectStatusService._project_first_date(
            conn,
            project_id,
            list(START_CONSTRUCTION_MILESTONE_NAMES),
        )
        start_due = ProjectStatusService._project_first_date(
            conn,
            project_id,
            list(START_CONSTRUCTION_MILESTONE_NAMES),
            operator="<=",
            as_of_date=as_of_date,
        )
        start_future = ProjectStatusService._project_first_date(
            conn,
            project_id,
            list(START_CONSTRUCTION_MILESTONE_NAMES),
            operator=">",
            as_of_date=as_of_date,
        )

        cod_actual_exists = cod_actual_any is not None
        start_exists = start_any is not None

        if current_status_name == CANCELLED_STATUS_NAME:
            return ProjectStatusSyncResult(
                project_id=project_id,
                cod_actual_exists=cod_actual_exists,
                start_construction_exists=start_exists,
                status_changed=False,
                previous_status_id=current_status_id,
                previous_status_name=current_status_name,
                new_status_id=current_status_id,
                new_status_name=current_status_name,
                cod_actual_date=cod_actual_any,
                start_construction_date=start_any,
                cod_actual_is_future=cod_actual_due is None and cod_actual_future is not None,
                start_construction_is_future=start_due is None and start_future is not None,
                message="Project is Cancelled. Derived status synchronization was not applied.",
            )

        if cod_actual_due is not None:
            return ProjectStatusService._set_status_if_needed(
                conn=conn,
                project_id=project_id,
                status_name=IN_SERVICE_STATUS_NAME,
                cod_actual_exists=True,
                start_construction_exists=start_exists,
                current_status_id=current_status_id,
                current_status_name=current_status_name,
                cod_actual_date=cod_actual_due,
                start_construction_date=start_any,
                cod_actual_is_future=False,
                start_construction_is_future=start_due is None and start_future is not None,
                changed_message="Project status updated to InService because COD_Actual is today or in the past.",
                unchanged_message="Project was already InService.",
            )

        # A future COD_Actual is suspicious; do not promote to InService.
        # The status may still be inferred from construction dates below.
        cod_future_only = cod_actual_due is None and cod_actual_future is not None

        if current_status_name == IN_SERVICE_STATUS_NAME:
            return ProjectStatusSyncResult(
                project_id=project_id,
                cod_actual_exists=cod_actual_exists,
                start_construction_exists=start_exists,
                status_changed=False,
                previous_status_id=current_status_id,
                previous_status_name=current_status_name,
                new_status_id=current_status_id,
                new_status_name=current_status_name,
                cod_actual_date=cod_actual_future or cod_actual_any,
                start_construction_date=start_any,
                cod_actual_is_future=cod_future_only,
                start_construction_is_future=start_due is None and start_future is not None,
                message="Project is already InService. Status was not downgraded.",
            )

        if start_due is not None:
            return ProjectStatusService._set_status_if_needed(
                conn=conn,
                project_id=project_id,
                status_name=UNDER_CONSTRUCTION_STATUS_NAME,
                cod_actual_exists=cod_actual_exists,
                start_construction_exists=True,
                current_status_id=current_status_id,
                current_status_name=current_status_name,
                cod_actual_date=cod_actual_future or cod_actual_any,
                start_construction_date=start_due,
                cod_actual_is_future=cod_future_only,
                start_construction_is_future=False,
                changed_message="Project status updated to UnderConstruction because Start_Construction is today or in the past.",
                unchanged_message="Project was already UnderConstruction.",
            )

        if start_future is not None:
            return ProjectStatusService._set_status_if_needed(
                conn=conn,
                project_id=project_id,
                status_name=PLANNED_STATUS_NAME,
                cod_actual_exists=cod_actual_exists,
                start_construction_exists=True,
                current_status_id=current_status_id,
                current_status_name=current_status_name,
                cod_actual_date=cod_actual_future or cod_actual_any,
                start_construction_date=start_future,
                cod_actual_is_future=cod_future_only,
                start_construction_is_future=True,
                changed_message="Project status updated to Planned because Start_Construction is in the future.",
                unchanged_message="Project was already Planned.",
            )

        message = "Project has no COD_Actual or Start_Construction date. Status was not changed."
        if cod_future_only:
            message = "Project has future COD_Actual only. InService was not applied."

        return ProjectStatusSyncResult(
            project_id=project_id,
            cod_actual_exists=cod_actual_exists,
            start_construction_exists=start_exists,
            status_changed=False,
            previous_status_id=current_status_id,
            previous_status_name=current_status_name,
            new_status_id=current_status_id,
            new_status_name=current_status_name,
            cod_actual_date=cod_actual_future or cod_actual_any,
            start_construction_date=start_any,
            cod_actual_is_future=cod_future_only,
            start_construction_is_future=False,
            message=message,
        )

    @staticmethod
    def set_in_service_when_cod_actual_exists(
        conn: Any,
        project_id: int,
        as_of_date: Optional[date] = None,
    ) -> ProjectStatusSyncResult:
        """Backward-compatible alias for date-derived status synchronization."""
        return ProjectStatusService.sync_project_status_from_dates(conn, project_id, as_of_date=as_of_date)

    @staticmethod
    def set_cancelled_if_no_cod_actual(conn: Any, project_id: int) -> ProjectStatusSyncResult:
        """Set project status to Cancelled unless COD_Actual already exists.

        This rule is used for explicit withdrawn / cancelled actions. It never
        cancels a project that already has COD_Actual, because that case must be
        reviewed manually.
        """
        if project_id is None:
            return ProjectStatusSyncResult(
                project_id=-1,
                cod_actual_exists=False,
                start_construction_exists=False,
                status_changed=False,
                previous_status_id=None,
                previous_status_name=None,
                new_status_id=None,
                new_status_name=None,
                message="Missing project_id.",
            )

        current_status_id = ProjectStatusService._get_project_status_id(conn, project_id)
        current_status_name = ProjectStatusService._get_status_name_by_id(conn, current_status_id)
        cod_actual_any = ProjectStatusService._project_first_date(conn, project_id, [COD_ACTUAL_MILESTONE_NAME])
        start_any = ProjectStatusService._project_first_date(conn, project_id, list(START_CONSTRUCTION_MILESTONE_NAMES))

        if cod_actual_any is not None:
            return ProjectStatusSyncResult(
                project_id=project_id,
                cod_actual_exists=True,
                start_construction_exists=start_any is not None,
                status_changed=False,
                previous_status_id=current_status_id,
                previous_status_name=current_status_name,
                new_status_id=current_status_id,
                new_status_name=current_status_name,
                cod_actual_date=cod_actual_any,
                start_construction_date=start_any,
                message="Project has COD_Actual. Cancelled status was not applied.",
                blocked_by_cod_actual=True,
            )

        return ProjectStatusService._set_status_if_needed(
            conn=conn,
            project_id=project_id,
            status_name=CANCELLED_STATUS_NAME,
            cod_actual_exists=False,
            start_construction_exists=start_any is not None,
            current_status_id=current_status_id,
            current_status_name=current_status_name,
            cod_actual_date=None,
            start_construction_date=start_any,
            cod_actual_is_future=False,
            start_construction_is_future=False,
            changed_message="Project status updated to Cancelled.",
            unchanged_message="Project was already Cancelled.",
        )

    @staticmethod
    def _set_status_if_needed(
        conn: Any,
        project_id: int,
        status_name: str,
        cod_actual_exists: bool,
        start_construction_exists: bool,
        current_status_id: Optional[int],
        current_status_name: Optional[str],
        cod_actual_date: Optional[date],
        start_construction_date: Optional[date],
        cod_actual_is_future: bool,
        start_construction_is_future: bool,
        changed_message: str,
        unchanged_message: str,
    ) -> ProjectStatusSyncResult:
        target_status_id = ProjectStatusService._ensure_status(conn, status_name)
        if current_status_id == target_status_id:
            return ProjectStatusSyncResult(
                project_id=project_id,
                cod_actual_exists=cod_actual_exists,
                start_construction_exists=start_construction_exists,
                status_changed=False,
                previous_status_id=current_status_id,
                previous_status_name=current_status_name,
                new_status_id=current_status_id,
                new_status_name=status_name,
                cod_actual_date=cod_actual_date,
                start_construction_date=start_construction_date,
                cod_actual_is_future=cod_actual_is_future,
                start_construction_is_future=start_construction_is_future,
                message=unchanged_message,
            )

        conn.execute(
            text(
                """
                UPDATE Project
                SET StatusID = :status_id
                WHERE ProjectID = :project_id
                """
            ),
            {"status_id": target_status_id, "project_id": project_id},
        )
        return ProjectStatusSyncResult(
            project_id=project_id,
            cod_actual_exists=cod_actual_exists,
            start_construction_exists=start_construction_exists,
            status_changed=True,
            previous_status_id=current_status_id,
            previous_status_name=current_status_name,
            new_status_id=target_status_id,
            new_status_name=status_name,
            cod_actual_date=cod_actual_date,
            start_construction_date=start_construction_date,
            cod_actual_is_future=cod_actual_is_future,
            start_construction_is_future=start_construction_is_future,
            message=changed_message,
        )

    @staticmethod
    def _project_first_date(
        conn: Any,
        project_id: int,
        milestone_names: list[str],
        operator: Optional[str] = None,
        as_of_date: Optional[date] = None,
    ) -> Optional[date]:
        if not milestone_names:
            return None
        if operator not in {None, "<=", ">"}:
            raise ValueError("operator must be None, '<=' or '>'")

        placeholders = ", ".join(f":milestone_{idx}" for idx, _ in enumerate(milestone_names))
        params: dict[str, Any] = {"project_id": project_id}
        for idx, name in enumerate(milestone_names):
            params[f"milestone_{idx}"] = name

        date_filter = ""
        if operator is not None:
            if as_of_date is None:
                as_of_date = date.today()
            params["as_of_date"] = as_of_date
            date_filter = f"AND CAST(rd.DateValue AS date) {operator} :as_of_date"

        value = conn.execute(
            text(
                f"""
                SELECT TOP 1 rd.DateValue
                FROM RelevantDate rd
                INNER JOIN MilestoneType mt
                    ON rd.MilestoneTypeID = mt.MilestoneTypeID
                WHERE rd.ProjectID = :project_id
                  AND mt.MilestoneName IN ({placeholders})
                  AND rd.DateValue IS NOT NULL
                  {date_filter}
                ORDER BY rd.DateValue ASC
                """
            ),
            params,
        ).scalar()
        return ProjectStatusService._to_date(value)

    @staticmethod
    def _project_has_cod_actual(conn: Any, project_id: int) -> bool:
        return ProjectStatusService._project_first_date(conn, project_id, [COD_ACTUAL_MILESTONE_NAME]) is not None

    @staticmethod
    def _project_has_start_construction(conn: Any, project_id: int) -> bool:
        return ProjectStatusService._project_first_date(
            conn,
            project_id,
            list(START_CONSTRUCTION_MILESTONE_NAMES),
        ) is not None

    @staticmethod
    def _to_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return datetime.fromisoformat(str(value)).date()
        except ValueError:
            return None

    @staticmethod
    def _get_project_status_id(conn: Any, project_id: int) -> Optional[int]:
        status_id = conn.execute(
            text("SELECT StatusID FROM Project WHERE ProjectID = :project_id"),
            {"project_id": project_id},
        ).scalar()
        return int(status_id) if status_id is not None else None

    @staticmethod
    def _get_status_name_by_id(conn: Any, status_id: Optional[int]) -> Optional[str]:
        if status_id is None:
            return None
        status_name = conn.execute(
            text("SELECT StatusName FROM ProjectStatus WHERE StatusID = :status_id"),
            {"status_id": status_id},
        ).scalar()
        return str(status_name) if status_name is not None else None

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
