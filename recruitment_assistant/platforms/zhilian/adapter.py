from pathlib import Path

from loguru import logger
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from recruitment_assistant.core.browser import get_state_path, open_browser_session, save_storage_state
from recruitment_assistant.platforms.base import BasePlatformAdapter
from recruitment_assistant.utils.hash_utils import text_hash
from recruitment_assistant.utils.snapshot_utils import save_text_snapshot


class ZhilianAdapter(BasePlatformAdapter):
    platform_code = "zhilian"
    login_url = "https://passport.zhaopin.com/login"
    home_url = "https://rd5.zhaopin.com/"

    def __init__(self, account_name: str = "default") -> None:
        self.account_name = account_name
        self.state_path = get_state_path(self.platform_code, account_name)

    def login(self) -> None:
        self.login_manually(wait_seconds=180)

    def login_manually(self, wait_seconds: int = 180) -> Path:
        session = open_browser_session(headless=False)
        try:
            page = session.page
            page.goto(self.login_url, wait_until="domcontentloaded")
            logger.info("请在打开的浏览器中完成人工扫码/短信登录。")
            try:
                page.wait_for_url(lambda url: "login" not in url.lower(), timeout=wait_seconds * 1000)
            except PlaywrightTimeoutError:
                logger.warning("等待登录跳转超时，将继续保存当前登录态。")
            save_storage_state(session.context, self.state_path)
            logger.info("智联招聘登录态已保存：{}", self.state_path)
            return self.state_path
        finally:
            session.close()

    def is_logged_in(self) -> bool:
        if not self.state_path.exists():
            return False
        session = open_browser_session(state_path=self.state_path, headless=True)
        try:
            page = session.page
            page.goto(self.home_url, wait_until="domcontentloaded", timeout=30000)
            current_url = page.url.lower()
            content = page.content().lower()
            login_markers = ["passport", "login"]
            return not any(marker in current_url or marker in content for marker in login_markers)
        except Exception as exc:
            logger.warning("智联招聘登录态检测失败：{}", exc)
            return False
        finally:
            session.close()

    def capture_current_page(self, target_url: str | None = None, wait_seconds: int = 30) -> dict:
        if not self.state_path.exists():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = open_browser_session(state_path=self.state_path, headless=False)
        try:
            page = session.page
            page.goto(target_url or self.home_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(wait_seconds * 1000)
            html = page.content()
            snapshot_path = save_text_snapshot(self.platform_code, page.url, html)
            content_hash = text_hash(html) or ""
            return {
                "platform_code": self.platform_code,
                "source_url": page.url,
                "raw_json": {
                    "title": page.title(),
                    "url": page.url,
                    "capture_mode": "current_page",
                },
                "raw_html_path": str(snapshot_path),
                "content_hash": content_hash,
            }
        finally:
            session.close()

    def capture_manual_resume_pages(self, target_url: str | None = None, max_pages: int = 5) -> list[dict]:
        if not self.state_path.exists():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = open_browser_session(state_path=self.state_path, headless=False)
        results = []
        try:
            page = session.page
            page.goto(target_url or self.home_url, wait_until="domcontentloaded", timeout=30000)
            for index in range(max_pages):
                input(f"请在浏览器中打开第 {index + 1} 个候选人/简历页面，完成后按 Enter 保存快照...")
                html = page.content()
                snapshot_path = save_text_snapshot(self.platform_code, page.url, html)
                results.append(
                    {
                        "platform_code": self.platform_code,
                        "source_url": page.url,
                        "raw_json": {
                            "title": page.title(),
                            "url": page.url,
                            "capture_mode": "manual_resume_page",
                            "manual_index": index + 1,
                        },
                        "raw_html_path": str(snapshot_path),
                        "content_hash": text_hash(html) or "",
                    }
                )
            return results
        finally:
            session.close()

    def fetch_resume_list(self) -> list[dict]:
        raise NotImplementedError("智联招聘自动简历列表采集将在页面结构确认后实现。")

    def fetch_resume_detail(self, resume_id: str) -> dict:
        raise NotImplementedError("智联招聘自动简历详情采集将在页面结构确认后实现。")
