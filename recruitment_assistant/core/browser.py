import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from recruitment_assistant.config.settings import get_settings


if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


@dataclass
class BrowserSession:
    playwright: Playwright
    browser: Browser | None
    context: BrowserContext
    page: Page

    def close(self) -> None:
        self.context.close()
        if self.browser is not None:
            self.browser.close()
        self.playwright.stop()


def get_state_path(platform_code: str, account_name: str = "default") -> Path:
    settings = get_settings()
    safe_account = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in account_name)
    return settings.browser_state_dir / f"{platform_code}_{safe_account}.json"


def get_user_data_dir(platform_code: str, account_name: str = "default") -> Path:
    settings = get_settings()
    safe_account = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in account_name)
    return settings.browser_state_dir / f"{platform_code}_{safe_account}_profile"


def open_browser_session(
    state_path: Path | None = None,
    headless: bool | None = None,
    viewport: dict | None = None,
    user_data_dir: Path | None = None,
) -> BrowserSession:
    settings = get_settings()
    playwright = sync_playwright().start()
    browser_headless = settings.playwright_headless if headless is None else headless
    context_kwargs = {
        "viewport": viewport or {"width": 1440, "height": 900},
        "accept_downloads": True,
    }

    if user_data_dir:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=browser_headless,
                **context_kwargs,
            )
            page = context.pages[0] if context.pages else context.new_page()
            return BrowserSession(playwright=playwright, browser=context.browser, context=context, page=page)
        except Exception:
            playwright.stop()
            raise

    try:
        browser = playwright.chromium.launch(headless=browser_headless)
        if state_path and state_path.exists():
            context_kwargs["storage_state"] = str(state_path)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        return BrowserSession(playwright=playwright, browser=browser, context=context, page=page)
    except Exception:
        playwright.stop()
        raise


def save_storage_state(context: BrowserContext, state_path: Path) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        context.storage_state(path=str(state_path), indexed_db=True)
    except TypeError:
        context.storage_state(path=str(state_path))

