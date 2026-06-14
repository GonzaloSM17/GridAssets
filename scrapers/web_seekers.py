# Project Seekers
"""
Web scrapers for extracting project information from external sources.
- PGPSeeker: searches in the PGP portal.
- SEOSeeker: searches in Seguimiento Ejecucion de Obras.
"""

from __future__ import annotations

import itertools
import re
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

import pandas as pd
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from scrapers.web_scraper import WebScraper
from database.db_orm_model import (
    MilestoneType,
    Project,
    RelevantDate,
    Source,
    TransmissionProject,
)


class PGPSeeker(WebScraper):
    """Search projects on the PGP website and update the database."""

    SEARCH_URL = "https://pgp.coordinador.cl/irequests"
    MAX_SEARCH_TERMS = 40
    TRUNCATION_RATIOS = (0.8, 0.7, 0.6)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page = None
        self.last_search_term: Optional[str] = None
        self.last_search_mode: Optional[str] = None

    def seek_and_update(self, session, project: Project) -> bool:
        """Search a project in PGP and update project URL, NUP and relevant dates."""
        project_name = getattr(project, "ProjectName", None)
        existing_nup = getattr(project, "NUP", None)

        if not project_name and not existing_nup:
            return False

        try:
            if not self._open_page_pgp():
                return False

            found = False

            if existing_nup:
                found = self._search_and_open_with_terms(
                    terms=self._generate_nup_terms(existing_nup),
                    mode="nup",
                    expected_nup=existing_nup,
                )

                if found:
                    self.last_search_mode = "nup"

            if not found and project_name:
                self._reset_search_page()
                found = self._search_and_open_with_terms(
                    terms=self._generate_name_search_terms(project_name),
                    mode="name",
                    expected_nup=existing_nup,
                )

                if found:
                    self.last_search_mode = "name"

            if not found:
                return False

            page_nup = self._get_nup()
            if existing_nup and page_nup and int(page_nup) != int(existing_nup):
                return False

            project.URL = self.page.url
            if page_nup:
                project.NUP = page_nup

            dates = self._get_dates()
            self._save_dates_to_db(session, project.ProjectID, dates)

            return True

        except Exception as exc:
            print(f"Error processing {project_name or existing_nup}: {exc}")
            return False

        finally:
            self.close()

    def _open_page_pgp(self) -> bool:
        """Open the PGP page and validate that the search input is ready."""
        try:
            self.start()
            self.page = self.new_page()
            return self._reset_search_page()

        except Exception as exc:
            print(f"Failed to load PGP page: {exc}")
            return False

    def _reset_search_page(self) -> bool:
        """Navigate back to the PGP search page and wait until it is ready."""
        try:
            self.page.goto(
                self.SEARCH_URL,
                wait_until="domcontentloaded",
                timeout=10000,
            )
            self.page.locator("#filtroPorNombres input").nth(1).wait_for(
                state="visible",
                timeout=10000,
            )
            self.page.wait_for_timeout(1000)
            return True
        except Exception:
            return False

    def _search_and_open_with_terms(
        self,
        terms: Iterable[str],
        mode: str,
        expected_nup: Optional[int] = None,
    ) -> bool:
        """Try search terms until a valid PGP result can be opened."""
        for term in terms:
            clean_term = str(term).strip()
            if not clean_term:
                continue

            if not self._search_single_term(clean_term):
                continue

            if not self._open_first_result():
                self._reset_search_page()
                continue

            page_nup = self._get_nup()
            if expected_nup and page_nup and int(page_nup) != int(expected_nup):
                self._reset_search_page()
                continue

            self.last_search_term = clean_term
            self.last_search_mode = mode
            return True

        return False

    def _search_single_term(self, term: str) -> bool:
        search_input = self.page.locator("#filtroPorNombres input").nth(1)
        previous_signature = self._get_results_signature()
        search_input.fill(term)
        return self._wait_results_ready(previous_signature)

    def _get_results_signature(self) -> str:
        try:
            return self.page.locator("#resultadosFiltro").inner_text()
        except Exception:
            return ""

    def _wait_results_ready(self, previous_signature: str) -> bool:
        try:
            self.page.wait_for_function(
                """
                previousSignature => {
                    const element = document.querySelector('#resultadosFiltro');
                    return element &&
                           element.innerText.trim() !== previousSignature.trim() &&
                           element.querySelector('button');
                }
                """,
                arg=previous_signature,
                timeout=3000,
            )
            return True
        except PlaywrightTimeout:
            return False

    def _open_first_result(self) -> bool:
        try:
            button = self.page.locator("#resultadosFiltro button").first
            button.wait_for(state="visible")
            old_url = self.page.url
            button.click()
            self.page.wait_for_url(lambda url: url != old_url, timeout=self.timeout_ms)
            self.page.locator("#infoBarComponent").wait_for(state="visible")
            return True
        except PlaywrightTimeout:
            return False

    def _get_nup(self) -> Optional[int]:
        try:
            text = self.page.locator(
                "//h4[contains(normalize-space(.), 'NUP')]"
            ).inner_text()
            match = re.search(r"\d+", text)
            return int(match.group(0)) if match else None
        except Exception:
            return None

    def _get_dates(self) -> Dict[str, Optional[date]]:
        data = {
            "commissioning_estimated": None,
            "commissioning_actual": None,
            "cod_estimated": None,
            "cod_actual": None,
        }

        try:
            container = self.page.locator("#infoBarComponent")
            estimated_elements = container.locator(
                "xpath=.//div[contains(text(), 'Estimada:')]"
            ).all()
            actual_elements = container.locator(
                "xpath=.//div[contains(text(), 'Real:')]"
            ).all()

            if len(estimated_elements) >= 1:
                data["commissioning_estimated"] = self._parse_date(
                    estimated_elements[0].inner_text()
                )
            if len(actual_elements) >= 1:
                data["commissioning_actual"] = self._parse_date(
                    actual_elements[0].inner_text()
                )
            if len(estimated_elements) >= 2:
                data["cod_estimated"] = self._parse_date(
                    estimated_elements[1].inner_text()
                )
            if len(actual_elements) >= 2:
                data["cod_actual"] = self._parse_date(actual_elements[1].inner_text())

        except Exception as exc:
            print(f"Error extracting PGP dates: {exc}")

        return data

    def _save_dates_to_db(self, session, project_id: int, dates: dict) -> None:
        """Save extracted dates into the RelevantDate table."""
        source_pgp = session.query(Source).filter(Source.SourceName == "PGP").first()
        if not source_pgp:
            raise RuntimeError("Missing required Source row: PGP")

        extraction_time = datetime.now()

        milestone_map = {
            "commissioning_estimated": "Commissioning_Estimated",
            "commissioning_actual": "Commissioning_Actual",
            "cod_estimated": "COD_Estimated",
            "cod_actual": "COD_Actual",
        }

        for key, milestone_name in milestone_map.items():
            date_value = dates.get(key)
            if not date_value:
                continue

            milestone = (
                session.query(MilestoneType)
                .filter(MilestoneType.MilestoneName == milestone_name)
                .first()
            )
            if not milestone:
                raise RuntimeError(f"Missing required MilestoneType row: {milestone_name}")

            existing = (
                session.query(RelevantDate)
                .filter(
                    RelevantDate.ProjectID == project_id,
                    RelevantDate.MilestoneTypeID == milestone.MilestoneTypeID,
                    RelevantDate.SourceID == source_pgp.SourceID,
                )
                .first()
            )

            datetime_value = datetime.combine(date_value, datetime.min.time())

            if existing:
                existing.DateValue = datetime_value
                existing.ExtractedAt = extraction_time
            else:
                session.add(
                    RelevantDate(
                        ProjectID=project_id,
                        MilestoneTypeID=milestone.MilestoneTypeID,
                        SourceID=source_pgp.SourceID,
                        DateValue=datetime_value,
                        ExtractedAt=extraction_time,
                    )
                )

    @classmethod
    def _generate_name_search_terms(cls, project_name: str) -> List[str]:
        """Build ordered search terms using full-name and truncated-name variants."""
        base_name = cls._normalize_name(project_name)
        candidates: List[str] = []

        base_candidates = [base_name]
        base_candidates.extend(cls._generate_truncated_names(base_name))

        for candidate in base_candidates:
            candidates.append(candidate)
            candidates.extend(sorted(cls._generate_variants(candidate)))

        return cls._unique_non_empty(candidates)[: cls.MAX_SEARCH_TERMS]

    @staticmethod
    def _generate_nup_terms(nup: int) -> List[str]:
        clean_nup = str(nup).strip()
        return [clean_nup, f"NUP {clean_nup}", f"NUP: {clean_nup}"]

    @staticmethod
    def _normalize_name(name: str) -> str:
        base = name.strip()
        base = base.split(",", 1)[0].strip()
        base = re.split(r"\b[yY]\b", base, maxsplit=1)[0].strip()
        base = re.sub(r"\s+", " ", base)
        return base

    @classmethod
    def _generate_truncated_names(cls, base_name: str) -> List[str]:
        """Generate 80%, 70% and 60% name prefixes, cutting only by words."""
        words = base_name.split()
        if len(words) < 5:
            return []

        truncated_names: List[str] = []
        for ratio in cls.TRUNCATION_RATIOS:
            word_count = max(3, int(round(len(words) * ratio)))
            if word_count < len(words):
                truncated_names.append(" ".join(words[:word_count]))

        return cls._unique_non_empty(truncated_names)

    @staticmethod
    def _generate_variants(base_name: str) -> set[str]:
        equivalence_groups = [
            [" - ", "-", " - "],
            [" S/E ", " SE ", "Subestacion", "Subestación"],
            ["Aumento de Capacidad", "Aumento Capacidad"],
            ["Capacidad", "Cap."],
            ["Linea ", "Línea ", "LT "],
            ["Lineas", "Líneas", "LT"],
            ["º", "°"],
        ]

        applicable_groups = []
        for group in equivalence_groups:
            if any(variant in base_name for variant in group):
                applicable_groups.append(group)

        if not applicable_groups:
            return {base_name.strip()}

        variants = set()
        for replacements in itertools.product(*applicable_groups):
            name = base_name
            for group, replacement in zip(applicable_groups, replacements):
                for variant in group:
                    if variant in name:
                        name = name.replace(variant, replacement)
            variants.add(re.sub(r"\s+", " ", name).strip())

        return variants

    @staticmethod
    def _unique_non_empty(values: Iterable[str]) -> List[str]:
        seen = set()
        result: List[str] = []
        for value in values:
            clean_value = str(value).strip()
            if clean_value and clean_value not in seen:
                seen.add(clean_value)
                result.append(clean_value)
        return result

    @staticmethod
    def _parse_date(text: str) -> Optional[date]:
        try:
            raw = text.split(":")[-1].strip()
            timestamp = pd.to_datetime(raw, dayfirst=True, errors="coerce")
            return None if pd.isna(timestamp) else timestamp.date()
        except Exception:
            return None


