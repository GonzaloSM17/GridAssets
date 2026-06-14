# Database Web Scraper Manager
"""
Orchestrates PGP and SEO web scraping updates against the SQL Server database.
This module can be used from Streamlit or executed from the command line.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional

from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.db_connection import get_connection_string, get_sqlserver_engine
from database.db_orm_model import (
    MilestoneType,
    Project,
    RelevantDate,
    Source,
    TransmissionProject,
)
from scrapers.web_seekers import PGPSeeker, SEOSeeker

ProgressCallback = Optional[Callable[[Dict], None]]


class WebScraperManager:
    """Manages scraping processes for projects stored in the database."""

    REQUIRED_SOURCES = {"PGP", "SEO"}
    REQUIRED_MILESTONES = {
        "Commissioning_Estimated",
        "Commissioning_Actual",
        "COD_Estimated",
        "COD_Actual",
        "Start_Construction",
    }

    PGP_PROJECT_TYPES = ["transmission", "generation", "bess"]
    SEO_PROJECT_TYPES = ["transmission"]

    MAX_PGP_WORKERS = 4
    MAX_SEO_WORKERS = 3

    def __init__(self, engine=None, echo: bool = False):
        self.engine = engine or get_sqlserver_engine()
        self.Session = sessionmaker(bind=self.engine)
        self.echo = echo

    def validate_reference_data(self) -> Dict[str, List[str]]:
        """Validate that required Source and MilestoneType rows exist."""
        session = self.Session()
        try:
            existing_sources = {
                row.SourceName
                for row in session.query(Source)
                .filter(Source.SourceName.in_(self.REQUIRED_SOURCES))
                .all()
            }

            existing_milestones = {
                row.MilestoneName
                for row in session.query(MilestoneType)
                .filter(MilestoneType.MilestoneName.in_(self.REQUIRED_MILESTONES))
                .all()
            }

            return {
                "missing_sources": sorted(self.REQUIRED_SOURCES - existing_sources),
                "missing_milestones": sorted(
                    self.REQUIRED_MILESTONES - existing_milestones
                ),
            }
        finally:
            session.close()

    def assert_reference_data(self) -> None:
        """Raise an explicit error if required reference data is missing."""
        validation = self.validate_reference_data()
        missing_sources = validation["missing_sources"]
        missing_milestones = validation["missing_milestones"]

        if missing_sources or missing_milestones:
            message_parts = []

            if missing_sources:
                message_parts.append(
                    f"missing Source rows: {', '.join(missing_sources)}"
                )

            if missing_milestones:
                message_parts.append(
                    f"missing MilestoneType rows: {', '.join(missing_milestones)}"
                )

            raise RuntimeError("Cannot run scraper because " + "; ".join(message_parts))

    def update_from_pgp(
        self,
        limit: Optional[int] = None,
        update_existing: bool = False,
        progress_callback: ProgressCallback = None,
        sleep_between_requests: bool = True,
        workers: int = 1,
        project_types: list[str] | None = None,
    ) -> Dict:
        """Update selected project types from PGP."""
        self.assert_reference_data()

        project_types = self._normalize_project_types(
            project_types,
            allowed_types=self.PGP_PROJECT_TYPES,
            default_types=self.PGP_PROJECT_TYPES,
        )

        workers = self._normalize_workers(workers, self.MAX_PGP_WORKERS)
        summary = self._empty_summary("PGP", update_existing, workers)
        summary["project_types"] = project_types

        project_ids = self._get_pgp_project_ids(
            limit=limit,
            update_existing=update_existing,
            project_types=project_types,
        )

        summary["total"] = len(project_ids)

        if not project_ids:
            self._emit(
                progress_callback,
                source="PGP",
                status="empty",
                message="No projects to process",
                project_types=project_types,
            )
            return summary

        return self._run_project_batch(
            source="PGP",
            project_ids=project_ids,
            summary=summary,
            processor=self._process_pgp_project,
            progress_callback=progress_callback,
            sleep_between_requests=sleep_between_requests,
            workers=workers,
        )

    def update_from_seo(
        self,
        limit: Optional[int] = None,
        update_existing: bool = False,
        progress_callback: ProgressCallback = None,
        sleep_between_requests: bool = True,
        workers: int = 1,
        project_types: list[str] | None = None,
    ) -> Dict:
        """Update transmission projects from SEO."""
        self.assert_reference_data()

        project_types = self.SEO_PROJECT_TYPES

        workers = self._normalize_workers(workers, self.MAX_SEO_WORKERS)
        summary = self._empty_summary("SEO", update_existing, workers)
        summary["project_types"] = project_types

        project_ids = self._get_seo_project_ids(
            limit=limit,
            update_existing=update_existing,
        )

        summary["total"] = len(project_ids)

        if not project_ids:
            self._emit(
                progress_callback,
                source="SEO",
                status="empty",
                message="No projects to process",
                project_types=project_types,
            )
            return summary

        return self._run_project_batch(
            source="SEO",
            project_ids=project_ids,
            summary=summary,
            processor=self._process_seo_project,
            progress_callback=progress_callback,
            sleep_between_requests=sleep_between_requests,
            workers=workers,
        )

    def update_from_sources(
        self,
        source: str = "all",
        limit: Optional[int] = None,
        update_existing: bool = False,
        progress_callback: ProgressCallback = None,
        sleep_between_requests: bool = True,
        workers: int = 1,
        project_types: list[str] | None = None,
    ) -> Dict:
        """Run PGP, SEO or both sources and return a combined summary."""
        source = source.lower().strip()

        if source not in {"pgp", "seo", "all"}:
            raise ValueError("source must be one of: pgp, seo, all")

        pgp_project_types = self._normalize_project_types(
            project_types,
            allowed_types=self.PGP_PROJECT_TYPES,
            default_types=self.PGP_PROJECT_TYPES,
        )

        combined = {
            "source": source.upper(),
            "total": 0,
            "success": 0,
            "failed": 0,
            "workers_requested": workers,
            "pgp_project_types": pgp_project_types,
            "seo_project_types": self.SEO_PROJECT_TYPES,
            "items": [],
            "runs": [],
        }

        if source in {"pgp", "all"}:
            pgp_summary = self.update_from_pgp(
                limit=limit,
                update_existing=update_existing,
                progress_callback=progress_callback,
                sleep_between_requests=sleep_between_requests,
                workers=workers,
                project_types=pgp_project_types,
            )

            combined["runs"].append(pgp_summary)
            self._merge_summary(combined, pgp_summary)

        if source in {"seo", "all"}:
            seo_summary = self.update_from_seo(
                limit=limit,
                update_existing=update_existing,
                progress_callback=progress_callback,
                sleep_between_requests=sleep_between_requests,
                workers=workers,
                project_types=self.SEO_PROJECT_TYPES,
            )

            combined["runs"].append(seo_summary)
            self._merge_summary(combined, seo_summary)

        return combined

    def _get_pgp_project_ids(
        self,
        limit: Optional[int],
        update_existing: bool,
        project_types: list[str],
    ) -> List[int]:
        session = self.Session()

        try:
            query = session.query(Project.ProjectID).filter(
                Project.project_discriminator.in_(project_types)
            )

            if not update_existing:
                query = query.filter(Project.NUP.is_(None))

            query = query.order_by(Project.ProjectID)

            if limit is not None:
                query = query.limit(limit)

            return [row[0] for row in query.all()]

        finally:
            session.close()

    def _get_seo_project_ids(
        self,
        limit: Optional[int],
        update_existing: bool,
    ) -> List[int]:
        session = self.Session()

        try:
            query = session.query(TransmissionProject.ProjectID).filter(
                TransmissionProject.NUP.isnot(None)
            )

            if not update_existing:
                source_seo = (
                    session.query(Source).filter(Source.SourceName == "SEO").first()
                )

                milestone_start = (
                    session.query(MilestoneType)
                    .filter(MilestoneType.MilestoneName == "Start_Construction")
                    .first()
                )

                if source_seo and milestone_start:
                    scraped_project_ids = (
                        session.query(RelevantDate.ProjectID)
                        .filter(
                            RelevantDate.MilestoneTypeID
                            == milestone_start.MilestoneTypeID,
                            RelevantDate.SourceID == source_seo.SourceID,
                        )
                        .subquery()
                    )

                    query = query.filter(
                        ~TransmissionProject.ProjectID.in_(scraped_project_ids)
                    )

            query = query.order_by(TransmissionProject.ProjectID)

            if limit is not None:
                query = query.limit(limit)

            return [row[0] for row in query.all()]

        finally:
            session.close()

    def _run_project_batch(
        self,
        source: str,
        project_ids: List[int],
        summary: Dict,
        processor: Callable[[int], Dict],
        progress_callback: ProgressCallback,
        sleep_between_requests: bool,
        workers: int,
    ) -> Dict:
        project_briefs = self._get_project_briefs(project_ids)

        if workers <= 1:
            processor_id = 1

            for index, project_id in enumerate(project_ids, start=1):
                project_brief = project_briefs.get(project_id, {})

                self._emit(
                    progress_callback,
                    source=source,
                    processor_id=processor_id,
                    status="running",
                    index=index,
                    total=len(project_ids),
                    project_id=project_id,
                    project_name=project_brief.get("project_name", ""),
                    project_type=project_brief.get("project_type", ""),
                    message=f"Processing ProjectID {project_id}",
                )

                item = processor(project_id)
                item["processor_id"] = processor_id

                self._add_item_to_summary(summary, item)
                self._emit_item(
                    progress_callback,
                    item,
                    index,
                    len(project_ids),
                    processor_id=processor_id,
                )

                if sleep_between_requests and index < len(project_ids):
                    time.sleep(random.uniform(2, 4))

            return summary

        self._emit(
            progress_callback,
            source=source,
            processor_id="all",
            status="running",
            index=0,
            total=len(project_ids),
            message=f"Processing with {workers} simultaneous scraper processors",
        )

        completed = 0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_context = {}

            for position, project_id in enumerate(project_ids, start=1):
                processor_id = ((position - 1) % workers) + 1

                future = executor.submit(
                    self._process_project_with_progress,
                    processor=processor,
                    project_id=project_id,
                    source=source,
                    processor_id=processor_id,
                    project_brief=project_briefs.get(project_id, {}),
                    progress_callback=progress_callback,
                )

                future_to_context[future] = {
                    "project_id": project_id,
                    "processor_id": processor_id,
                }

            for future in as_completed(future_to_context):
                completed += 1

                context = future_to_context[future]
                project_id = context["project_id"]
                processor_id = context["processor_id"]
                project_brief = project_briefs.get(project_id, {})

                try:
                    item = future.result()
                except Exception as exc:
                    item = {
                        "source": source,
                        "project_id": project_id,
                        "project_name": project_brief.get(
                            "project_name",
                            f"ProjectID {project_id}",
                        ),
                        "project_type": project_brief.get("project_type", ""),
                        "processor_id": processor_id,
                        "success": False,
                        "status": "error",
                        "message": str(exc),
                        "search_mode": None,
                        "search_term": None,
                    }

                item["processor_id"] = processor_id

                self._add_item_to_summary(summary, item)
                self._emit_item(
                    progress_callback,
                    item,
                    completed,
                    len(project_ids),
                    processor_id=processor_id,
                )

                if sleep_between_requests and completed < len(project_ids):
                    time.sleep(random.uniform(0.5, 1.5))

        return summary

    def _process_project_with_progress(
        self,
        processor: Callable[[int], Dict],
        project_id: int,
        source: str,
        processor_id: int,
        project_brief: Dict,
        progress_callback: ProgressCallback,
    ) -> Dict:
        self._emit(
            progress_callback,
            source=source,
            processor_id=processor_id,
            status="processing",
            project_id=project_id,
            project_name=project_brief.get("project_name", ""),
            project_type=project_brief.get("project_type", ""),
            message=f"Processor {processor_id} started ProjectID {project_id}",
        )

        item = processor(project_id)
        item["processor_id"] = processor_id

        return item

    def _process_pgp_project(self, project_id: int) -> Dict:
        session = self.Session()

        try:
            project = session.get(Project, project_id)

            if not project:
                return self._project_item(
                    "PGP",
                    project_id,
                    None,
                    None,
                    False,
                    "error",
                    "Project not found",
                )

            project_name = project.ProjectName or f"ProjectID {project.ProjectID}"
            project_type = project.project_discriminator

            try:
                seeker = PGPSeeker()
                was_updated = seeker.seek_and_update(session, project)

                if was_updated:
                    session.commit()

                    message = "Updated from PGP"

                    if seeker.last_search_mode == "nup":
                        message = "Updated from PGP using NUP"
                    elif seeker.last_search_mode == "name":
                        message = "Updated from PGP using name variants"

                    return self._project_item(
                        "PGP",
                        project.ProjectID,
                        project_name,
                        project_type,
                        True,
                        "success",
                        message,
                        search_mode=seeker.last_search_mode,
                        search_term=seeker.last_search_term,
                    )

                session.rollback()

                return self._project_item(
                    "PGP",
                    project.ProjectID,
                    project_name,
                    project_type,
                    False,
                    "failed",
                    "No PGP match or no update applied",
                    search_mode=getattr(seeker, "last_search_mode", None),
                    search_term=getattr(seeker, "last_search_term", None),
                )

            except Exception as exc:
                session.rollback()

                return self._project_item(
                    "PGP",
                    project.ProjectID,
                    project_name,
                    project_type,
                    False,
                    "error",
                    str(exc),
                )

        finally:
            session.close()

    def _process_seo_project(self, project_id: int) -> Dict:
        session = self.Session()

        try:
            project = session.get(TransmissionProject, project_id)

            if not project:
                return self._project_item(
                    "SEO",
                    project_id,
                    None,
                    "transmission",
                    False,
                    "error",
                    "Project not found",
                )

            project_name = project.ProjectName or f"ProjectID {project.ProjectID}"
            project_type = "transmission"

            try:
                seeker = SEOSeeker()
                was_updated = seeker.seek_and_update(session, project)

                if was_updated:
                    session.commit()

                    return self._project_item(
                        "SEO",
                        project.ProjectID,
                        project_name,
                        project_type,
                        True,
                        "success",
                        "Updated from SEO",
                        search_mode="nup",
                        search_term=str(project.NUP),
                    )

                session.rollback()

                return self._project_item(
                    "SEO",
                    project.ProjectID,
                    project_name,
                    project_type,
                    False,
                    "failed",
                    "No SEO match or no update applied",
                    search_mode="nup",
                    search_term=str(project.NUP),
                )

            except Exception as exc:
                session.rollback()

                return self._project_item(
                    "SEO",
                    project.ProjectID,
                    project_name,
                    project_type,
                    False,
                    "error",
                    str(exc),
                )

        finally:
            session.close()

    def _get_project_briefs(self, project_ids: List[int]) -> Dict[int, Dict]:
        if not project_ids:
            return {}

        session = self.Session()

        try:
            rows = (
                session.query(
                    Project.ProjectID,
                    Project.ProjectName,
                    Project.project_discriminator,
                )
                .filter(Project.ProjectID.in_(project_ids))
                .all()
            )

            return {
                row.ProjectID: {
                    "project_name": row.ProjectName or f"ProjectID {row.ProjectID}",
                    "project_type": row.project_discriminator,
                }
                for row in rows
            }

        finally:
            session.close()

    @staticmethod
    def _project_item(
        source: str,
        project_id: int,
        project_name: Optional[str],
        project_type: Optional[str],
        success: bool,
        status: str,
        message: str,
        search_mode: Optional[str] = None,
        search_term: Optional[str] = None,
    ) -> Dict:
        return {
            "source": source,
            "project_id": project_id,
            "project_name": project_name or f"ProjectID {project_id}",
            "project_type": project_type,
            "success": success,
            "status": status,
            "message": message,
            "search_mode": search_mode,
            "search_term": search_term,
        }

    @staticmethod
    def _empty_summary(source: str, update_existing: bool, workers: int) -> Dict:
        return {
            "source": source,
            "mode": "update_existing" if update_existing else "only_missing",
            "workers": workers,
            "total": 0,
            "success": 0,
            "failed": 0,
            "items": [],
        }

    @staticmethod
    def _add_item_to_summary(summary: Dict, item: Dict) -> None:
        if item.get("success"):
            summary["success"] += 1
        else:
            summary["failed"] += 1

        summary["items"].append(item)

    @staticmethod
    def _merge_summary(target: Dict, source_summary: Dict) -> None:
        target["total"] += source_summary.get("total", 0)
        target["success"] += source_summary.get("success", 0)
        target["failed"] += source_summary.get("failed", 0)
        target["items"].extend(source_summary.get("items", []))

    @staticmethod
    def _normalize_workers(workers: int, maximum: int) -> int:
        try:
            worker_count = int(workers)
        except (TypeError, ValueError):
            worker_count = 1

        return max(1, min(worker_count, maximum))

    @staticmethod
    def _normalize_project_types(
        project_types: list[str] | None,
        allowed_types: list[str],
        default_types: list[str],
    ) -> list[str]:
        if not project_types:
            return default_types.copy()

        normalized_types = []

        for project_type in project_types:
            clean_type = str(project_type).lower().strip()

            if clean_type in allowed_types and clean_type not in normalized_types:
                normalized_types.append(clean_type)

        if not normalized_types:
            return default_types.copy()

        return normalized_types

    @staticmethod
    def _emit(callback: ProgressCallback, **payload) -> None:
        if callback:
            callback(payload)

    @classmethod
    def _emit_item(
        cls,
        callback: ProgressCallback,
        item: Dict,
        index: int,
        total: int,
        processor_id: int | str | None = None,
    ) -> None:
        cls._emit(
            callback,
            source=item.get("source", ""),
            processor_id=processor_id or item.get("processor_id", 1),
            status=item.get("status", ""),
            index=index,
            total=total,
            project_id=item.get("project_id"),
            project_name=item.get("project_name", ""),
            project_type=item.get("project_type", ""),
            message=item.get("message", ""),
            search_mode=item.get("search_mode"),
            search_term=item.get("search_term"),
        )


def run_web_scraping(
    source: str,
    limit: int,
    update_existing: bool,
    progress_callback=None,
    sleep_between_requests: bool = True,
    workers: int = 1,
    project_types: list[str] | None = None,
) -> Dict:
    """Run the requested scraper source and return a structured summary."""
    source = source.lower().strip()

    if project_types is None:
        project_types = ["transmission", "generation", "bess"]

    if source == "seo":
        project_types = ["transmission"]

    manager = WebScraperManager()

    return manager.update_from_sources(
        source=source,
        limit=limit,
        update_existing=update_existing,
        progress_callback=progress_callback,
        sleep_between_requests=sleep_between_requests,
        workers=workers,
        project_types=project_types,
    )


def validate_scraper_reference_data() -> Dict:
    """Validate required Source and MilestoneType rows before running the scraper."""
    manager = WebScraperManager()
    return manager.validate_reference_data()


def _cli_progress(event: Dict) -> None:
    processor_id = event.get("processor_id", "")
    status = event.get("status", "")
    source = event.get("source", "")
    index = event.get("index")
    total = event.get("total")
    project_id = event.get("project_id", "")
    project_name = event.get("project_name", "")
    project_type = event.get("project_type", "")
    message = event.get("message", "")
    search_mode = event.get("search_mode")
    search_term = event.get("search_term")

    prefix = f"[{source}]"

    if processor_id:
        prefix += f" Processor {processor_id}"

    if index is not None and total is not None:
        prefix += f" {index}/{total}"

    detail = message

    if search_mode or search_term:
        detail += f" | mode={search_mode} | term={search_term}"

    if project_name:
        print(
            f"{prefix} {status}: ProjectID={project_id} | "
            f"type={project_type} | {project_name} - {detail}"
        )
    else:
        print(f"{prefix} {status}: {detail}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update projects from web sources")

    parser.add_argument(
        "--source",
        choices=["pgp", "seo", "all"],
        default="all",
        help="Data source to scrape",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of projects to process",
    )

    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Re-scrape projects that already have source data",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of simultaneous scraper processors. PGP max 3, SEO max 2.",
    )

    parser.add_argument(
        "--project-types",
        nargs="+",
        choices=["transmission", "generation", "bess"],
        default=["transmission", "generation", "bess"],
        help="Project types to process with PGP. SEO always uses transmission.",
    )

    parser.add_argument(
        "--no-sleep",
        action="store_true",
        help="Disable pauses between requests",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Database Web Scraper")
    print("=" * 60)
    print(f"Using database: {get_connection_string()}")

    manager = WebScraperManager()

    result = manager.update_from_sources(
        source=args.source,
        limit=args.limit,
        update_existing=args.update_existing,
        progress_callback=_cli_progress,
        sleep_between_requests=not args.no_sleep,
        workers=args.workers,
        project_types=args.project_types,
    )

    print("-" * 60)
    print(
        f"Finished. Total: {result['total']} | "
        f"Success: {result['success']} | Failed: {result['failed']}"
    )
