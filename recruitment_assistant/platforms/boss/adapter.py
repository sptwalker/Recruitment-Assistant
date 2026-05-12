import json
import re
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Callable

from loguru import logger
from playwright.sync_api import Download, Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError

from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.core.browser import get_state_path, save_storage_state
from recruitment_assistant.core.cdp_browser import CDPBrowserSession, open_cdp_browser_session
from recruitment_assistant.platforms.base import BasePlatformAdapter
from recruitment_assistant.utils.hash_utils import text_hash
from recruitment_assistant.utils.snapshot_utils import safe_filename, save_text_snapshot


class BossAdapter(BasePlatformAdapter):
    platform_code = "boss"
    login_url = "https://www.zhipin.com/"
    home_url = "https://www.zhipin.com/web/chat/index"

    def __init__(self, account_name: str = "default") -> None:
        self.account_name = account_name
        self.state_path = get_state_path(self.platform_code, account_name)
        self.user_data_dir = self.state_path.with_name(f"{self.state_path.stem}_profile")

    def _has_persistent_profile(self) -> bool:
        if not self.user_data_dir.exists() or not self.user_data_dir.is_dir():
            return False
        try:
            return any(self.user_data_dir.iterdir())
        except OSError:
            return False

    def _open_stateful_session(self, headless: bool = False, force_profile: bool = False, force_storage_state: bool = False):
        return open_cdp_browser_session(user_data_dir=self.user_data_dir)

    def _page_url_text(self, page: Page) -> tuple[str, str]:
        try:
            url = page.url.lower()
        except Exception:
            url = ""
        try:
            text = page.locator("body").inner_text(timeout=3000).lower()
        except Exception:
            text = ""
        return url, text

    def _is_authenticated_page(self, page: Page) -> bool:
        url, text = self._page_url_text(page)
        if "/web/chat" not in url:
            return False
        for selector in [".chat-list", ".user-list", ".friend-list", "[class*='chat-list']", "[class*='friend-list']"]:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible(timeout=1000):
                    return True
            except Exception:
                continue
        logged_in_markers = ["沟通中", "新招呼", "联系人", "附件简历", "牛人"]
        return sum(1 for marker in logged_in_markers if marker.lower() in text) >= 2

    def _is_login_page(self, page: Page) -> bool:
        url, text = self._page_url_text(page)
        login_url_markers = ["/web/user", "/user/", "login", "passport", "account"]
        login_text_markers = ["扫码登录", "验证码", "手机号登录", "立即登录", "请先登录", "微信扫码", "安全验证", "登录/注册"]
        if any(marker in url for marker in login_url_markers):
            return True
        if any(marker.lower() in text for marker in login_text_markers):
            return True
        return not self._is_authenticated_page(page)

    def _page_state_summary(self, page: Page) -> dict:
        url, text = self._page_url_text(page)
        try:
            title = page.title()
        except Exception:
            title = ""
        return {
            "url": url,
            "title": title,
            "text_length": len(text),
            "is_blank": url in {"", "about:blank"},
            "is_login_page": self._is_login_page(page),
            "is_authenticated": self._is_authenticated_page(page),
        }

    def _assert_authenticated_chat_page(self, page: Page, context: str = "BOSS直聘页面") -> None:
        state = self._page_state_summary(page)
        url = state.get("url") or ""
        if state.get("is_blank"):
            raise RuntimeError(f"BOSS直聘页面异常：{context}进入空白页 about:blank，请改用当前页面接管登录。")
        if state.get("is_login_page"):
            raise RuntimeError(f"BOSS直聘自动登录未成功：{context}当前仍停留在登录页面，请重新保存登录态。当前页面={url}")
        if "/web/chat" not in url or not state.get("is_authenticated"):
            raise RuntimeError(f"BOSS直聘页面异常：{context}未进入有效沟通页，请重新保存登录态。当前页面={url}")

    def _ensure_login_page(self, page: Page) -> None:
        page.goto(self.login_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        logger.info("BOSS直聘首页已打开，请手动点击登录/注册并完成人工登录：{}", page.url)

    def _open_login_session(self, headless: bool = False):
        login_profile = get_settings().browser_state_dir / "boss_cdp_login_tmp"
        return open_cdp_browser_session(user_data_dir=login_profile)

    def _open_authenticated_session(self, target_url: str | None = None, headless: bool = False):
        url = target_url or self.home_url
        session = self._open_stateful_session(headless=headless)
        try:
            session.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            session.page.wait_for_timeout(5000)
            if self._is_authenticated_page(session.page):
                logger.info("BOSS直聘 CDP 自动登录成功，当前页面={}", session.page.url)
                return session
            # Profile 中无有效登录态，尝试从 JSON state 注入 cookie
            if self.state_path.exists():
                logger.info("CDP profile 登录态无效，尝试从 JSON 注入 cookie")
                try:
                    state = json.loads(self.state_path.read_text(encoding="utf-8"))
                    cookies = state.get("cookies", [])
                    if cookies:
                        session.context.add_cookies(cookies)
                        session.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        session.page.wait_for_timeout(5000)
                        if self._is_authenticated_page(session.page):
                            logger.info("BOSS直聘 Cookie 注入后登录成功，当前页面={}", session.page.url)
                            return session
                except Exception as exc:
                    logger.warning("BOSS直聘 Cookie 注入失败: {}", exc)
            raise RuntimeError(f"BOSS直聘自动登录未成功：请重新保存登录态。当前页面={session.page.url}")
        except Exception:
            session.close()
            raise

    def import_cookies_from_json(self, cookies_text: str) -> Path:
        try:
            raw = json.loads(cookies_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Cookie JSON 解析失败，请粘贴 Cookie-Editor 导出的 JSON 数组。") from exc
        if isinstance(raw, dict):
            cookies = raw.get("cookies") or raw.get("data") or []
        else:
            cookies = raw
        if not isinstance(cookies, list) or not cookies:
            raise RuntimeError("Cookie JSON 中未找到有效 Cookie 列表。")

        normalized = []
        for item in cookies:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "")
            if not name:
                continue
            domain = str(item.get("domain") or ".zhipin.com").strip() or ".zhipin.com"
            if "zhipin.com" not in domain:
                continue
            same_site = item.get("sameSite") or item.get("same_site") or "Lax"
            same_site_map = {"no_restriction": "None", "unspecified": "Lax", "lax": "Lax", "strict": "Strict", "none": "None"}
            same_site = same_site_map.get(str(same_site).lower(), same_site)
            cookie = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": str(item.get("path") or "/"),
                "httpOnly": bool(item.get("httpOnly", item.get("http_only", False))),
                "secure": bool(item.get("secure", True)),
                "sameSite": same_site,
            }
            expires = item.get("expirationDate", item.get("expires", item.get("expiry")))
            if expires not in {None, -1, "-1", ""}:
                try:
                    cookie["expires"] = float(expires)
                except (TypeError, ValueError):
                    pass
            normalized.append(cookie)
        if not normalized:
            raise RuntimeError("未找到 zhipin.com 域名下的 Cookie。")

        state = {
            "cookies": normalized,
            "origins": [
                {"origin": "https://www.zhipin.com", "localStorage": []},
                {"origin": "https://www.zhipin.com:443", "localStorage": []},
            ],
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("BOSS直聘 Cookie 登录态已导入：{}，Cookie 数={}", self.state_path, len(normalized))
        return self.state_path

    def diagnose_login_navigation(self, wait_seconds: int = 60) -> dict:
        session = self._open_login_session(headless=False)
        events: list[dict] = []
        closed_reason = ""
        try:
            page = session.page

            def record(event: str, error: str = "") -> None:
                try:
                    state = self._page_state_summary(page)
                except Exception as exc:
                    state = {
                        "url": "closed",
                        "title": "",
                        "text_length": 0,
                        "is_blank": False,
                        "is_login_page": False,
                        "is_authenticated": False,
                    }
                    error = error or str(exc)
                events.append({"event": event, **state, "error": error, "at": datetime.now().strftime("%H:%M:%S")})

            page.on("framenavigated", lambda frame: record("navigate") if frame == page.main_frame else None)
            page.on("close", lambda: record("page_closed", "页面已关闭"))
            record("start")
            try:
                page.goto(self.login_url, wait_until="domcontentloaded", timeout=30000)
                record("opened")
            except PlaywrightError as exc:
                record("open_failed", str(exc))
                closed_reason = str(exc)

            deadline = time.monotonic() + wait_seconds
            while not closed_reason and time.monotonic() < deadline:
                try:
                    if page.is_closed():
                        closed_reason = "页面已关闭"
                        record("page_closed", closed_reason)
                        break
                    page.wait_for_timeout(2000)
                    record("poll")
                except PlaywrightError as exc:
                    closed_reason = str(exc)
                    record("interrupted", closed_reason)
                    break
            final = events[-1] if events else {"url": "", "title": "", "text_length": 0, "is_blank": False, "is_login_page": False, "is_authenticated": False}
            return {
                "events": events[-80:],
                "final": final,
                "wait_seconds": wait_seconds,
                "closed": bool(closed_reason),
                "closed_reason": closed_reason,
            }
        finally:
            try:
                session.close()
            except Exception:
                pass

    def login_by_manual_takeover(self, wait_seconds: int = 900) -> Path:
        session = self._open_login_session(headless=False)
        try:
            page = session.page
            self._ensure_login_page(page)
            logger.info("BOSS直聘当前页面接管模式：请手动登录，并进入沟通页面。")
            deadline = time.monotonic() + wait_seconds
            last_log_at = 0.0
            while time.monotonic() < deadline:
                page.wait_for_timeout(2000)
                if self._is_authenticated_page(page):
                    save_storage_state(session.context, self.state_path)
                    logger.info("BOSS直聘当前页面接管登录态已保存：{}", page.url)
                    return self.state_path
                now = time.monotonic()
                if now - last_log_at >= 15:
                    last_log_at = now
                    logger.info("等待 BOSS直聘 当前页面接管登录完成，当前页面：{}", page.url)
            raise RuntimeError("BOSS直聘当前页面接管登录未完成：未保存无效登录态。")
        finally:
            session.close()

    def login(self) -> None:
        self.login_manually(wait_seconds=180)

    def login_manually(self, wait_seconds: int = 180, keep_open: bool = False, enter_home: bool = True) -> Path:
        session = self._open_login_session(headless=False)
        try:
            page = session.page
            self._ensure_login_page(page)
            logger.info("请在打开的 BOSS直聘 登录页面完成人工登录。")
            deadline = time.monotonic() + wait_seconds
            logged_in = False
            last_log_at = 0.0
            while time.monotonic() < deadline:
                try:
                    page.wait_for_timeout(2000)
                    if self._is_authenticated_page(page):
                        logged_in = True
                        save_storage_state(session.context, self.state_path)
                        logger.info("检测到 BOSS直聘 登录完成：{}", page.url)
                        break
                    now = time.monotonic()
                    if now - last_log_at >= 15:
                        last_log_at = now
                        logger.info("等待 BOSS直聘 人工登录完成，当前页面：{}", page.url)
                except PlaywrightError as exc:
                    raise RuntimeError("登录窗口已关闭或登录流程已取消") from exc
            if not logged_in:
                raise RuntimeError("BOSS直聘登录未完成：未保存无效登录态。")
            if enter_home:
                page.goto(self.home_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(5000)
                if not self._is_authenticated_page(page):
                    raise RuntimeError("进入 BOSS直聘 沟通页失败，未覆盖已保存登录态。")
            save_storage_state(session.context, self.state_path)
            if keep_open:
                input("登录态已保存。浏览器将保持打开，按 Enter 后关闭窗口...")
            return self.state_path
        finally:
            session.close()

    def is_logged_in(self, headless: bool = True) -> bool:
        if not self.state_path.exists() and not self._has_persistent_profile():
            return False
        session = self._open_stateful_session(headless=headless, force_profile=True)
        try:
            page = session.page
            page.goto(self.home_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            return self._is_authenticated_page(page)
        except Exception as exc:
            logger.warning("BOSS直聘登录态检测失败：{}", exc)
            return False
        finally:
            session.close()

    def fetch_resume_list(self) -> list[dict]:
        return []

    def fetch_resume_detail(self, resume_id: str) -> dict:
        return {"resume_id": resume_id}

    def save_downloaded_resume(self, download_info: dict) -> dict:
        """Save a resume file downloaded by the Chrome extension.

        Args:
            download_info: {filename, file_path, candidate_signature, candidate_info}
        """
        settings = get_settings()
        now = datetime.now()
        candidate_info = download_info.get("candidate_info", {})
        source_path = Path(download_info.get("file_path", ""))

        if source_path.exists():
            content = source_path.read_bytes()
            file_hash = sha256(content).hexdigest()
        else:
            file_hash = text_hash(download_info.get("candidate_signature", "")) or ""

        suffix = source_path.suffix.lower() if source_path.suffix else ".pdf"
        target_dir = settings.attachment_dir / self.platform_code / now.strftime("%Y%m%d")
        target_dir.mkdir(parents=True, exist_ok=True)

        row = {
            "platform_code": self.platform_code,
            "source_url": download_info.get("source_url", "https://www.zhipin.com/web/chat/index"),
            "raw_json": {
                "title": download_info.get("candidate_signature", ""),
                "url": download_info.get("source_url", ""),
                "capture_mode": "extension_download",
                "candidate_signature": download_info.get("candidate_signature", ""),
                "candidate_info": candidate_info,
                "pre_download_candidate_info": candidate_info,
                "attachment": {
                    "file_name": source_path.name if source_path.exists() else download_info.get("filename", ""),
                    "file_path": str(source_path),
                    "file_ext": suffix,
                    "file_hash": file_hash,
                },
            },
            "raw_html_path": None,
            "content_hash": file_hash,
        }

        if source_path.exists():
            self._rename_attachment_for_candidate(row, candidate_info, 1)

        return row

    def capture_current_page(self, target_url: str | None = None, wait_seconds: int = 30) -> dict:
        if not self.state_path.exists() and not self._has_persistent_profile():
            raise RuntimeError("BOSS直聘登录态不存在，请先完成登录。")

        session = self._open_stateful_session(headless=False)
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

    def _build_attachment_path(self, suggested_filename: str | None, source_url: str | None = None) -> Path:
        settings = get_settings()
        now = datetime.now()
        original_name = suggested_filename or "resume.pdf"
        suffix = Path(original_name).suffix or ".pdf"
        stem = safe_filename(Path(original_name).stem or "resume", max_length=60)
        url_hash = text_hash(source_url or "")[:10] if text_hash(source_url or "") else "unknown"
        filename = f"boss_{now.strftime('%Y%m%d_%H%M%S')}_{stem}_{url_hash}{suffix}"
        path = settings.attachment_dir / self.platform_code / now.strftime("%Y%m%d") / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _normalize_resume_filename_part(self, value: str | None, fallback: str) -> str:
        text = re.sub(r"\s+", "", str(value or "")).strip("-—_｜|/\\:：,，;；.。()（）[]【】")
        if not text or text == "待识别":
            text = fallback
        return safe_filename(text, max_length=24)

    def _rename_attachment_for_candidate(self, row: dict, candidate_info: dict, sequence: int) -> None:
        attachment = row.setdefault("raw_json", {}).setdefault("attachment", {})
        file_path = attachment.get("file_path")
        if not file_path:
            return
        source_path = Path(file_path)
        if not source_path.exists():
            return
        now = datetime.now()
        name = self._normalize_resume_filename_part(candidate_info.get("name"), "未知姓名")
        age = self._normalize_resume_filename_part(candidate_info.get("age"), "未知年龄")
        education = self._normalize_resume_filename_part(candidate_info.get("education") or candidate_info.get("highest_degree"), "未知学历")
        suffix = source_path.suffix.lower() or str(attachment.get("file_ext") or ".pdf")
        filename_stem = f"{name}-{age}-{education}-BOSS直聘-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-{sequence:03d}"
        target_path = source_path.with_name(f"{filename_stem}{suffix}")
        duplicate_index = 1
        while target_path.exists() and target_path != source_path:
            target_path = source_path.with_name(f"{filename_stem}-{duplicate_index}{suffix}")
            duplicate_index += 1
        if target_path != source_path:
            source_path.replace(target_path)
        attachment["file_name"] = target_path.name
        attachment["file_path"] = str(target_path)
        attachment["file_ext"] = target_path.suffix.lower()

    def _save_browser_download(self, download: Download, page: Page | None, mode: str = "boss_browser_download") -> dict:
        download_url = download.url or ""
        suggested_filename = download.suggested_filename or "resume.pdf"
        target_path = self._build_attachment_path(suggested_filename, source_url=download_url)
        download.save_as(str(target_path))
        content = target_path.read_bytes()
        file_hash = sha256(content).hexdigest()
        suffix = target_path.suffix.lower() or ".pdf"
        page_url = ""
        title = suggested_filename
        if page and not page.is_closed():
            try:
                page_url = page.url
                title = page.title() or suggested_filename
            except Exception:
                pass
        return {
            "platform_code": self.platform_code,
            "source_url": download_url or page_url,
            "raw_json": {
                "title": title,
                "url": download_url or page_url,
                "capture_mode": mode,
                "attachment": {
                    "file_name": target_path.name,
                    "file_path": str(target_path),
                    "file_ext": suffix,
                    "mime_type": "application/pdf" if suffix == ".pdf" else None,
                    "file_size": target_path.stat().st_size,
                    "file_hash": file_hash,
                    "suggested_filename": suggested_filename,
                    "download_url": download_url,
                },
            },
            "raw_html_path": None,
            "content_hash": file_hash,
        }

    def _click_text(self, page: Page, texts: list[str], timeout: int = 5000) -> bool:
        selectors = []
        for text in texts:
            selectors.extend([
                f"text={text}",
                f"button:has-text('{text}')",
                f"a:has-text('{text}')",
                f"li:has-text('{text}')",
                f"div:has-text('{text}')",
            ])
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() and locator.is_visible(timeout=800):
                    locator.click(timeout=timeout)
                    page.wait_for_timeout(800)
                    return True
            except Exception:
                continue
        return False

    def _prepare_chat_page(self, page: Page, diag: Callable[[str, str, str, float | None, str], None]) -> None:
        started = time.monotonic()
        page.goto(self.home_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        diag("collect", "open_chat", "ok", (time.monotonic() - started) * 1000, page.url)
        self._assert_authenticated_chat_page(page, "打开沟通页后")
        started = time.monotonic()
        self._click_text(page, ["沟通"], timeout=5000)
        diag("menu", "select_left_chat", "ok", (time.monotonic() - started) * 1000, "沟通")
        self._assert_authenticated_chat_page(page, "选择左侧沟通后")
        started = time.monotonic()
        self._click_text(page, ["沟通中"], timeout=5000)
        diag("menu", "select_top_active", "ok", (time.monotonic() - started) * 1000, "沟通中")
        self._assert_authenticated_chat_page(page, "选择沟通中后")

    def _candidate_items(self, page: Page):
        selectors = [
            ".chat-list li",
            ".chat-list .item",
            ".user-list li",
            ".friend-list li",
            "[class*='chat'] [class*='item']",
            "[class*='user'] [class*='item']",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = locator.count()
                if count:
                    return locator, count, selector
            except Exception:
                continue
        return page.locator("body"), 0, ""

    def _extract_contact_info(self, page: Page) -> dict:
        script = r"""
            () => {
                const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
                const candidates = Array.from(document.querySelectorAll('[class*=chat] [class*=header], [class*=user] [class*=info], [class*=card], header, .name-box, .base-info'));
                let text = '';
                for (const el of candidates) {
                    const value = textOf(el);
                    if (value && value.length >= 2 && value.length <= 300 && /岁|本科|大专|硕士|博士|年|经验|先生|女士/.test(value)) {
                        text = value;
                        break;
                    }
                }
                if (!text) text = textOf(document.body).slice(0, 300);
                return text;
            }
        """
        try:
            text = page.evaluate(script)
        except Exception:
            text = ""
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        name = "待识别"
        age = "待识别"
        education = "待识别"
        name_match = re.search(r"([\u4e00-\u9fa5]{2,4})(?:先生|女士)?", text)
        if name_match:
            name = name_match.group(1)
        age_match = re.search(r"(\d{2})\s*岁", text)
        if age_match:
            age = f"{age_match.group(1)}岁"
        degree_match = re.search(r"博士|硕士|研究生|本科|大专|专科|高中|中专", text)
        if degree_match:
            education = "硕士" if degree_match.group(0) == "研究生" else ("大专" if degree_match.group(0) == "专科" else degree_match.group(0))
        return {"name": name, "age": age, "education": education, "raw_text": text[:240]}

    def _candidate_signature(self, info: dict) -> str:
        return "/".join([str(info.get("name") or "待识别"), str(info.get("age") or "待识别"), str(info.get("education") or "待识别")])

    def _find_resume_button_state(self, page: Page) -> tuple[bool, str]:
        script = r"""
            () => {
                const nodes = Array.from(document.querySelectorAll('button,a,div,span'));
                const list = nodes.filter(el => ((el.innerText || el.textContent || '').trim().includes('附件简历') || (el.getAttribute('title') || '').includes('附件简历')));
                for (const el of list) {
                    const style = getComputedStyle(el);
                    const cls = el.className ? String(el.className) : '';
                    const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true' || cls.includes('disabled') || style.pointerEvents === 'none' || parseFloat(style.opacity || '1') < 0.55;
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) return { found: true, enabled: !disabled, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, text: (el.innerText || el.textContent || '附件简历').trim() };
                }
                return { found: false, enabled: false, text: '' };
            }
        """
        try:
            result = page.evaluate(script)
        except Exception:
            result = {"found": False, "enabled": False}
        if not result.get("found"):
            return False, "not_found"
        try:
            page.mouse.click(float(result["x"]), float(result["y"]), delay=40)
            page.wait_for_timeout(800)
        except Exception:
            return False, "click_failed"
        return bool(result.get("enabled")), "enabled" if result.get("enabled") else "disabled"

    def _confirm_request_resume(self, page: Page) -> bool:
        try:
            if "确定向牛人索取简历吗" in page.locator("body").inner_text(timeout=1500):
                return self._click_text(page, ["确定", "确认"], timeout=3000)
        except Exception:
            pass
        return False

    def _click_resume_download_button(self, page: Page) -> bool:
        script = r"""
            () => {
                const nodes = Array.from(document.querySelectorAll('button,a,div,span,i,svg'));
                const keywords = ['下载', 'download', 'Download'];
                const scored = [];
                for (const el of nodes) {
                    const text = `${el.innerText || ''} ${el.textContent || ''} ${el.getAttribute('title') || ''} ${el.getAttribute('aria-label') || ''} ${el.className || ''}`;
                    if (!keywords.some(k => text.includes(k))) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    scored.push({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, score: rect.top * 10 - rect.left });
                }
                scored.sort((a, b) => a.score - b.score);
                return scored[0] || null;
            }
        """
        try:
            point = page.evaluate(script)
            if point:
                page.mouse.click(float(point["x"]), float(point["y"]), delay=40)
                return True
        except Exception:
            pass
        return self._click_text(page, ["下载"], timeout=3000)

    def auto_click_chat_attachment_resumes(
        self,
        target_url: str | None = None,
        max_resumes: int = 5,
        wait_seconds: int = 900,
        per_candidate_wait_seconds: int = 60,
        min_download_interval_seconds: int = 5,
        on_resume_saved: Callable[[dict], None] | None = None,
        should_skip_candidate_profile: Callable[[dict, str], bool] | None = None,
        on_resume_skipped: Callable[[dict], None] | None = None,
        should_continue: Callable[[], bool] | None = None,
        on_diagnostic: Callable[[str], None] | None = None,
        on_download_failed: Callable[[dict], None] | None = None,
    ) -> list[dict]:
        session = self._open_authenticated_session(target_url or self.home_url, headless=False)
        results: list[dict] = []
        started_at = time.monotonic()
        seen_signatures: set[str] = set()

        def can_continue() -> bool:
            return should_continue() if should_continue else True

        def diag_event(stage: str, action: str = "", status: str = "", cost_ms: float | None = None, candidate: str = "", **fields) -> None:
            detail = " | ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
            line = f"STEP {stage}.{action} | status={status} | cost={int(cost_ms or 0)}ms | candidate={candidate}"
            if detail:
                line = f"{line} | {detail}"
            logger.info(line)
            if on_diagnostic:
                on_diagnostic(line)

        try:
            page = session.page
            self._prepare_chat_page(page, diag_event)
            if self._is_login_page(page):
                raise RuntimeError("BOSS直聘自动登录未成功：当前仍停留在登录页面，请重新保存登录态。")
            cursor = 0
            while len(results) < max_resumes and time.monotonic() - started_at < wait_seconds and can_continue():
                items, count, selector = self._candidate_items(page)
                if not count or cursor >= count:
                    self._assert_authenticated_chat_page(page, "扫描候选人列表时")
                    diag_event("candidate", "scan", "exhausted", selector=selector, count=count)
                    break
                candidate_started = time.monotonic()
                try:
                    item = items.nth(cursor)
                    cursor += 1
                    item.scroll_into_view_if_needed(timeout=3000)
                    item.click(timeout=5000)
                    page.wait_for_timeout(1200)
                    info = self._extract_contact_info(page)
                    signature = self._candidate_signature(info)
                    if signature in seen_signatures:
                        diag_event("candidate", "skip", "skipped", candidate=signature, reason="duplicate_in_session")
                        continue
                    seen_signatures.add(signature)
                    diag_event("candidate", "click", "ok", (time.monotonic() - candidate_started) * 1000, signature, index=cursor)

                    if should_skip_candidate_profile and should_skip_candidate_profile(info, signature):
                        row = {
                            "platform_code": self.platform_code,
                            "source_url": page.url,
                            "raw_json": {
                                "candidate_signature": signature,
                                "candidate_info": info,
                                "pre_download_candidate_info": info,
                                "skip_stage": "before_download_profile",
                            },
                            "raw_html_path": None,
                            "content_hash": text_hash(signature) or "",
                        }
                        if on_resume_skipped:
                            on_resume_skipped(row)
                        diag_event("candidate", "summary", "skipped", (time.monotonic() - candidate_started) * 1000, signature, reason="before_download_profile")
                        continue

                    enabled, button_state = self._find_resume_button_state(page)
                    diag_event("attachment", "button", button_state, candidate=signature)
                    if not enabled:
                        requested = self._confirm_request_resume(page)
                        row = {
                            "platform_code": self.platform_code,
                            "source_url": page.url,
                            "raw_json": {
                                "candidate_signature": signature,
                                "candidate_info": info,
                                "pre_download_candidate_info": info,
                                "skip_stage": "request_attachment_disabled" if requested else "attachment_button_disabled",
                            },
                            "raw_html_path": None,
                            "content_hash": text_hash(signature) or "",
                        }
                        if on_resume_skipped:
                            on_resume_skipped(row)
                        diag_event("candidate", "summary", "skipped", (time.monotonic() - candidate_started) * 1000, signature, requested=requested)
                        continue

                    active_pages = [p for p in session.context.pages if not p.is_closed()]
                    resume_page = active_pages[-1] if active_pages else page
                    page.wait_for_timeout(1500)
                    current_pages = [p for p in session.context.pages if not p.is_closed()]
                    if len(current_pages) > len(active_pages):
                        resume_page = current_pages[-1]
                    try:
                        resume_page.bring_to_front()
                    except Exception:
                        pass
                    download_started = time.monotonic()
                    try:
                        with resume_page.expect_download(timeout=per_candidate_wait_seconds * 1000) as download_info:
                            if not self._click_resume_download_button(resume_page):
                                raise RuntimeError("download_button_not_found")
                        download = download_info.value
                        row = self._save_browser_download(download, resume_page)
                    except Exception as exc:
                        if on_download_failed:
                            on_download_failed({"candidate_signature": signature, "error": str(exc), "url_hash": "空"})
                        diag_event("attachment", "download", "failed", (time.monotonic() - download_started) * 1000, signature, reason=exc)
                        continue

                    row["raw_json"]["candidate_signature"] = signature
                    row["raw_json"]["candidate_info"] = info
                    row["raw_json"]["pre_download_candidate_info"] = info
                    self._rename_attachment_for_candidate(row, info, len(results) + 1)
                    results.append(row)
                    if on_resume_saved:
                        on_resume_saved(row)
                    diag_event("attachment", "save", "success", (time.monotonic() - download_started) * 1000, signature, file=row.get("raw_json", {}).get("attachment", {}).get("file_name"))
                    diag_event("candidate", "summary", "success", (time.monotonic() - candidate_started) * 1000, signature)
                    time.sleep(max(min_download_interval_seconds, 0))
                except Exception as exc:
                    diag_event("candidate", "summary", "failed", (time.monotonic() - candidate_started) * 1000, reason=exc)
                    continue
            return results
        finally:
            try:
                save_storage_state(session.context, self.state_path)
            except Exception:
                pass
            session.close()
