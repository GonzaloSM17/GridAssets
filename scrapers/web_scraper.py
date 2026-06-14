from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from typing import Optional
import logging
from contextlib import contextmanager


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WebScraper:
    """Robust web scraper with error handling and context manager."""

    def __init__(
        self,
        headless: bool = True,
        slow_mo: int = 0,
        timeout_ms: int = 10000,
        user_agent: Optional[str] = None,
    ):
        self.headless = headless
        self.slow_mo = slow_mo
        self.timeout_ms = timeout_ms
        self.user_agent = user_agent

        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self._is_started = False

    def start(self) -> None:
        """Starts the browser and context."""
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo,
            )

            context_kwargs = {
                "locale": "es-CL",
                "viewport": {"width": 1280, "height": 900},
            }

            if self.user_agent:
                context_kwargs["user_agent"] = self.user_agent

            self.context = self.browser.new_context(**context_kwargs)
            self._is_started = True
            # logger.info("Web scrapper started successfully")
        except Exception as e:
            logger.error(f"Error starting web scrapper: {e}")
            self.close()
            raise

    def new_page(self) -> Page:
        """Creates a new page with configured timeout."""
        if not self._is_started or not self.context:
            raise RuntimeError("Scrapper has not been started. Call start() first.")

        try:
            page = self.context.new_page()
            page.set_default_timeout(self.timeout_ms)
            page.set_default_navigation_timeout(self.timeout_ms)
            # logger.info("New page created")
            return page
        except Exception as e:
            logger.error(f"Error creating new page: {e}")
            raise

    def close(self) -> None:
        """Closes the browser and releases resources."""
        try:
            if self.context:
                self.context.close()
                self.context = None
                # logger.info("Context closed")

            if self.browser:
                self.browser.close()
                self.browser = None
                # logger.info("Browser closed")

            if self.playwright:
                self.playwright.stop()
                self.playwright = None
                # logger.info("Playwright stopped")

            self._is_started = False

        except Exception as e:
            logger.error(f"Error closing scraper: {e}")

    @contextmanager
    def page(self):
        """Context manager to create and close pages automatically."""
        page = None
        try:
            page = self.new_page()
            yield page
        except Exception as e:
            logger.error(f"Error in page: {e}")
            raise

        finally:
            if page:
                try:
                    page.close()
                    # logger.info("Page closed")
                except Exception as e:
                    logger.error(f"Error closing page: {e}")

    def __enter__(self):
        """Allows using WebScrapper as a context manager."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Automatically closes when exiting the context."""
        self.close()
        if exc_type:
            logger.error(f"Exception in context: {exc_val}")
        return False
