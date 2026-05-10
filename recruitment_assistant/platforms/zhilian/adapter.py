import re
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse



from loguru import logger
from playwright.sync_api import BrowserContext, Download, Error as PlaywrightError, Page, Request, Route, TimeoutError as PlaywrightTimeoutError



from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.core.browser import get_state_path, open_browser_session, save_storage_state
from recruitment_assistant.parsers.pdf_resume_parser import clean_candidate_signature, parse_resume_file
from recruitment_assistant.platforms.base import BasePlatformAdapter
from recruitment_assistant.utils.hash_utils import text_hash
from recruitment_assistant.utils.snapshot_utils import safe_filename, save_text_snapshot



class ZhilianAdapter(BasePlatformAdapter):
    platform_code = "zhilian"
    login_url = "https://passport.zhaopin.com/login"
    home_url = "https://rd5.zhaopin.com/"

    def __init__(self, account_name: str = "default") -> None:
        self.account_name = account_name
        self.state_path = get_state_path(self.platform_code, account_name)

    def login(self) -> None:
        self.login_manually(wait_seconds=180)

    def login_manually(self, wait_seconds: int = 180, keep_open: bool = False, enter_home: bool = True) -> Path:
        session = open_browser_session(headless=False)
        try:
            page = session.page
            page.goto(self.login_url, wait_until="domcontentloaded")
            logger.info("请在打开的浏览器中完成人工扫码/短信登录。")
            try:
                page.wait_for_url(lambda url: "login" not in url.lower(), timeout=wait_seconds * 1000)
                page.wait_for_timeout(3000)
            except PlaywrightTimeoutError:
                logger.warning("等待登录跳转超时，将尝试进入智联系统首页后保存当前登录态。")
            except PlaywrightError as exc:
                raise RuntimeError("登录窗口已关闭或登录流程已取消") from exc
            if enter_home:
                try:
                    page.goto(self.home_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(5000)
                    if "login" in page.url.lower() or "passport" in page.url.lower():
                        logger.warning("进入系统首页后仍在登录页，请确认是否已完成登录。")
                    else:
                        logger.info("已进入智联系统首页：{}", page.url)
                except Exception as exc:
                    logger.warning("登录后进入智联系统首页失败：{}", exc)
            save_storage_state(session.context, self.state_path)
            logger.info("智联招聘登录态已保存：{}", self.state_path)
            if keep_open:
                input("登录态已保存。浏览器将保持打开，按 Enter 后关闭窗口...")
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
            print("\n操作说明：")
            print("1. 在浏览器中手动打开候选人/简历详情页。")
            print("2. 如果智联打开了新标签页，脚本会优先采集最新标签页。")
            print("3. 终端直接按 Enter：保存当前最新标签页快照。")
            print("4. 输入 l：列出所有标签页；输入数字：选择指定标签页保存。")
            print("5. 输入 r：刷新最新标签页；输入 g 网址：跳转最新标签页；输入 q：结束。\n")

            index = 0
            while index < max_pages:
                command = input(
                    f"请打开第 {index + 1} 个候选人/简历页面后按 Enter 保存，或输入 l/r/g 网址/页码/q："
                ).strip()
                pages = [item for item in session.context.pages if not item.is_closed()]
                if not pages:
                    page = session.context.new_page()
                    pages = [page]

                if command.lower() == "q":
                    break
                if command.lower() == "l":
                    for page_index, item in enumerate(pages, start=1):
                        title = ""
                        try:
                            title = item.title()
                        except Exception:
                            title = "<无法读取标题>"
                        print(f"{page_index}. {title} | {item.url}")
                    continue

                active_page = pages[-1]
                if command.lower() == "r":
                    active_page.reload(wait_until="domcontentloaded", timeout=30000)
                    active_page.wait_for_timeout(3000)
                    print(f"已刷新最新标签页：{active_page.url}")
                    continue
                if command.lower().startswith("g "):
                    url = command[2:].strip()
                    if not url.startswith(("http://", "https://")):
                        url = f"https://{url}"
                    active_page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    active_page.wait_for_timeout(3000)
                    print(f"已跳转最新标签页：{active_page.url}")
                    continue
                if command.isdigit():
                    selected_index = int(command) - 1
                    if selected_index < 0 or selected_index >= len(pages):
                        print("页码无效，请先输入 l 查看标签页列表。")
                        continue
                    active_page = pages[selected_index]

                try:
                    active_page.wait_for_load_state("domcontentloaded", timeout=10000)
                except PlaywrightTimeoutError:
                    logger.warning("等待页面加载超时，将继续保存当前页面内容。")
                active_page.wait_for_timeout(1000)
                active_page.bring_to_front()

                html = active_page.content()
                title = active_page.title()
                snapshot_path = save_text_snapshot(self.platform_code, active_page.url, html)
                results.append(
                    {
                        "platform_code": self.platform_code,
                        "source_url": active_page.url,
                        "raw_json": {
                            "title": title,
                            "url": active_page.url,
                            "capture_mode": "manual_resume_page",
                            "manual_index": index + 1,
                        },
                        "raw_html_path": str(snapshot_path),
                        "content_hash": text_hash(html) or "",
                    }
                )
                index += 1
                print(f"已保存第 {index} 页：{title} | {active_page.url} | {snapshot_path}")
            return results
        finally:
            session.close()


    def _build_attachment_path(self, suggested_filename: str | None, page: Page | None = None, source_url: str | None = None) -> Path:
        settings = get_settings()
        now = datetime.now()
        original_name = suggested_filename or "resume.pdf"
        suffix = Path(original_name).suffix or ".pdf"
        stem = safe_filename(Path(original_name).stem or "resume", max_length=60)
        url = source_url or (page.url if page else "")
        url_hash = text_hash(url)[:10] if text_hash(url) else "unknown"
        filename = f"zhilian_{now.strftime('%Y%m%d_%H%M%S')}_{stem}_{url_hash}{suffix}"
        path = settings.attachment_dir / self.platform_code / now.strftime("%Y%m%d") / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


    def _save_download(self, download: Download, page: Page, manual_index: int) -> dict:
        target_path = self._build_attachment_path(download.suggested_filename, page)
        download.save_as(str(target_path))
        content = target_path.read_bytes()
        file_hash = sha256(content).hexdigest()
        return {
            "platform_code": self.platform_code,
            "source_url": page.url,
            "raw_json": {
                "title": page.title(),
                "url": page.url,
                "capture_mode": "chat_attachment_pdf_download",
                "manual_index": manual_index,
                "attachment": {
                    "file_name": target_path.name,
                    "file_path": str(target_path),
                    "file_ext": target_path.suffix.lower(),
                    "mime_type": "application/pdf" if target_path.suffix.lower() == ".pdf" else None,
                    "file_size": target_path.stat().st_size,
                    "file_hash": file_hash,
                    "suggested_filename": download.suggested_filename,
                },
            },
            "raw_html_path": None,
            "content_hash": file_hash,
        }

    def _save_browser_download(self, download: Download, page: Page | None, mode: str = "browser_download_event") -> dict:
        download_url = download.url or ""
        suggested_filename = download.suggested_filename or self._filename_from_download_url(download_url)
        target_path = self._build_attachment_path(suggested_filename, page, source_url=download_url)
        download.save_as(str(target_path))
        content = target_path.read_bytes()
        suffix = self._detect_attachment_suffix(content, "", suggested_filename)
        if not self._is_supported_resume_file(content, suffix):
            preview = content[:80].hex()
            try:
                target_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise RuntimeError(
                f"浏览器下载文件格式无效或下载不完整：suffix={suffix}, "
                f"size={len(content)}, head={preview}, url_hash={text_hash(download_url)[:10] if text_hash(download_url) else '空'}"
            )
        if target_path.suffix.lower() != suffix:
            renamed_path = target_path.with_suffix(suffix)
            if renamed_path != target_path:
                target_path.replace(renamed_path)
                target_path = renamed_path
        file_hash = sha256(content).hexdigest()
        title = ""
        page_url = ""
        if page and not page.is_closed():
            try:
                title = page.title()
            except Exception:
                title = suggested_filename
            try:
                page_url = page.url
            except Exception:
                page_url = ""
        return {
            "platform_code": self.platform_code,
            "source_url": download_url or page_url,
            "raw_json": {
                "title": title or suggested_filename,
                "url": download_url or page_url,
                "capture_mode": mode,
                "attachment": {
                    "file_name": target_path.name,
                    "file_path": str(target_path),
                    "file_ext": suffix,
                    "mime_type": "application/pdf" if suffix == ".pdf" else None,
                    "file_size": target_path.stat().st_size,
                    "file_hash": file_hash,
                    "suggested_filename": download.suggested_filename,
                    "download_url": download_url,
                },
            },
            "raw_html_path": None,
            "content_hash": file_hash,
        }

    def _wait_for_any_download(self, pages: list[Page], wait_seconds: int) -> tuple[Download, Page]:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            active_pages = [item for item in pages if not item.is_closed()]
            for page in active_pages:
                try:
                    with page.expect_download(timeout=1000) as download_info:
                        pass
                    return download_info.value, page
                except PlaywrightTimeoutError:
                    continue
            time.sleep(0.2)
        raise PlaywrightTimeoutError("等待 PDF 下载超时")

    def download_manual_chat_attachment_resumes(
        self, target_url: str | None = None, max_resumes: int = 5, wait_seconds: int = 180
    ) -> list[dict]:
        if not self.state_path.exists():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = open_browser_session(state_path=self.state_path, headless=False)
        results = []
        try:
            page = session.page
            page.goto(target_url or self.home_url, wait_until="domcontentloaded", timeout=30000)
            print("\n智联聊天附件简历 PDF 采集流程：")
            print("1. 在浏览器侧边栏进入'聊天'。")
            print("2. 选择候选人。")
            print("3. 点击'要附件简历'。")
            print("4. 候选人已提供附件后，点击'查看附件简历'。")
            print("5. 在新页面触发 PDF 下载，程序会自动保存文件。")
            print("6. 每保存一份后回到终端继续下一份；输入 q 可结束。\n")

            while len(results) < max_resumes:
                command = input(
                    f"准备采集第 {len(results) + 1} 份附件简历。按 Enter 开始监听下载，输入 q 结束："
                ).strip()
                if command.lower() == "q":
                    break

                pages = [item for item in session.context.pages if not item.is_closed()]
                if pages:
                    pages[-1].bring_to_front()
                print(f"请在 {wait_seconds} 秒内点击'查看附件简历'并触发 PDF 下载...")
                try:
                    download, download_page = self._wait_for_any_download(pages, wait_seconds)
                except PlaywrightTimeoutError:
                    print("未检测到下载。请确认浏览器是否出现 PDF 下载，或先在新页面点击下载按钮。")
                    continue

                row = self._save_download(download, download_page, len(results) + 1)
                results.append(row)
                attachment = row["raw_json"]["attachment"]
                print(f"已保存第 {len(results)} 份：{attachment['file_path']}")
            return results
        finally:
            session.close()





    def _filename_from_download_url(self, download_url: str) -> str:
        query = parse_qs(urlparse(download_url).query)
        raw_name = (query.get("fileName") or query.get("filename") or query.get("name") or [""])[0]
        if raw_name:
            decoded_name = unquote(raw_name)
            safe_name = safe_filename(Path(decoded_name).stem or decoded_name, max_length=80)
            suffix = Path(decoded_name).suffix.lower()
            if suffix in {".pdf", ".doc", ".docx"}:
                return f"{safe_name}{suffix}"
            return f"{safe_name}.pdf"
        return "resume.pdf"

    def _detect_attachment_suffix(self, content: bytes, content_type: str, suggested_filename: str) -> str:
        suffix = Path(suggested_filename).suffix.lower()
        if content.startswith(b"%PDF"):
            return ".pdf"
        if content.startswith(b"PK\x03\x04"):
            return ".docx"
        if content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            return ".doc"
        if suffix in {".pdf", ".doc", ".docx"}:
            return suffix
        if "pdf" in content_type.lower():
            return ".pdf"
        if "word" in content_type.lower() or "msword" in content_type.lower():
            return ".doc"
        return suffix or ".bin"

    def _is_supported_resume_file(self, content: bytes, suffix: str) -> bool:
        if suffix == ".pdf":
            return content.startswith(b"%PDF") and b"%%EOF" in content[-2048:]
        if suffix == ".docx":
            return content.startswith(b"PK\x03\x04")
        if suffix == ".doc":
            return content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
        return False

    def _is_resume_attachment_download_url(self, url: str) -> bool:
        return "attachment.zhaopin.com" in url and "downloadFileTemporary" in url

    def _download_attachment_with_context(
        self,
        context: BrowserContext,
        download_url: str,
        filename: str | None = None,
        mode: str = "attachment_url_download",
        request_headers: dict | None = None,
    ) -> dict:
        headers = {
            "Referer": self.home_url,
            "Accept": "application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/octet-stream,*/*",
        }
        if request_headers:
            for key, value in request_headers.items():
                lower_key = key.lower()
                if lower_key in {"host", "content-length", "connection", "accept-encoding"}:
                    continue
                if value:
                    headers[key] = value
        headers.setdefault("Referer", self.home_url)
        headers["Accept"] = headers.get("Accept") or headers.get("accept") or headers["Accept"]
        response = context.request.get(
            download_url,
            headers=headers,
            timeout=60000,
        )
        if not response.ok:
            preview = ""
            try:
                preview = response.body()[:160].hex()
            except Exception:
                preview = ""
            raise RuntimeError(
                f"附件下载失败：HTTP {response.status} {response.status_text}，"
                f"content-type={response.headers.get('content-type', '')}，head={preview}，"
                f"url_hash={text_hash(download_url)[:10] if text_hash(download_url) else '空'}"
            )

        content = response.body()
        content_type = response.headers.get("content-type", "")
        disposition = response.headers.get("content-disposition", "")
        suggested_filename = filename or self._filename_from_download_url(download_url)
        suffix = self._detect_attachment_suffix(content, content_type, suggested_filename)
        suggested_filename = f"{safe_filename(Path(suggested_filename).stem or 'resume', max_length=80)}{suffix}"
        if not self._is_supported_resume_file(content, suffix):
            preview = content[:80].hex()
            raise RuntimeError(
                f"附件文件格式无效或下载不完整：suffix={suffix}, "
                f"content-type={content_type}, size={len(content)}, head={preview}, "
                f"url_hash={text_hash(download_url)[:10] if text_hash(download_url) else '空'}"
            )
        target_path = self._build_attachment_path(suggested_filename, source_url=download_url)
        target_path.write_bytes(content)
        file_hash = sha256(content).hexdigest()
        return {
            "platform_code": self.platform_code,
            "source_url": download_url,
            "raw_json": {
                "title": suggested_filename,
                "url": download_url,
                "capture_mode": mode,
                "attachment": {
                    "file_name": target_path.name,
                    "file_path": str(target_path),
                    "file_ext": suffix,
                    "mime_type": content_type or None,
                    "file_size": target_path.stat().st_size,
                    "file_hash": file_hash,
                    "content_disposition": disposition,
                },
            },
            "raw_html_path": None,
            "content_hash": file_hash,
        }

    def _find_attachment_urls_from_pages(self, pages: list[Page], captured_urls: set[str], mark_captured: bool = True) -> list[str]:
        urls = []
        script = r"""
            () => {
                const values = [];
                const attrs = ['href', 'src', 'data-url', 'data-href'];
                for (const el of Array.from(document.querySelectorAll('a, iframe, embed, object, [href], [src], [data-url], [data-href]'))) {
                    for (const attr of attrs) {
                        const value = el.getAttribute(attr);
                        if (value) values.push(value);
                    }
                }
                return values;
            }
        """
        for page in pages:
            if page.is_closed():
                continue
            page_urls = [page.url]
            for frame in page.frames:
                try:
                    page_urls.extend(frame.evaluate(script))
                except Exception:
                    continue
            for url in page_urls:
                if not url:
                    continue
                if url.startswith("//"):
                    url = f"https:{url}"
                elif url.startswith("/"):
                    parsed = urlparse(page.url)
                    url = f"{parsed.scheme}://{parsed.netloc}{url}"
                if self._is_resume_attachment_download_url(url) and (not mark_captured or url not in captured_urls):
                    if mark_captured:
                        captured_urls.add(url)
                    urls.append(url)
                    print(f"已从页面/DOM捕获附件下载链接：{url}")
        return urls


    def auto_capture_chat_attachment_resumes(
        self, target_url: str | None = None, max_resumes: int = 5, wait_seconds: int = 600
    ) -> list[dict]:
        if not self.state_path.exists():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = open_browser_session(state_path=self.state_path, headless=False)
        results = []
        captured_urls: set[str] = set()
        pending_urls: list[str] = []

        try:
            page = session.page
            page.goto(target_url or self.home_url, wait_until="domcontentloaded", timeout=30000)
            print("\n智联聊天附件简历自动采集已启动：")
            print("1. 在浏览器侧边栏进入'聊天'。")
            print("2. 选择候选人。")
            print("3. 点击'要附件简历'。")
            print("4. 出现'查看附件简历'后直接点击。")
            print("5. 程序会自动捕获下载链接、保存 PDF、归档入库。")
            print(f"6. 目标数量：{max_resumes}，最长等待：{wait_seconds} 秒。\n")

            deadline = time.monotonic() + wait_seconds
            while len(results) < max_resumes and time.monotonic() < deadline:

                pages = [item for item in session.context.pages if not item.is_closed()]
                pending_urls.extend(self._find_attachment_urls_from_pages(pages, captured_urls))
                while pending_urls and len(results) < max_resumes:
                    download_url = pending_urls.pop(0)
                    try:
                        filename = self._filename_from_download_url(download_url)
                        row = self._download_attachment_with_context(
                            session.context,
                            download_url,
                            filename=filename,
                            mode="auto_attachment_url_capture",
                        )
                    except Exception as exc:
                        logger.warning("自动下载附件失败：{}", exc)
                        continue
                    results.append(row)
                    attachment = row["raw_json"]["attachment"]
                    print(f"已自动保存第 {len(results)} 份：{attachment['file_path']}")
                page.wait_for_timeout(1000)

            if len(results) < max_resumes:
                print(f"监听结束：已保存 {len(results)} 份，未达到目标数量 {max_resumes}。")
            else:
                print(f"监听结束：已保存目标数量 {max_resumes} 份。")
            return results
        finally:
            session.close()

    def download_attachment_by_url(self, download_url: str, filename: str | None = None) -> dict:
        if not self.state_path.exists():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = open_browser_session(state_path=self.state_path, headless=True)
        try:
            return self._download_attachment_with_context(
                session.context,
                download_url,
                filename=filename,
                mode="attachment_url_download",
            )
        finally:
            session.close()



    def _click_text(self, page: Page, texts: list[str], timeout: int = 3000) -> bool:
        for frame in page.frames:
            for text in texts:
                try:
                    locator = frame.get_by_text(text, exact=False).first
                    locator.wait_for(state="visible", timeout=timeout)
                    locator.click(timeout=timeout)
                    logger.info("已点击文本：{}", text)
                    return True
                except Exception:
                    continue
        return False

    def _click_chat_entry_by_dom(self, page: Page) -> bool:
        selectors = [
            'a[href*="chat"], a[href*="im"], a[href*="message"], a[href*="conversation"]',
            '[class*="chat"], [class*="im"], [class*="message"], [class*="conversation"]',
            '[aria-label*="聊天"], [aria-label*="沟通"], [title*="聊天"], [title*="沟通"]',
            'a, button, li, div[role="button"], [class*="menu"], [class*="nav"], [class*="side"]',
        ]
        script = r"""
            (selector) => {
                const keywords = ['聊天', '沟通', '消息', '在线沟通', '候选人沟通', 'chat', 'im', 'message', 'conversation', 'talk'];
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                };
                const read = (el) => [
                    el.innerText,
                    el.textContent,
                    el.getAttribute('aria-label'),
                    el.getAttribute('title'),
                    el.getAttribute('href'),
                    el.getAttribute('class'),
                    el.getAttribute('data-menu'),
                    el.getAttribute('data-testid'),
                    el.getAttribute('data-spm'),
                    el.getAttribute('data-track'),
                ].filter(Boolean).join(' ').toLowerCase();
                const nodes = Array.from(document.querySelectorAll(selector));
                const candidates = nodes
                    .filter((el) => isVisible(el))
                    .map((el) => ({ el, text: read(el), rect: el.getBoundingClientRect() }))
                    .filter((item) => keywords.some((keyword) => item.text.includes(keyword)));
                candidates.sort((a, b) => a.rect.left - b.rect.left || a.rect.top - b.rect.top);
                const item = candidates[0];
                if (!item) return null;
                item.el.scrollIntoView({ block: 'center', inline: 'center' });
                item.el.click();
                return item.text.slice(0, 200);
            }
        """
        for frame in page.frames:
            for selector in selectors:
                try:
                    result = frame.evaluate(script, selector)
                except Exception:
                    continue
                if result:
                    logger.info("已通过 DOM 点击聊天入口：{}", result)
                    return True
        return False

    def _print_navigation_candidates(self, page: Page) -> None:
        script = r"""
            () => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const read = (el) => [
                    el.innerText,
                    el.textContent,
                    el.getAttribute('aria-label'),
                    el.getAttribute('title'),
                    el.getAttribute('href'),
                    el.getAttribute('class'),
                    el.getAttribute('data-menu'),
                    el.getAttribute('data-testid'),
                ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
                return Array.from(document.querySelectorAll('a, button, li, div[role="button"], [class*="menu"], [class*="nav"], [class*="side"], [class*="chat"], [class*="im"], [class*="message"]'))
                    .filter((el) => isVisible(el))
                    .map((el) => {
                        const rect = el.getBoundingClientRect();
                        return { text: read(el).slice(0, 160), x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height) };
                    })
                    .filter((item) => item.text)
                    .slice(0, 50);
            }
        """
        print("未找到聊天入口。当前可见导航/按钮候选：")
        for frame_index, frame in enumerate(page.frames, start=1):
            try:
                candidates = frame.evaluate(script)
            except Exception:
                continue
            if len(page.frames) > 1:
                print(f"Frame {frame_index}: {frame.url}")
            for index, item in enumerate(candidates, start=1):
                print(f"{index}. ({item['x']},{item['y']},{item['w']}x{item['h']}) text={item.get('text', '')} class={item.get('cls', '')} bg={item.get('bg', '')} color={item.get('color', '')}")

    def _click_sidebar_chat_entry(self, page: Page) -> bool:
        script = r"""
            () => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const nodes = Array.from(document.querySelectorAll('a, button, li, div[role="button"], [class*="menu"], [class*="nav"], [class*="side"], div'));
                const candidates = nodes
                    .filter((el) => isVisible(el))
                    .map((el) => ({ el, text: normalize(el.innerText || el.textContent), rect: el.getBoundingClientRect() }))
                    .filter((item) => item.rect.left >= 0 && item.rect.left < 95 && item.rect.top > 60 && item.rect.height >= 24 && item.rect.height <= 80 && /聊天/.test(item.text));
                candidates.sort((a, b) => a.rect.left - b.rect.left || a.rect.width - b.rect.width || a.rect.top - b.rect.top);
                const item = candidates[0];
                if (!item) return null;
                item.el.scrollIntoView({ block: 'center', inline: 'center' });
                item.el.click();
                return item.text.slice(0, 120);
            }
        """
        for frame in page.frames:
            try:
                result = frame.evaluate(script)
            except Exception:
                continue
            if result:
                logger.info("已点击左侧聊天入口：{}", result)
                return True
        try:
            page.mouse.click(46, 146)
            logger.info("已使用坐标兜底点击左侧聊天入口。")
            return True
        except Exception:
            return False

    def _is_probably_chat_page(self, page: Page) -> bool:
        url = page.url.lower()
        if any(marker in url for marker in ["chat", "im", "message", "conversation"]):
            logger.info("当前页面疑似已在聊天界面：{}", page.url)
            return True
        try:
            has_chat_content = page.evaluate(
                r"""
                () => {
                    const text = document.body ? document.body.innerText : '';
                    return /要附件简历|查看附件简历|未联系|未读|在线沟通|候选人沟通|请从左侧列表中选择/.test(text);
                }
                """
            )
        except Exception:
            return False
        if has_chat_content:
            logger.info("当前页面疑似已在聊天界面。")
            return True
        return False

    def _open_chat_interface(self, page: Page) -> bool:
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        if self._is_probably_chat_page(page):
            return True
        if self._click_sidebar_chat_entry(page):
            page.wait_for_timeout(5000)
            if self._is_probably_chat_page(page):
                return True
        if self._click_text(page, ["聊天", "沟通", "消息", "在线沟通", "候选人沟通", "立即沟通"], timeout=3000):
            page.wait_for_timeout(5000)
            if self._is_probably_chat_page(page):
                return True
        if self._click_chat_entry_by_dom(page):
            page.wait_for_timeout(5000)
            if self._is_probably_chat_page(page):
                return True
        self._print_navigation_candidates(page)
        logger.warning("未自动找到聊天入口，请把上方候选列表中聊天入口对应文本发给我，或使用 --url 直接传入聊天页面地址。")
        return False


    def _collect_uncontacted_candidate_targets(
        self,
        page: Page,
        seen_signatures: set[str],
        max_targets: int = 12,
    ) -> list[dict]:
        script = r"""
            ({seen, maxTargets}) => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const skip = (text) => /快速处理|新招呼|99\+人|全部职位|筛选|批量/.test(text);
                const cardAt = (x, y) => {
                    const stack = document.elementsFromPoint(x, y);
                    let best = null;
                    for (const el of stack) {
                        if (!isVisible(el)) continue;
                        const rect = el.getBoundingClientRect();
                        const text = normalize(el.innerText || el.textContent);
                        if (
                            rect.left >= 170 && rect.left <= 440 && rect.top >= 220 &&
                            rect.top <= y && rect.bottom >= y &&
                            rect.width >= 200 && rect.width <= 280 &&
                            rect.height >= 45 && rect.height <= 130 &&
                            text.length >= 2 && text.length <= 260 && !skip(text)
                        ) {
                            if (!best || rect.width * rect.height > best.getBoundingClientRect().width * best.getBoundingClientRect().height) {
                                best = el;
                            }
                        }
                    }
                    return best;
                };
                const redMarkers = Array.from(document.querySelectorAll('*'))
                    .filter((el) => isVisible(el))
                    .map((el) => ({ el, rect: el.getBoundingClientRect(), style: window.getComputedStyle(el), text: normalize(el.innerText || el.textContent) }))
                    .filter((item) => {
                        const rect = item.rect;
                        if (rect.left < 170 || rect.left > 215 || rect.top < 220 || rect.width > 34 || rect.height > 34) return false;
                        const className = String(item.el.className || '').toLowerCase();
                        const color = `${item.style.backgroundColor} ${item.style.color} ${item.style.borderColor}`;
                        return /badge|dot|unread|red|count|notice|num/.test(className) || /rgb\( ?(2[0-5][0-5]|1[5-9][0-9])[, ]+([0-9]{1,3})[, ]+([0-9]{1,3})/.test(color) || /^\d{1,3}$/.test(item.text);
                    })
                    .sort((a, b) => a.rect.top - b.rect.top);
                const targets = [];
                for (const marker of redMarkers) {
                    const y = marker.rect.top + marker.rect.height / 2;
                    const card = cardAt(300, y) || cardAt(260, y) || cardAt(220, y) || cardAt(390, y);
                    if (card) targets.push(card);
                }
                const rows = Array.from(document.querySelectorAll('li, article, section, div[role="listitem"], div[role="button"], [class*="conversation"], [class*="session"], [class*="item"], [class*="card"]'))
                    .filter((el) => isVisible(el))
                    .map((el) => ({ el, rect: el.getBoundingClientRect(), text: normalize(el.innerText || el.textContent) }))
                    .filter((item) =>
                        item.rect.left >= 170 && item.rect.left <= 440 && item.rect.top >= 220 &&
                        item.rect.width >= 200 && item.rect.width <= 280 &&
                        item.rect.height >= 45 && item.rect.height <= 130 &&
                        item.text.length >= 2 && item.text.length <= 260 && !skip(item.text)
                    )
                    .sort((a, b) => a.rect.top - b.rect.top);
                for (const row of rows) {
                    if (!targets.includes(row.el)) targets.push(row.el);
                }
                const output = [];
                const used = new Set();
                const stableIdFrom = (el) => {
                    const values = [];
                    let node = el;
                    for (let depth = 0; node && depth < 4; depth += 1, node = node.parentElement) {
                        values.push(node.getAttribute('href'), node.getAttribute('title'), node.getAttribute('aria-label'), node.id, String(node.className || ''));
                        for (const attr of Array.from(node.attributes || [])) {
                            if (/^(data-|href|id|title|aria-label)/i.test(attr.name) || /(candidate|resume|user|uid|zpid|zp|geek|chat|conversation|session|id)/i.test(attr.name)) {
                                values.push(`${attr.name}=${attr.value}`);
                            }
                        }
                    }
                    const text = values.filter(Boolean).join(' ');
                    const match = text.match(/(?:candidate|resume|user|uid|zpid|zp|geek|chat|conversation|session|id)[_\-=:"']*([A-Za-z0-9_-]{6,})/i);
                    return match ? match[0].slice(0, 120) : '';
                };
                for (const target of targets) {
                    const rect = target.getBoundingClientRect();
                    const signature = normalize(target.innerText || target.textContent).slice(0, 220);
                    const stableId = stableIdFrom(target);
                    const positionKey = `pos:${Math.round(rect.left)}:${Math.round(rect.top)}:${Math.round(rect.width)}:${Math.round(rect.height)}`;
                    const key = `${positionKey}:${signature}:${stableId}`;
                    if (!signature || seen.includes(signature) || used.has(key) || skip(signature)) continue;
                    used.add(key);
                    output.push({
                        signature,
                        stableId,
                        positionKey,
                        x: Math.min(410, Math.max(230, rect.left + rect.width * 0.55)),
                        y: rect.top + rect.height / 2,
                    });
                    if (output.length >= maxTargets) break;
                }
                return output;
            }
        """
        for frame in page.frames:
            try:
                targets = frame.evaluate(script, {"seen": list(seen_signatures), "maxTargets": max_targets})
            except Exception:
                continue
            if targets:
                return targets
        return []


    def _click_next_uncontacted_candidate(
        self,
        page: Page,
        seen_signatures: set[str],
        should_skip_candidate_signature: Callable[[str], bool] | None = None,
        on_skipped_signature: Callable[[str], None] | None = None,
    ) -> dict:
        started = time.monotonic()
        targets = self._collect_uncontacted_candidate_targets(page, seen_signatures, max_targets=24)
        skipped_count = 0
        emitted_skips: set[str] = set()
        for result in targets:
            signature = result["signature"]
            normalized_signature = re.sub(r"\s+", " ", signature).strip()
            if normalized_signature in emitted_skips:
                continue
            position_key = result["positionKey"]
            stable_id = result.get("stableId") or ""
            skip_hit = should_skip_candidate_signature(signature) if should_skip_candidate_signature else False
            logger.info("点击前候选人判定：skip_hit={}，stable_id={}，signature={}", skip_hit, stable_id, signature[:120])
            if skip_hit:
                emitted_skips.add(normalized_signature)
                seen_signatures.add(signature)

                skipped_count += 1
                print(f"已快速跳过重复候选人：{signature[:80]}")
                if on_skipped_signature:
                    on_skipped_signature(signature)
                continue
            page.mouse.click(result["x"], result["y"])
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if skipped_count:
                logger.info("本轮扫描已批量跳过重复候选人 {} 位，耗时 {}ms。", skipped_count, elapsed_ms)
            clicked_signature = signature
            return {"status": "clicked", "signature": clicked_signature, "skipped_count": skipped_count, "elapsed_ms": elapsed_ms}
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if skipped_count:
            logger.info("本轮扫描候选人均为重复，已跳过 {} 位，耗时 {}ms。", skipped_count, elapsed_ms)
            return {"status": "skipped_only", "signature": "", "skipped_count": skipped_count, "elapsed_ms": elapsed_ms}
        return {"status": "not_found", "signature": "", "skipped_count": 0, "elapsed_ms": elapsed_ms}

    def _scroll_candidate_list(self, page: Page, delta_y: int = 900) -> bool:
        script = r"""
            ({deltaY}) => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const candidates = Array.from(document.querySelectorAll('div, aside, section, ul, main'))
                    .filter((el) => isVisible(el))
                    .map((el) => ({ el, rect: el.getBoundingClientRect(), text: normalize(el.innerText || el.textContent), scrollable: el.scrollHeight - el.clientHeight }))
                    .filter((item) =>
                        item.scrollable > 20 &&
                        item.rect.left >= 120 && item.rect.left <= 470 &&
                        item.rect.top <= 260 && item.rect.bottom >= 500 &&
                        item.rect.width >= 180 && item.rect.width <= 360 &&
                        /附件简历|不合适|未联系|请查收|感兴趣|沟通|候选/.test(item.text)
                    )
                    .sort((a, b) => (b.scrollable - a.scrollable) || (b.rect.height - a.rect.height));
                const item = candidates[0];
                if (!item) return null;
                const before = item.el.scrollTop;
                item.el.scrollTop = Math.min(item.el.scrollHeight, item.el.scrollTop + deltaY);
                item.el.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY }));
                return {
                    before,
                    after: item.el.scrollTop,
                    max: item.el.scrollHeight - item.el.clientHeight,
                    text: item.text.slice(0, 120),
                    x: Math.round(item.rect.left),
                    y: Math.round(item.rect.top),
                    w: Math.round(item.rect.width),
                    h: Math.round(item.rect.height),
                };
            }
        """
        for frame in page.frames:
            try:
                result = frame.evaluate(script, {"deltaY": delta_y})
            except Exception:
                continue
            if result:
                logger.info(
                    "已滚动候选人列表：before={} after={} max={} rect=({},{} {}x{}) text={}",
                    result.get("before"),
                    result.get("after"),
                    result.get("max"),
                    result.get("x"),
                    result.get("y"),
                    result.get("w"),
                    result.get("h"),
                    result.get("text"),
                )
                return bool(result.get("after") != result.get("before"))
        page.mouse.move(300, 520)
        page.mouse.wheel(0, delta_y)
        return False

    def _print_candidate_candidates(self, page: Page) -> None:

        script = r"""
            () => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1440;
                return Array.from(document.querySelectorAll('li, article, section, div[role="listitem"], div[role="button"], [class*="candidate"], [class*="resume"], [class*="conversation"], [class*="session"], [class*="unread"], [class*="badge"], [class*="dot"]'))
                    .filter((el) => isVisible(el))
                    .map((el) => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return { text: normalize(el.innerText || el.textContent).slice(0, 180), cls: String(el.className || '').slice(0, 80), bg: style.backgroundColor, color: style.color, x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height) };
                    })
                    .filter((item) => item.x < viewportWidth * 0.68 && (item.text || item.cls))
                    .slice(0, 80);
            }
        """
        print("未找到未联系候选人。当前左侧候选列表/会话候选：")
        for frame_index, frame in enumerate(page.frames, start=1):
            try:
                candidates = frame.evaluate(script)
            except Exception:
                continue
            if len(page.frames) > 1:
                print(f"Frame {frame_index}: {frame.url}")
            for index, item in enumerate(candidates, start=1):
                print(f"{index}. ({item['x']},{item['y']},{item['w']}x{item['h']}) text={item.get('text', '')} class={item.get('cls', '')} bg={item.get('bg', '')} color={item.get('color', '')}")

    def _click_text_in_chat_detail(
        self,
        page: Page,
        texts: list[str],
        timeout: int = 8000,
        exclude_texts: list[str] | None = None,
    ) -> bool:
        script = r"""
            ({texts, excludes}) => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const allText = (el) => normalize([
                    el.innerText,
                    el.textContent,
                    el.getAttribute('title'),
                    el.getAttribute('aria-label'),
                    el.getAttribute('href'),
                    el.getAttribute('download'),
                ].filter(Boolean).join(' '));
                const hasKeyword = (text) => texts.some((keyword) => text.includes(keyword));
                const hasExclude = (text) => excludes.some((keyword) => text.includes(keyword));
                const fileHint = /附件简历|简历附件|查看简历|下载简历|\.pdf|\.doc|\.docx|pdf|doc|docx|resume|download/i;
                const strongHint = /查看简历附件|查看附件简历|下载附件简历|下载简历附件|查看简历|附件简历|简历附件/i;
                const clickableAncestor = (el) => {
                    let best = null;
                    let cur = el;
                    for (let i = 0; cur && i < 8; i += 1, cur = cur.parentElement) {
                        if (!isVisible(cur)) continue;
                        const role = cur.getAttribute('role') || '';
                        const tag = cur.tagName ? cur.tagName.toLowerCase() : '';
                        const cls = String(cur.className || '').toLowerCase();
                        const style = window.getComputedStyle(cur);
                        const text = allText(cur);
                        const rect = cur.getBoundingClientRect();
                        if (rect.left <= 420 || rect.top <= 70 || rect.width > 760 || rect.height > 260) continue;
                        if (tag === 'button' || tag === 'a' || role === 'button' || typeof cur.onclick === 'function' || cls.includes('button') || cls.includes('btn') || cls.includes('link') || style.cursor === 'pointer' || cur.hasAttribute('tabindex')) {
                            best = cur;
                            if (tag === 'a' || tag === 'button' || strongHint.test(text)) break;
                        }
                    }
                    return best || el;
                };
                const nodes = Array.from(document.querySelectorAll('a, button, [role="button"], [tabindex], [download], [href], span, p, div, li, section, article, [class*="button"], [class*="btn"], [class*="link"], [class*="file"], [class*="attach"], [class*="resume"]'));
                const candidates = [];
                for (const el of nodes) {
                    if (!isVisible(el)) continue;
                    const ownText = allText(el);
                    const target = clickableAncestor(el);
                    const targetText = allText(target);
                    const combinedText = normalize(`${ownText} ${targetText}`);
                    if (!combinedText || !hasKeyword(combinedText) || hasExclude(combinedText)) continue;
                    if (!strongHint.test(combinedText) && !fileHint.test(combinedText)) continue;
                    const rect = target.getBoundingClientRect();
                    if (rect.left <= 420 || rect.top <= 70 || rect.width > 760 || rect.height > 260) continue;
                    const tag = target.tagName ? target.tagName.toLowerCase() : '';
                    const href = target.getAttribute('href') || el.getAttribute('href') || '';
                    const cls = String(target.className || '');
                    const exactScore = texts.some((text) => ownText === text || targetText === text) ? 60 : 0;
                    const strongScore = strongHint.test(combinedText) ? 45 : 0;
                    const fileScore = fileHint.test(combinedText) ? 25 : 0;
                    const tagScore = tag === 'a' ? 35 : tag === 'button' ? 30 : target.getAttribute('role') === 'button' ? 22 : 0;
                    const hrefScore = href ? 20 : 0;
                    const smallScore = Math.max(0, 20 - Math.round((rect.width * rect.height) / 2500));
                    candidates.push({ el: target, rect, text: combinedText, tag, href, cls, score: exactScore + strongScore + fileScore + tagScore + hrefScore + smallScore - rect.top / 2000 });
                }
                candidates.sort((a, b) => b.score - a.score || a.rect.top - b.rect.top || a.rect.left - b.rect.left);
                const item = candidates[0];
                if (!item) return null;
                item.el.scrollIntoView({ block: 'center', inline: 'center' });
                const rect = item.el.getBoundingClientRect();
                const x = rect.left + Math.min(Math.max(rect.width * 0.5, 8), Math.max(rect.width - 8, 8));
                const y = rect.top + Math.min(Math.max(rect.height * 0.5, 8), Math.max(rect.height - 8, 8));
                for (const type of ['pointerover', 'mouseover', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    const eventOptions = { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: type.includes('down') ? 1 : 0, pointerType: 'mouse' };
                    const event = type.startsWith('pointer') ? new PointerEvent(type, eventOptions) : new MouseEvent(type, eventOptions);
                    item.el.dispatchEvent(event);
                }
                if (typeof item.el.click === 'function') item.el.click();
                return { text: item.text.slice(0, 160), x, y, tag: item.tag, href: item.href || '', cls: String(item.cls || '').slice(0, 80), score: item.score };
            }
        """
        deadline = time.monotonic() + timeout / 1000
        payload = {"texts": texts, "excludes": exclude_texts or []}
        last_result = None
        while time.monotonic() < deadline:
            for frame in page.frames:
                try:
                    result = frame.evaluate(script, payload)
                except Exception:
                    continue
                if result:
                    last_result = result
                    page.mouse.move(result["x"], result["y"])
                    page.mouse.down()
                    page.wait_for_timeout(80)
                    page.mouse.up()
                    page.wait_for_timeout(120)
                    page.mouse.click(result["x"], result["y"], click_count=2, delay=80)
                    logger.info(
                        "已点击聊天详情按钮：text={} tag={} href={} score={} class={}",
                        result.get("text"),
                        result.get("tag"),
                        result.get("href"),
                        result.get("score"),
                        result.get("cls"),
                    )
                    return True
            page.wait_for_timeout(200)
        if last_result:
            logger.warning("聊天详情按钮点击候选存在但未成功返回：{}", last_result)
        return False


    def _print_chat_detail_actions(self, page: Page) -> None:
        script = r"""
            () => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                return Array.from(document.querySelectorAll('button, a, div[role="button"], span, div, [class*="button"], [class*="btn"]'))
                    .filter((el) => isVisible(el))
                    .map((el) => {
                        const rect = el.getBoundingClientRect();
                        return { text: normalize(el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label')).slice(0, 120), cls: String(el.className || '').slice(0, 80), x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height) };
                    })
                    .filter((item) => item.x > 430 && item.text)
                    .slice(0, 80);
            }
        """
        print("当前聊天详情区可见操作候选：")
        for frame_index, frame in enumerate(page.frames, start=1):
            try:
                candidates = frame.evaluate(script)
            except Exception:
                continue
            if len(page.frames) > 1:
                print(f"Frame {frame_index}: {frame.url}")
            for index, item in enumerate(candidates, start=1):
                print(f"{index}. ({item['x']},{item['y']},{item['w']}x{item['h']}) text={item['text']} class={item['cls']}")

    def _click_request_attachment_resume(self, page: Page) -> bool:
        return self._click_text_in_chat_detail(
            page,
            ["要附件简历", "索要附件简历", "请求附件简历", "获取附件简历", "要简历"],
            timeout=8000,
            exclude_texts=["已向对方要附件简历", "已要附件简历", "已索要"],
        )

    def _click_view_attachment_resume(self, page: Page) -> bool:
        return self._click_text_in_chat_detail(
            page,
            ["查看简历附件", "查看附件简历", "查看简历", "下载附件简历", "下载简历附件", "附件简历", "简历附件", "下载", "查看", "PDF", "pdf", "DOC", "doc"],
            timeout=12000,
            exclude_texts=["已向对方要附件简历", "要附件简历", "索要附件简历", "请求附件简历", "获取附件简历", "要简历"],
        )

    def _clean_candidate_name(self, value: str) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return ""
        stop_tokens = [
            "沟通", "聊天", "附件", "简历", "查看", "下载", "电话", "手机号", "求职", "职位", "岗位",
            "未读", "已读", "在线", "打招呼", "要附件", "本科", "专科", "硕士", "博士", "经验",
            "岁", "性别", "工作", "学历", "平台", "快捷", "发送", "复制", "不合适", "约面试",
            "设置备注", "已向对方要附件简历", "待识别",
        ]
        text = re.sub(r"(姓名|候选人|联系人)[:：]", " ", text)
        for part in re.split(r"[｜|/\\,，;；:：\n\r\t ]+", text):
            part = part.strip(" ·-—_()（）[]【】")
            if not part or any(token in part for token in stop_tokens):
                continue
            if re.search(r"\d|岁|年|男|女", part):
                continue
            if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", part) or re.fullmatch(r"[A-Za-z][A-Za-z .·-]{1,30}", part):
                return part
        return ""

    def _clean_candidate_job_title(self, value: str, candidate_name: str = "") -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip(" -—｜|:：")
        if not text or text == candidate_name:
            return ""
        job_keywords = [
            "工程师", "经理", "主管", "专员", "顾问", "运营", "销售", "开发", "产品", "设计", "会计", "人事",
            "行政", "客服", "教师", "司机", "助理", "总监", "招聘", "采购", "算法", "测试", "前端", "后端",
            "架构", "实施", "运维", "财务", "出纳", "法务", "分析师", "需求分析",
        ]
        company_noise = ["有限公司", "分公司", "集团", "科技", "公司", "企业", "中心", "事业部", "工作室", "系统集成"]
        section_noise = ["工作经历", "项目经历", "教育经历", "实习经历", "培训经历", "校园经历"]
        text = re.sub(r"^(求职岗位|求职职位|应聘岗位|应聘职位|期望职位|期望岗位|目标职位|目标岗位|职位|岗位)[:： ]*", "", text).strip(" -—｜|")
        text = re.sub(r"^(" + "|".join(section_noise) + r")\s*[（(]?\s*\d+(?:\.\d+)?\s*年\s*[）)]?\s*", "", text).strip(" -—｜|")
        text = re.split(r"电话|手机|性别|姓名|男|女|\d{2,}|岁|经验|本科|专科|硕士|博士|学历|在线|沟通|附件|简历", text)[0].strip(" -—｜|")
        parts = [part.strip(" -—｜|/\\,，;；:：()（）[]【】") for part in re.split(r"[·•|｜/\\,，;；\n\r\t]+", text)]
        candidates = [part for part in parts if part]
        candidates.append(text)
        text = ""
        for part in reversed(candidates):
            if not (2 <= len(part) <= 40):
                continue
            if any(token in part for token in company_noise + section_noise):
                continue
            if any(keyword.lower() in part.lower() for keyword in job_keywords):
                text = part
                break
        if not text:
            text = candidates[-1] if candidates else ""
        if not text or text == candidate_name:
            return ""
        if self._clean_candidate_name(text) == text:
            return ""
        if any(token in text for token in ["聊天", "沟通", "附件", "简历", "查看", "下载", "电话", "手机", "未读", "已读", "快捷回复", "设置备注", "不合适"]):
            return ""
        if any(token in text for token in company_noise + section_noise):
            return ""
        return text if 2 <= len(text) <= 40 else ""

    def _parse_candidate_signature(self, signature: str) -> dict:
        name, job_title = clean_candidate_signature(signature or "")
        name = self._clean_candidate_name(name or "")
        job_title = self._clean_candidate_job_title(job_title or "", name)
        return {"name": name or "待识别", "job_title": job_title or "待识别", "extractor": "candidate_signature"}

    def _is_unknown_or_noise(self, value: str) -> bool:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return not text or text == "待识别" or any(
            token in text
            for token in ["设置备注", "不合适", "已向对方要附件简历", "要附件简历", "查看附件简历"]
        )

    def _candidate_info_score(self, info: dict) -> int:
        weights = {
            "phone": 5,
            "name": 4,
            "job_title": 3,
            "education": 2,
            "highest_degree": 2,
            "age": 2,
            "salary_expectation": 2,
            "resignation_status": 2,
            "gender": 1,
        }
        return sum(weights[key] for key in weights if info.get(key) and info.get(key) != "待识别")


    def _extract_candidate_info_from_resume_file(self, file_path: str | None, fallback_signature: str = "") -> dict:
        if not file_path:
            return {}
        try:
            parsed = parse_resume_file(file_path, candidate_signature=fallback_signature)
        except Exception as exc:
            logger.warning("附件简历解析失败，跳过附件字段兜底：{}", exc)
            return {}
        data = parsed.to_dict()
        return {
            "name": data.get("name") or "待识别",
            "gender": self._extract_gender_from_resume_text(parsed.text) or "待识别",
            "age": self._extract_age_from_text(parsed.text) or "待识别",
            "education": data.get("highest_degree") or self._extract_education_from_text(parsed.text) or "待识别",
            "highest_degree": data.get("highest_degree") or self._extract_education_from_text(parsed.text) or "待识别",
            "job_title": data.get("expected_position") or data.get("current_position") or data.get("job_title") or "待识别",
            "phone": data.get("phone") or "待识别",
            "resignation_status": self._extract_resignation_status_from_text(parsed.text) or "待识别",
            "salary_expectation": self._extract_salary_expectation_from_text(parsed.text) or "待识别",
            "profile_text": data.get("text_preview") or parsed.text[:1000],
            "extractor": "resume_file",
        }

    def _extract_gender_from_resume_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", str(text or ""))
        if re.search(r"性别[:： ]*男|男士|男 \|", text):
            return "男"
        if re.search(r"性别[:： ]*女|女士|女 \|", text):
            return "女"
        return ""

    def _extract_age_from_text(self, text: str) -> str:
        merged = re.sub(r"\s+", " ", str(text or ""))
        patterns = [
            r"(?:年龄|年纪)[:： ]*(\d{2})\s*岁?",
            r"(?<!\d)(\d{2})\s*岁(?!\d)",
            r"(?:19\d{2}|20[0-1]\d)\s*年(?:出生)?",
        ]
        for pattern in patterns[:2]:
            match = re.search(pattern, merged)
            if match:
                age = int(match.group(1))
                if 16 <= age <= 70:
                    return f"{age}岁"
        birth_match = re.search(patterns[2], merged)
        if birth_match:
            birth_year = int(re.search(r"\d{4}", birth_match.group(0)).group(0))
            age = datetime.now().year - birth_year
            if 16 <= age <= 70:
                return f"{age}岁"
        return ""

    def _extract_salary_expectation_from_text(self, text: str) -> str:
        merged = re.sub(r"\s+", " ", str(text or ""))
        patterns = [
            r"(?:期望薪资|薪资要求|薪资期望|月薪要求|期望月薪|待遇要求)[:： ]*([^，,；;|｜\n]{2,30})",
            r"(?<!\d)(\d{1,3}\s*[kKＫ]|\d{1,3}\s*千|\d{1,3}\s*万)(?:\s*[-~—至到]\s*(\d{1,3}\s*[kKＫ]|\d{1,3}\s*千|\d{1,3}\s*万))?(?:\s*/\s*月|元/月|月薪)?",
        ]
        for pattern in patterns:
            match = re.search(pattern, merged)
            if match:
                value = re.sub(r"\s+", "", match.group(0 if pattern.startswith('(?<!') else 1)).strip(" ：:")
                if 2 <= len(value) <= 30:
                    return value
        return ""

    def _extract_resignation_status_from_text(self, text: str) -> str:
        merged = re.sub(r"\s+", " ", str(text or ""))
        status_patterns = [
            ("已离职", r"已离职|离职-随时到岗|随时到岗|目前离职|离职状态[:： ]*已离职"),
            ("在职-考虑机会", r"在职-考虑机会|在职考虑机会|看看机会|考虑机会"),
            ("在职", r"目前在职|在职状态|离职状态[:： ]*在职|在职"),
        ]
        for status, pattern in status_patterns:
            if re.search(pattern, merged):
                return status
        return ""

    def _extract_education_from_text(self, text: str) -> str:
        merged = re.sub(r"\s+", " ", str(text or ""))
        degrees = ["博士", "硕士", "研究生", "本科", "大专", "专科", "高中", "中专"]
        for degree in degrees:
            if re.search(rf"(?:学历|最高学历|教育程度)[:： ]*[^，,；;|｜\n]{{0,12}}{degree}|{degree}", merged):
                if degree == "研究生":
                    return "硕士"
                if degree == "专科":
                    return "大专"
                return degree
        return ""

    def _parse_candidate_info_text(self, source_text: str, fallback_signature: str = "", extractor: str = "dom_fallback") -> dict:
        lines = [re.sub(r"\s+", " ", line).strip() for line in str(source_text or "").splitlines()]
        lines = [line for line in lines if line and len(line) <= 220]
        merged = " ".join(lines) or fallback_signature
        phone_match = re.search(r"(?<!\d)(1[3-9]\d{9})(?!\d)", merged)
        gender = ""
        if re.search(r"性别[:： ]*男|(^|[^\u4e00-\u9fa5])男([^\u4e00-\u9fa5]|$)", merged):
            gender = "男"
        elif re.search(r"性别[:： ]*女|(^|[^\u4e00-\u9fa5])女([^\u4e00-\u9fa5]|$)", merged):
            gender = "女"

        age = self._extract_age_from_text(merged)
        education = self._extract_education_from_text(merged)
        resignation_status = self._extract_resignation_status_from_text(merged)
        salary_expectation = self._extract_salary_expectation_from_text(merged)

        name = ""
        job_title = ""
        job_keywords = [
            "工程师", "经理", "主管", "专员", "顾问", "运营", "销售", "开发", "产品", "设计", "会计", "人事",
            "行政", "客服", "教师", "司机", "助理", "总监", "招聘", "采购", "算法", "测试", "前端", "后端",
            "架构", "实施", "运维", "财务", "出纳", "法务",
        ]
        label_pattern = r"求职岗位|求职职位|应聘岗位|应聘职位|期望职位|期望岗位|目标职位|目标岗位|职位|岗位"
        for line in lines:
            if not name:
                match = re.search(r"(?:姓名|候选人)[:： ]*([\u4e00-\u9fa5]{2,4}|[A-Za-z][A-Za-z .·-]{1,30})", line)
                name = self._clean_candidate_name(match.group(1) if match else line)
            if not job_title:
                match = re.search(rf"(?:{label_pattern})[:： ]*([^｜|,，;；\n\r]+)", line)
                if match:
                    job_title = self._clean_candidate_job_title(match.group(1), name)
            if not job_title and any(token in line for token in job_keywords):
                job_title = self._clean_candidate_job_title(line, name)

        if not name:
            name = self._clean_candidate_name(fallback_signature)
        if not job_title:
            for line in fallback_signature.splitlines():
                if any(token in line for token in job_keywords):
                    job_title = self._clean_candidate_job_title(line, name)
                    if job_title:
                        break
        return {
            "name": name or "待识别",
            "gender": gender or "待识别",
            "age": age or "待识别",
            "education": education or "待识别",
            "highest_degree": education or "待识别",
            "job_title": job_title or "待识别",
            "phone": phone_match.group(1) if phone_match else "待识别",
            "resignation_status": resignation_status or "待识别",
            "salary_expectation": salary_expectation or "待识别",
            "profile_text": "\n".join(lines),
            "extractor": extractor,
        }

    def _extract_profile_text_by_dom(self, page: Page) -> str:
        script = r"""
            () => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1440;
                const keywords = /姓名|候选人|男|女|求职|应聘|职位|岗位|电话|手机|1[3-9]\d{9}|年龄|\d{2}\s*岁|经验|本科|大专|专科|硕士|博士|研究生|高中|中专|学历|最高学历|教育|工作年限|离职|在职|到岗|期望薪资|薪资要求|薪资期望|月薪要求|期望月薪|待遇要求|\d{1,3}\s*[kKＫ]|\d{1,3}\s*千|\d{1,3}\s*万/;
                const excludes = /快捷回复|发送|表情|聊天记录|要附件简历|查看简历附件|下载简历|请输入|复制|已读|未读/;
                const nodes = Array.from(document.querySelectorAll('aside, section, header, article, div, span, p, li'))
                    .filter((el) => isVisible(el))
                    .map((el) => {
                        const rect = el.getBoundingClientRect();
                        const rawText = el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label');
                        const text = normalize(rawText);
                        const cls = String(el.className || '');
                        const area = rect.width * rect.height;
                        const rightPanel = rect.left >= Math.max(420, viewportWidth * 0.42) && rect.top >= 40 && rect.top <= 760;
                        const profileClass = /candidate|profile|detail|resume|user|person|talent|card|info|basic/i.test(cls);
                        const keywordScore = (text.match(keywords) || []).length;
                        return { text, rawText, cls, x: rect.left, y: rect.top, w: rect.width, h: rect.height, area, rightPanel, profileClass, keywordScore };
                    })
                    .filter((item) => item.text && item.text.length >= 2 && item.text.length <= 900)
                    .filter((item) => item.rightPanel && !excludes.test(item.text) && (keywords.test(item.text) || item.profileClass));
                nodes.sort((a, b) => {
                    const scoreA = (a.keywordScore * 10) + (a.profileClass ? 8 : 0) + Math.min(a.area / 10000, 8) - (a.y / 1000);
                    const scoreB = (b.keywordScore * 10) + (b.profileClass ? 8 : 0) + Math.min(b.area / 10000, 8) - (b.y / 1000);
                    return scoreB - scoreA;
                });
                const seen = new Set();
                const lines = [];
                for (const item of nodes.slice(0, 36)) {
                    for (const part of item.text.split(/\n| {2,}|\t/)) {
                        const line = normalize(part);
                        if (!line || line.length > 220 || seen.has(line) || excludes.test(line)) continue;
                        seen.add(line);
                        lines.push(line);
                        if (lines.length >= 30) break;
                    }
                    if (lines.length >= 30) break;
                }
                return lines.join('\n');
            }
        """
        for frame in page.frames:
            try:
                text = frame.evaluate(script)
            except Exception:
                continue
            if text:
                return text
        return ""

    def _merge_candidate_info(self, primary: dict, fallback: dict) -> dict:
        merged = dict(primary or {})
        for key in ["name", "gender", "age", "education", "highest_degree", "job_title", "phone", "resignation_status", "salary_expectation"]:
            if not merged.get(key) or merged.get(key) == "待识别":
                merged[key] = fallback.get(key) or "待识别"
        profile_texts = [text for text in [merged.get("profile_text"), fallback.get("profile_text")] if text]
        merged["profile_text"] = "\n".join(dict.fromkeys("\n".join(profile_texts).splitlines()))
        merged["extractor"] = f"{merged.get('extractor', 'dom')}+fallback"
        return merged

    def _extract_current_candidate_info(
        self,
        page: Page,
        fallback_signature: str = "",
        resume_file_path: str | None = None,
        use_scrapling: bool = False,
    ) -> dict:
        signature_info = self._parse_candidate_signature(fallback_signature)
        resume_info = self._extract_candidate_info_from_resume_file(resume_file_path, fallback_signature)
        dom_text = self._extract_profile_text_by_dom(page)
        dom_info = self._parse_candidate_info_text(dom_text, fallback_signature, extractor="dom_profile")
        best_info = self._merge_candidate_info(resume_info, dom_info) if resume_info else dom_info
        if use_scrapling:
            try:
                from recruitment_assistant.extractors.scrapling_candidate_extractor import extract_candidate_info

                scrapling_info = extract_candidate_info(page.content(), fallback_signature)
                best_info = self._merge_candidate_info(best_info, scrapling_info)
            except Exception as exc:
                logger.warning("Scrapling 候选人信息提取失败，使用附件/DOM结果：{}", exc)
        if not best_info:
            best_info = signature_info
        if not self._is_unknown_or_noise(signature_info.get("name")):
            best_info["name"] = signature_info["name"]
        elif self._is_unknown_or_noise(best_info.get("name")):
            best_info["name"] = "待识别"
        if not self._is_unknown_or_noise(signature_info.get("job_title")):
            best_info["job_title"] = signature_info["job_title"]
        elif self._is_unknown_or_noise(best_info.get("job_title")):
            best_info["job_title"] = "待识别"
        best_info["extractor"] = f"{best_info.get('extractor', 'unknown')}+signature_guard"
        logger.info(
            "候选人信息提取结果：name={}, age={}, education={}, job_title={}, phone={}, extractor={}",
            best_info.get("name"),
            best_info.get("age"),
            best_info.get("education") or best_info.get("highest_degree"),
            best_info.get("job_title"),
            best_info.get("phone"),
            best_info.get("extractor"),
        )
        return best_info

    def auto_click_chat_attachment_resumes(
        self,
        target_url: str | None = None,
        max_resumes: int = 5,
        wait_seconds: int = 900,
        per_candidate_wait_seconds: int = 60,
        min_download_interval_seconds: int = 5,
        on_resume_saved: Callable[[dict], None] | None = None,
        should_skip_candidate_signature: Callable[[str], bool] | None = None,
        should_skip_candidate_profile: Callable[[dict, str], bool] | None = None,
        on_resume_skipped: Callable[[dict], None] | None = None,
        snapshot_paths: list[str] | None = None,
        should_continue: Callable[[], bool] | None = None,
        on_diagnostic: Callable[[str], None] | None = None,
        on_download_failed: Callable[[dict], None] | None = None,
    ) -> list[dict]:
        if not self.state_path.exists():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = open_browser_session(state_path=self.state_path, headless=False)
        results = []
        captured_urls: set[str] = set()
        downloaded_urls: set[str] = set()
        pending_urls: list[dict] = []
        ignored_attachment_urls: set[str] = set()
        pending_downloads: list[dict] = []
        bound_download_pages: set[int] = set()
        seen_candidates: set[str] = set()
        last_download_monotonic = 0.0
        active_candidate_started_at = 0.0
        active_candidate_page_ids: set[int] = set()


        def can_continue() -> bool:
            return should_continue() if should_continue else True

        def diag(message: str) -> None:
            text = message
            logger.info(text)
            if on_diagnostic:
                on_diagnostic(text)

        def handle_request(request: Request) -> None:
            url = request.url
            now = time.monotonic()
            if (
                self._is_resume_attachment_download_url(url)
                and not any(item.get("url") == url for item in pending_urls)
                and url not in downloaded_urls
                and url not in ignored_attachment_urls
                and active_candidate_started_at
                and now >= active_candidate_started_at
            ):
                source_page = None
                try:
                    source_page = request.frame.page
                except Exception:
                    source_page = None
                page_id = id(source_page) if source_page and not source_page.is_closed() else 0
                if active_candidate_page_ids and page_id and page_id not in active_candidate_page_ids:
                    active_candidate_page_ids.add(page_id)
                    diag(
                        "已将下载请求来源页补入当前候选人范围："
                        f"page_id={page_id}，url_hash={text_hash(url)[:10] if text_hash(url) else '空'}"
                    )
                add_pending_url(url, now, page_id, dict(request.headers))
                print(f"已从网络请求捕获当前候选人附件下载链接：{url}")

        def handle_route(route: Route, request: Request) -> None:
            url = request.url
            now = time.monotonic()
            if self._is_resume_attachment_download_url(url):
                if (
                    active_candidate_started_at
                    and now >= active_candidate_started_at
                    and url not in downloaded_urls
                    and url not in ignored_attachment_urls
                ):
                    source_page = None
                    try:
                        source_page = request.frame.page
                    except Exception:
                        source_page = None
                    page_id = id(source_page) if source_page and not source_page.is_closed() else 0
                    if active_candidate_page_ids and page_id and page_id not in active_candidate_page_ids:
                        active_candidate_page_ids.add(page_id)
                        diag(
                            "已将拦截下载请求来源页补入当前候选人范围："
                            f"page_id={page_id}，url_hash={text_hash(url)[:10] if text_hash(url) else '空'}"
                        )
                    add_pending_url(url, now, page_id, dict(request.headers))
                    diag(
                        "已拦截浏览器原生附件下载，改由系统内保存："
                        f"url_hash={text_hash(url)[:10] if text_hash(url) else '空'}"
                    )
                else:
                    diag(
                        "已阻止非当前候选人附件下载，避免浏览器下载气泡堆积："
                        f"url_hash={text_hash(url)[:10] if text_hash(url) else '空'}"
                    )
                if request.is_navigation_request():
                    route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        body=(
                            "<!doctype html><meta charset='utf-8'>"
                            "<title>附件简历已由系统接管</title>"
                            "<body style='font-family:Microsoft YaHei,Arial,sans-serif;padding:48px;color:#1f2937;'>"
                            "<h2>附件简历下载已由系统接管</h2>"
                            "<p>请回到采集页面查看保存和入库结果。本页面不会触发浏览器原生下载。</p>"
                            "</body>"
                        ),
                    )
                else:
                    route.abort()
                return
            route.continue_()




        def handle_download(download: Download) -> None:
            try:
                download_page = download.page
            except Exception:
                download_page = None
            pending_downloads.append(
                {
                    "download": download,
                    "page": download_page,
                    "created_at": time.monotonic(),
                    "url": download.url or "",
                    "suggested_filename": download.suggested_filename or "",
                }
            )
            diag(
                "已捕获浏览器原生下载事件："
                f"filename={download.suggested_filename or '空'}，"
                f"url_hash={text_hash(download.url or '')[:10] if text_hash(download.url or '') else '空'}"
            )

        def bind_download_listener(target_page: Page) -> None:
            if target_page.is_closed() or id(target_page) in bound_download_pages:
                return
            target_page.on("download", handle_download)
            bound_download_pages.add(id(target_page))

        def handle_new_page(new_page: Page) -> None:
            bind_download_listener(new_page)
            if active_candidate_started_at:
                active_candidate_page_ids.add(id(new_page))
            diag("已监听新弹出页面的下载事件。")

        def bind_existing_download_pages() -> None:
            for target_page in [item for item in session.context.pages if not item.is_closed()]:
                bind_download_listener(target_page)

        def drain_stale_downloads(before_monotonic: float) -> int:
            stale_items = [item for item in pending_downloads if item.get("created_at", 0) < before_monotonic]
            if stale_items:
                pending_downloads[:] = [item for item in pending_downloads if item.get("created_at", 0) >= before_monotonic]
            return len(stale_items)

        def close_non_chat_pages(keep_page: Page) -> int:
            closed_count = 0
            for target_page in list(session.context.pages):
                if target_page.is_closed() or target_page == keep_page:
                    continue
                try:
                    target_url = target_page.url.lower()
                except Exception:
                    target_url = ""
                if self._is_resume_attachment_download_url(target_url) or "attachment.zhaopin.com" in target_url or "resume" in target_url or "preview" in target_url:
                    try:
                        target_page.close()
                        closed_count += 1
                    except Exception:
                        continue
            return closed_count

        def candidate_pages() -> list[Page]:
            return [
                item
                for item in session.context.pages
                if not item.is_closed() and id(item) in active_candidate_page_ids
            ]

        def remember_candidate_pages(pages_before_ids: set[int]) -> int:
            new_count = 0
            for target_page in [item for item in session.context.pages if not item.is_closed()]:
                page_id = id(target_page)
                if page_id not in pages_before_ids and page_id not in active_candidate_page_ids:
                    active_candidate_page_ids.add(page_id)
                    bind_download_listener(target_page)
                    new_count += 1
            return new_count

        def add_pending_url(url: str, created_at: float, page_id: int = 0, request_headers: dict | None = None) -> None:
            if (
                url
                and url not in downloaded_urls
                and url not in ignored_attachment_urls
                and not any(item.get("url") == url for item in pending_urls)
            ):
                pending_urls.append({"url": url, "created_at": created_at, "page_id": page_id, "headers": request_headers or {}})

        def cleanup_current_candidate_pages() -> int:
            closed_count = 0
            for target_page in list(session.context.pages):
                if target_page.is_closed() or target_page == page or id(target_page) not in active_candidate_page_ids:
                    continue
                try:
                    target_page.close()
                    closed_count += 1
                except Exception:
                    continue
            active_candidate_page_ids.clear()
            return closed_count

        def save_debug_snapshot(label: str) -> None:
            if snapshot_paths is None:
                return
            try:
                html_path = save_text_snapshot(self.platform_code, f"{page.url}#{label}", page.content())
                snapshot_paths.append(str(html_path))
                png_path = html_path.with_suffix(".png")
                page.screenshot(path=str(png_path), full_page=True)
                snapshot_paths.append(str(png_path))
            except Exception as exc:
                logger.warning("保存调试快照失败：{}", exc)


        try:
            session.context.route("**/*", handle_route)
            session.context.on("request", handle_request)
            session.context.on("page", handle_new_page)
            page = session.page
            bind_existing_download_pages()
            page.goto(target_url or self.home_url, wait_until="domcontentloaded", timeout=30000)
            if not self._open_chat_interface(page):
                raise RuntimeError("未能进入智联聊天界面，请检查页面是否已登录或传入 --url 聊天页面地址。")
            save_debug_snapshot("opened_chat")
            print("\n智联聊天候选人全自动采集已启动：")
            print("1. 自动扫描包含'未联系'的候选人。")
            print("2. 自动点击候选人、'要附件简历'、'查看附件简历'。")
            print("3. 自动捕获附件下载链接并保存 PDF。")
            print(f"4. 目标数量：{max_resumes}，最长运行：{wait_seconds} 秒。\n")

            deadline = time.monotonic() + wait_seconds
            consecutive_not_found = 0
            while len(results) < max_resumes and time.monotonic() < deadline:

                if not can_continue():
                    break
                def emit_skipped_candidate(candidate_signature: str) -> None:
                    if on_resume_skipped:
                        on_resume_skipped({
                            "platform_code": self.platform_code,
                            "source_url": page.url,
                            "raw_json": {
                                "candidate_signature": candidate_signature,
                                "candidate_info": self._parse_candidate_signature(candidate_signature),
                                "attachment": {},
                                "skip_stage": "before_click_signature",
                            },
                            "raw_html_path": None,
                            "content_hash": "",
                        })

                iter_started = time.monotonic()
                click_result = self._click_next_uncontacted_candidate(
                    page,
                    seen_candidates,
                    should_skip_candidate_signature,
                    emit_skipped_candidate,
                )
                diag(
                    f"候选人扫描完成：status={click_result.get('status')}，targets_skip={click_result.get('skipped_count')}，"
                    f"scan_ms={click_result.get('elapsed_ms')}，seen={len(seen_candidates)}"
                )
                if not can_continue():
                    break
                status = click_result.get("status")
                if status == "skipped_only":
                    diag(f"本轮全部命中点击前重复，未进入按钮/下载慢路径，轮次耗时={time.monotonic() - iter_started:.2f}s")
                    scrolled = self._scroll_candidate_list(page, 900)
                    diag(f"已滚动左侧候选列表：scrolled={scrolled}")
                    page.wait_for_timeout(300)
                    continue
                if status == "not_found":
                    consecutive_not_found += 1
                    backoff_ms = min(3000, 500 + consecutive_not_found * 300)
                    scrolled = self._scroll_candidate_list(page, 900)
                    diag(
                        f"未收集到候选人卡片，已尝试滚动左侧列表，scrolled={scrolled}，连续={consecutive_not_found}，"
                        f"等待={backoff_ms}ms，轮次耗时={time.monotonic() - iter_started:.2f}s"
                    )
                    logger.warning("未找到新的候选人卡片，尝试滚动左侧候选列表。")
                    page.wait_for_timeout(backoff_ms)
                    if consecutive_not_found in {4, 8, 12}:
                        self._print_candidate_candidates(page)
                    if consecutive_not_found >= 18:
                        diag(
                            f"连续多次未收集到候选人卡片，当前已保存 {len(results)}/{max_resumes}，"
                            "判断左侧候选列表已无可处理新卡片，结束采集。"
                        )
                        break
                    continue

                consecutive_not_found = 0
                signature = click_result.get("signature") or ""
                pending_urls.clear()
                ignored_attachment_urls.clear()
                closed_stale_pages = close_non_chat_pages(page)
                if closed_stale_pages:
                    diag(f"已关闭候选人切换前残留简历/附件页面：{closed_stale_pages} 个。")
                existing_pages = [item for item in session.context.pages if not item.is_closed()]
                ignored_attachment_urls.update(
                    self._find_attachment_urls_from_pages(
                        existing_pages,
                        captured_urls,
                        mark_captured=False,
                    )
                )


                seen_candidates.update(signature.splitlines())
                display_signature = signature.splitlines()[0]
                print(f"已选择候选人：{display_signature[:80]}")
                page.wait_for_timeout(300)
                profile_info_before_download = self._extract_current_candidate_info(
                    page,
                    display_signature,
                    resume_file_path=None,
                    use_scrapling=False,
                )
                profile_label = "/".join(
                    str(profile_info_before_download.get(key) or "待识别")
                    for key in ["name", "age", "education"]
                )
                duplicate_before_download = should_skip_candidate_profile(profile_info_before_download, display_signature) if should_skip_candidate_profile else False
                diag(f"下载前个人信息重复拦截：duplicate={duplicate_before_download}，profile={profile_label}，候选人={display_signature[:80]}")
                if duplicate_before_download:
                    pending_urls.clear()
                    if on_resume_skipped:
                        on_resume_skipped({
                            "platform_code": self.platform_code,
                            "source_url": page.url,
                            "raw_json": {
                                "candidate_signature": display_signature,
                                "pre_download_candidate_info": profile_info_before_download,
                                "candidate_info": profile_info_before_download,
                                "attachment": {},
                                "skip_stage": "before_download_profile",
                            },
                            "raw_html_path": None,
                            "content_hash": "",
                        })
                    page.wait_for_timeout(50)
                    continue
                save_debug_snapshot(f"candidate_{len(results) + 1}_selected")
                if not can_continue():
                    break
                request_started = time.monotonic()
                signature_has_attachment_hint = bool(re.search(r"这是我的附件简历|附件简历[，,。 ]*请查收|已向对方要附件简历", display_signature))
                request_clicked = False if signature_has_attachment_hint else self._click_request_attachment_resume(page)
                diag(
                    f"要附件简历按钮查找完成：clicked={request_clicked}，耗时={time.monotonic() - request_started:.2f}s，"
                    f"signature_has_attachment_hint={signature_has_attachment_hint}"
                )
                if not request_clicked and not signature_has_attachment_hint:
                    logger.warning("当前候选人未找到可点击的'要附件简历'按钮，可能已经收到附件，继续尝试点击'查看简历附件'。")
                page.wait_for_timeout(300)
                save_debug_snapshot(f"candidate_{len(results) + 1}_requested")
                if not can_continue():
                    break

                def process_downloaded_row(row: dict, source_label: str, source_url: str = "") -> bool:
                    nonlocal last_download_monotonic
                    last_download_monotonic = time.monotonic()
                    attachment = row.setdefault("raw_json", {}).setdefault("attachment", {})
                    candidate_info = self._extract_current_candidate_info(
                        page,
                        display_signature,
                        attachment.get("file_path"),
                        use_scrapling=False,
                    )
                    candidate_info = self._merge_candidate_info(candidate_info, profile_info_before_download)
                    candidate_info["resume_file_name"] = attachment.get("file_name")
                    row["raw_json"]["candidate_signature"] = display_signature
                    row["raw_json"]["pre_download_candidate_info"] = profile_info_before_download
                    row["raw_json"]["candidate_info"] = candidate_info
                    results.append(row)
                    if on_resume_saved:
                        on_resume_saved(row)
                    save_debug_snapshot(f"candidate_{len(results)}_info")
                    print(f"已自动保存第 {len(results)} 份：{attachment.get('file_path')}")
                    diag(f"附件简历已保存：source={source_label}，file={attachment.get('file_name') or '空'}")
                    return True

                active_candidate_started_at = time.monotonic()
                active_candidate_page_ids.clear()
                active_candidate_page_ids.add(id(page))
                candidate_download_started = active_candidate_started_at
                stale_download_count = drain_stale_downloads(candidate_download_started)
                if stale_download_count:
                    diag(f"已丢弃候选人切换前残留浏览器下载事件：{stale_download_count} 个。")
                bind_existing_download_pages()
                view_started = time.monotonic()
                pages_before_view_list = [item for item in session.context.pages if not item.is_closed()]
                pages_before_view_ids = {id(item) for item in pages_before_view_list}
                pages_before_view = len(pages_before_view_list)
                view_clicked = self._click_view_attachment_resume(page)
                page.wait_for_timeout(500)
                bind_existing_download_pages()
                new_page_count = remember_candidate_pages(pages_before_view_ids)
                pages_after_view = len([item for item in session.context.pages if not item.is_closed()])
                diag(
                    f"查看附件简历按钮查找完成：clicked={view_clicked}，耗时={time.monotonic() - view_started:.2f}s，"
                    f"页面数={pages_before_view}->{pages_after_view}，本次新增页={new_page_count}，"
                    f"pending_downloads={len(pending_downloads)}，pending_urls={len(pending_urls)}"
                )
                if not view_clicked:
                    logger.warning("当前候选人未找到'查看附件简历'按钮，等待可能的自动链接。")
                    self._print_chat_detail_actions(page)

                wait_started = time.monotonic()
                initial_pages = candidate_pages()
                for item_page in initial_pages:
                    for url in self._find_attachment_urls_from_pages([item_page], captured_urls, mark_captured=False):
                        add_pending_url(url, time.monotonic(), id(item_page))
                effective_wait_seconds = per_candidate_wait_seconds
                if pending_urls or pending_downloads:
                    effective_wait_seconds = min(per_candidate_wait_seconds, 8)
                elif view_clicked:
                    effective_wait_seconds = min(per_candidate_wait_seconds, 8)
                elif request_clicked or signature_has_attachment_hint:
                    effective_wait_seconds = min(per_candidate_wait_seconds, 12)
                else:
                    effective_wait_seconds = min(per_candidate_wait_seconds, 5)
                candidate_deadline = time.monotonic() + effective_wait_seconds
                saved_current_candidate = False
                skipped_or_failed_current_candidate = False

                while time.monotonic() < candidate_deadline and not saved_current_candidate:
                    if not can_continue():
                        break
                    bind_existing_download_pages()
                    remember_candidate_pages(pages_before_view_ids)
                    for item_page in candidate_pages():
                        for url in self._find_attachment_urls_from_pages([item_page], captured_urls, mark_captured=False):
                            add_pending_url(url, time.monotonic(), id(item_page))
                    while pending_downloads and len(results) < max_resumes:
                        download_item = pending_downloads.pop(0)
                        download_page = download_item.get("page")
                        download_page_id = id(download_page) if download_page and not download_page.is_closed() else 0
                        if download_item.get("created_at", 0) < candidate_download_started:
                            continue
                        if download_page_id and download_page_id not in active_candidate_page_ids:
                            active_candidate_page_ids.add(download_page_id)
                            diag(
                                "已将浏览器下载来源页补入当前候选人范围："
                                f"filename={download_item.get('suggested_filename') or '空'}，page_id={download_page_id}"
                            )
                        elapsed_since_download = time.monotonic() - last_download_monotonic if last_download_monotonic else min_download_interval_seconds
                        remaining_interval = min_download_interval_seconds - elapsed_since_download
                        if remaining_interval > 0:
                            page.wait_for_timeout(int(remaining_interval * 1000))
                            if not can_continue():
                                break
                        try:
                            row = self._save_browser_download(
                                download_item["download"],
                                download_page or page,
                                mode="auto_chat_browser_download",
                            )
                        except Exception as exc:
                            logger.warning("保存浏览器原生下载失败：{}", exc)
                            continue
                        download_url = row.get("source_url") or download_item.get("url") or ""
                        if download_url:
                            downloaded_urls.add(download_url)
                        result_saved = process_downloaded_row(row, "browser_download", download_url)
                        if result_saved:
                            saved_current_candidate = True
                        else:
                            skipped_or_failed_current_candidate = True
                        break
                    if saved_current_candidate:
                        break
                    while pending_urls and len(results) < max_resumes:
                        download_item = pending_urls.pop(0)
                        download_url = download_item.get("url") or ""
                        if download_item.get("created_at", 0) < candidate_download_started:
                            continue
                        page_id = int(download_item.get("page_id") or 0)
                        if page_id and page_id not in active_candidate_page_ids:
                            diag(f"已丢弃非当前候选人页面的附件链接：page_id={page_id}，url_hash={text_hash(download_url)[:10] if text_hash(download_url) else '空'}")
                            continue
                        if download_url in downloaded_urls:
                            continue
                        elapsed_since_download = time.monotonic() - last_download_monotonic if last_download_monotonic else min_download_interval_seconds
                        remaining_interval = min_download_interval_seconds - elapsed_since_download
                        if remaining_interval > 0:
                            page.wait_for_timeout(int(remaining_interval * 1000))
                            if not can_continue():
                                break
                        try:
                            row = self._download_attachment_with_context(
                                session.context,
                                download_url,
                                filename=self._filename_from_download_url(download_url),
                                mode="auto_chat_candidate_click",
                                request_headers=download_item.get("headers") or None,
                            )
                        except Exception as exc:
                            error_payload = {
                                "url": download_url,
                                "url_hash": text_hash(download_url)[:10] if text_hash(download_url) else "",
                                "error": str(exc),
                                "candidate_signature": display_signature,
                            }
                            if on_download_failed:
                                on_download_failed(error_payload)
                            skipped_or_failed_current_candidate = True
                            diag(
                                "附件系统内下载失败："
                                f"候选人={display_signature[:60]}，url_hash={error_payload['url_hash'] or '空'}，原因={exc}"
                            )
                            logger.warning("自动下载附件失败：{}", exc)
                            break
                        downloaded_urls.add(download_url)
                        result_saved = process_downloaded_row(row, "url_capture", download_url)
                        if result_saved:
                            saved_current_candidate = True
                        else:
                            skipped_or_failed_current_candidate = True
                        break
                    page.wait_for_timeout(200)

                if not saved_current_candidate and not skipped_or_failed_current_candidate:
                    diag(
                        f"附件按钮点击后未捕获下载链接，快速跳过当前候选人："
                        f"等待耗时={time.monotonic() - wait_started:.2f}s，计划等待={effective_wait_seconds:.2f}s，"
                        f"request_clicked={request_clicked}，view_clicked={view_clicked}，pending_urls={len(pending_urls)}，pending_downloads={len(pending_downloads)}"
                    )
                    logger.warning("当前候选人在快速等待窗口内未捕获到附件下载链接。")

                else:
                    diag(f"候选人处理完成：总耗时={time.monotonic() - iter_started:.2f}s，下载等待耗时={time.monotonic() - wait_started:.2f}s")
                closed_current_pages = cleanup_current_candidate_pages()
                if closed_current_pages:
                    diag(f"已关闭本候选人产生的简历/附件页面：{closed_current_pages} 个。")
                active_candidate_started_at = 0.0

            print(f"自动采集结束：已保存 {len(results)} 份。")
            return results
        finally:
            session.close()

    def auto_collect_candidate_info_test(
        self,
        target_url: str | None = None,
        max_candidates: int = 3,
        wait_seconds: int = 300,
        per_candidate_wait_seconds: int = 20,
        on_resume_skipped: Callable[[dict], None] | None = None,
        snapshot_paths: list[str] | None = None,
        should_continue: Callable[[], bool] | None = None,
        on_diagnostic: Callable[[str], None] | None = None,
        on_download_failed: Callable[[dict], None] | None = None,
    ) -> list[dict]:
        return self.auto_click_chat_attachment_resumes(
            target_url=target_url,
            max_resumes=max_candidates,
            wait_seconds=wait_seconds,
            per_candidate_wait_seconds=per_candidate_wait_seconds,
            min_download_interval_seconds=0,
            on_resume_skipped=on_resume_skipped,
            snapshot_paths=snapshot_paths,
            should_continue=should_continue,
            on_diagnostic=on_diagnostic,
        )

    def fetch_resume_list(self) -> list[dict]:
        raise NotImplementedError("智联招聘自动简历列表采集将在页面结构确认后实现。")



    def fetch_resume_detail(self, resume_id: str) -> dict:
        raise NotImplementedError("智联招聘自动简历详情采集将在页面结构确认后实现。")

