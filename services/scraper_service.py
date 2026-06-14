"""
Service layer for running web scraping from UI code such as Streamlit.
Keep Streamlit widgets outside this file; this module only returns structured data.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from scrapers.db_scraper import WebScraperManager

ProgressCallback = Optional[Callable[[Dict], None]]


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