class SEOSeeker(WebScraper):
    """Search transmission projects on Seguimiento Ejecucion de Obras."""

    SEARCH_URL = "https://seguimientoejecucionobras.coordinador.cl/"
    MAX_PAGE_LOAD_RETRIES = 5
    MAX_SEARCH_RETRIES = 3
    PAGE_LOAD_TIMEOUT = 10000
    RETRY_DELAY = 3000

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.page = None

    def seek_and_update(self, session, project: TransmissionProject) -> bool:
        """Search a transmission project in SEO and update relevant dates."""
        if not getattr(project, "NUP", None):
            return False

        try:
            if not self._open_page_tracking():
                return False

            value = self._resolve_autocomplete_value(project.NUP)
            if not value:
                return False

            if not self._search_by_nup_with_retries(value):
                return False

            dates = self._get_tracking_dates()
            self._save_dates_to_db(session, project.ProjectID, dates)

            return True

        except Exception as exc:
            print(f"Error processing {project.ProjectName}: {exc}")
            return False

        finally:
            self.close()

    def _open_page_tracking(self) -> bool:
        self.start()
        self.page = self.new_page()

        for attempt in range(self.MAX_PAGE_LOAD_RETRIES):
            try:
                self.page.goto(
                    self.SEARCH_URL,
                    wait_until="domcontentloaded",
                    timeout=self.PAGE_LOAD_TIMEOUT,
                )
                self.page.wait_for_selector(
                    "input[list='dynmicName']",
                    timeout=20000,
                    state="visible",
                )
                self.page.wait_for_timeout(2000)

                has_options = self.page.evaluate(
                    """
                    () => {
                        const list = document.querySelector('datalist#dynmicName');
                        return list && list.options.length > 0;
                    }
                    """
                )
                if has_options:
                    return True

            except Exception:
                pass

            if attempt < self.MAX_PAGE_LOAD_RETRIES - 1:
                self.page.wait_for_timeout(self.RETRY_DELAY)

        return False

    def _search_by_nup_with_retries(self, value: str) -> bool:
        for attempt in range(self.MAX_SEARCH_RETRIES):
            try:
                search_input = self.page.locator("input[list='dynmicName']").first
                search_button = self.page.locator("button.btn.btn-default.p-0.ml-2")

                search_input.click(timeout=5000)
                search_input.fill("")
                self.page.wait_for_timeout(500)
                search_input.type(value, delay=100)
                self.page.wait_for_timeout(1000)

                search_button.click(timeout=5000)
                self.page.wait_for_selector(
                    'xpath=//*[@id="conten1"]/div/div[3]/div[1]',
                    timeout=25000,
                )
                return True

            except (PlaywrightTimeout, Exception):
                pass

            if attempt < self.MAX_SEARCH_RETRIES - 1:
                self.page.wait_for_timeout(2000)

        return False

    def _get_tracking_dates(self) -> dict:
        default_result = {"start_construction": None, "cod_estimated": None}

        try:
            base = self.page.locator('xpath=//*[@id="conten1"]/div/div[3]/div[1]')
            base.locator("xpath=div[5]/div[2]/div").wait_for(
                state="attached",
                timeout=10000,
            )

            def read_date(relative_xpath: str) -> Optional[date]:
                try:
                    text = base.locator(f"xpath={relative_xpath}").text_content(
                        timeout=5000
                    )
                    return self._parse_date(text) if text else None
                except Exception:
                    return None

            return {
                "start_construction": read_date("div[5]/div[2]/div"),
                "cod_estimated": read_date("div[8]/div[2]/div"),
            }

        except Exception:
            return default_result

    def _save_dates_to_db(self, session, project_id: int, dates: dict) -> None:
        """Save extracted dates into the RelevantDate table."""
        source_seo = session.query(Source).filter(Source.SourceName == "SEO").first()
        if not source_seo:
            raise RuntimeError("Missing required Source row: SEO")

        extraction_time = datetime.now()

        milestone_map = {
            "start_construction": "Start_Construction",
            "cod_estimated": "COD_Estimated",
        }

        for key, milestone_name in milestone_map.items():
            date_value = dates.get(key)
            if not date_value:
                continue

            milestone = (
                session.query(MilestoneType)
                .filter(MilestoneType.MilestoneName == milestone_name)
                .first()
            )
            if not milestone:
                raise RuntimeError(f"Missing required MilestoneType row: {milestone_name}")

            existing = (
                session.query(RelevantDate)
                .filter(
                    RelevantDate.ProjectID == project_id,
                    RelevantDate.MilestoneTypeID == milestone.MilestoneTypeID,
                    RelevantDate.SourceID == source_seo.SourceID,
                )
                .first()
            )

            datetime_value = datetime.combine(date_value, datetime.min.time())

            if existing:
                existing.DateValue = datetime_value
                existing.ExtractedAt = extraction_time
            else:
                session.add(
                    RelevantDate(
                        ProjectID=project_id,
                        MilestoneTypeID=milestone.MilestoneTypeID,
                        SourceID=source_seo.SourceID,
                        DateValue=datetime_value,
                        ExtractedAt=extraction_time,
                    )
                )

    def _resolve_autocomplete_value(self, nup: int) -> Optional[str]:
        try:
            self.page.wait_for_timeout(1500)
            return self.page.evaluate(
                """
                nup => {
                    const list = document.querySelector('datalist#dynmicName');
                    if (!list) return null;
                    const option = Array.from(list.options)
                        .find(item => item.textContent.includes(`NUP: ${nup}`));
                    return option ? option.value : null;
                }
                """,
                str(nup),
            )
        except Exception:
            return None

    @staticmethod
    def _parse_date(text: str) -> Optional[date]:
        try:
            raw = text.split(":")[-1].strip()
            timestamp = pd.to_datetime(raw, dayfirst=True, errors="coerce")
            return None if pd.isna(timestamp) else timestamp.date()
        except Exception:
            return None
