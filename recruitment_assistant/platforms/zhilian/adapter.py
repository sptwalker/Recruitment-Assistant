import re
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse



from loguru import logger
from playwright.sync_api import BrowserContext, Download, Error as PlaywrightError, Page, Request, TimeoutError as PlaywrightTimeoutError



from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.core.browser import get_state_path, open_browser_session, save_storage_state
from recruitment_assistant.extractors.scrapling_candidate_extractor import extract_candidate_info
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
    ) -> dict:
        response = context.request.get(
            download_url,
            headers={
                "Referer": self.home_url,
                "Accept": "application/pdf,application/octet-stream,*/*",
            },
            timeout=60000,
        )
        if not response.ok:
            raise RuntimeError(f"附件下载失败：HTTP {response.status} {response.status_text}")

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
                f"content-type={content_type}, size={len(content)}, head={preview}"
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

    def _find_attachment_urls_from_pages(self, pages: list[Page], captured_urls: set[str]) -> list[str]:
        urls = []
        for page in pages:
            if page.is_closed():
                continue
            url = page.url
            if self._is_resume_attachment_download_url(url) and url not in captured_urls:
                captured_urls.add(url)
                urls.append(url)
                print(f"已从页面地址捕获附件下载链接：{url}")
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

        def handle_request(request: Request) -> None:
            url = request.url
            if self._is_resume_attachment_download_url(url) and url not in captured_urls:
                captured_urls.add(url)
                pending_urls.append(url)
                print(f"已从网络请求捕获附件下载链接：{url}")

        try:
            session.context.on("request", handle_request)
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
                if (!targets.length) {
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
                    for (const row of rows) targets.push(row.el);
                }
                const output = [];
                const used = new Set();
                for (const target of targets) {
                    const rect = target.getBoundingClientRect();
                    const signature = normalize(target.innerText || target.textContent).slice(0, 220);
                    const positionKey = `pos:${Math.round(rect.left)}:${Math.round(rect.top)}:${Math.round(rect.width)}:${Math.round(rect.height)}`;
                    const key = `${positionKey}:${signature}`;
                    if (!signature || seen.includes(signature) || seen.includes(positionKey) || used.has(key) || skip(signature)) continue;
                    used.add(key);
                    output.push({
                        signature,
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
    ) -> str | None:
        targets = self._collect_uncontacted_candidate_targets(page, seen_signatures, max_targets=16)
        for result in targets:
            signature = result["signature"]
            position_key = result["positionKey"]
            if should_skip_candidate_signature and should_skip_candidate_signature(signature):
                seen_signatures.update({signature, position_key})
                return "\n".join([signature, position_key, "skipped_before_click"])
            page.mouse.click(result["x"], result["y"])
            return "\n".join([signature, position_key])
        return None

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
                const nodes = Array.from(document.querySelectorAll('button, a, div[role="button"], span, div, [class*="button"], [class*="btn"]'));
                const candidates = nodes
                    .filter((el) => isVisible(el))
                    .map((el) => ({ el, rect: el.getBoundingClientRect(), text: normalize(el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label')) }))
                    .filter((item) => item.rect.left > 430 && item.rect.top > 80 && item.text && texts.some((text) => item.text.includes(text)))
                    .filter((item) => !excludes.some((text) => item.text.includes(text)))
                    .filter((item) => item.rect.width <= 320 && item.rect.height <= 120)
                    .sort((a, b) => {
                        const aExact = texts.some((text) => a.text === text) ? 0 : 1;
                        const bExact = texts.some((text) => b.text === text) ? 0 : 1;
                        return aExact - bExact || a.rect.top - b.rect.top || a.rect.left - b.rect.left;
                    });
                const item = candidates[0];
                if (!item) return null;
                const x = item.rect.left + item.rect.width / 2;
                const y = item.rect.top + item.rect.height / 2;
                item.el.scrollIntoView({ block: 'center', inline: 'center' });
                item.el.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, clientX: x, clientY: y }));
                item.el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }));
                item.el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }));
                item.el.click();
                return { text: item.text.slice(0, 120), x, y };
            }
        """
        deadline = time.monotonic() + timeout / 1000
        payload = {"texts": texts, "excludes": exclude_texts or []}
        while time.monotonic() < deadline:
            for frame in page.frames:
                try:
                    result = frame.evaluate(script, payload)
                except Exception:
                    continue
                if result:
                    page.mouse.click(result["x"], result["y"])
                    logger.info("已点击聊天详情按钮：{}", result["text"])
                    return True
            page.wait_for_timeout(500)
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
            ["查看简历附件", "查看附件简历", "查看简历", "下载附件简历", "下载简历附件"],
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
        weights = {"phone": 4, "name": 3, "job_title": 2, "gender": 1}
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
            "job_title": data.get("expected_position") or data.get("current_position") or data.get("job_title") or "待识别",
            "phone": data.get("phone") or "待识别",
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
            "job_title": job_title or "待识别",
            "phone": phone_match.group(1) if phone_match else "待识别",
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
                const keywords = /姓名|候选人|男|女|求职|应聘|职位|岗位|电话|手机|1[3-9]\d{9}|岁|经验|本科|专科|硕士|博士|学历|工作年限/;
                const excludes = /快捷回复|发送|表情|聊天记录|附件简历|要附件简历|查看简历附件|下载简历|请输入|复制|已读|未读/;
                const nodes = Array.from(document.querySelectorAll('aside, section, header, article, div, span, p'))
                    .filter((el) => isVisible(el))
                    .map((el) => {
                        const rect = el.getBoundingClientRect();
                        const text = normalize(el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label'));
                        const cls = String(el.className || '');
                        const area = rect.width * rect.height;
                        const rightPanel = rect.left >= Math.max(430, viewportWidth * 0.48) && rect.top >= 50 && rect.top <= 520;
                        const profileClass = /candidate|profile|detail|resume|user|person|talent|card|info/i.test(cls);
                        const keywordScore = (text.match(keywords) || []).length;
                        return { text, cls, x: rect.left, y: rect.top, w: rect.width, h: rect.height, area, rightPanel, profileClass, keywordScore };
                    })
                    .filter((item) => item.text && item.text.length >= 2 && item.text.length <= 500)
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
        for key in ["name", "gender", "job_title", "phone"]:
            if not merged.get(key) or merged.get(key) == "待识别":
                merged[key] = fallback.get(key) or "待识别"
        profile_texts = [text for text in [merged.get("profile_text"), fallback.get("profile_text")] if text]
        merged["profile_text"] = "\n".join(dict.fromkeys("\n".join(profile_texts).splitlines()))
        merged["extractor"] = f"{merged.get('extractor', 'dom')}+fallback"
        return merged

    def _extract_current_candidate_info(self, page: Page, fallback_signature: str = "", resume_file_path: str | None = None) -> dict:
        signature_info = self._parse_candidate_signature(fallback_signature)
        resume_info = self._extract_candidate_info_from_resume_file(resume_file_path, fallback_signature)
        dom_text = self._extract_profile_text_by_dom(page)
        dom_info = self._parse_candidate_info_text(dom_text, fallback_signature, extractor="dom_profile")
        best_info = self._merge_candidate_info(resume_info, dom_info) if resume_info else dom_info
        try:
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
            "候选人信息提取结果：name={}, gender={}, job_title={}, phone={}, extractor={}",
            best_info.get("name"),
            best_info.get("gender"),
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
        on_resume_saved: Callable[[dict], None] | None = None,
        should_skip_resume: Callable[[dict], bool] | None = None,
        should_skip_candidate_signature: Callable[[str], bool] | None = None,
        on_resume_skipped: Callable[[dict], None] | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> list[dict]:
        if not self.state_path.exists():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = open_browser_session(state_path=self.state_path, headless=False)
        results = []
        captured_urls: set[str] = set()
        pending_urls: list[str] = []
        seen_candidates: set[str] = set()

        def can_continue() -> bool:
            return should_continue() if should_continue else True

        def handle_request(request: Request) -> None:
            url = request.url
            if self._is_resume_attachment_download_url(url) and url not in captured_urls:
                captured_urls.add(url)
                pending_urls.append(url)
                print(f"已从网络请求捕获附件下载链接：{url}")

        try:
            session.context.on("request", handle_request)
            page = session.page
            page.goto(target_url or self.home_url, wait_until="domcontentloaded", timeout=30000)
            if not self._open_chat_interface(page):
                raise RuntimeError("未能进入智联聊天界面，请检查页面是否已登录或传入 --url 聊天页面地址。")
            print("\n智联聊天候选人全自动采集已启动：")
            print("1. 自动扫描包含'未联系'的候选人。")
            print("2. 自动点击候选人、'要附件简历'、'查看附件简历'。")
            print("3. 自动捕获附件下载链接并保存 PDF。")
            print(f"4. 目标数量：{max_resumes}，最长运行：{wait_seconds} 秒。\n")

            deadline = time.monotonic() + wait_seconds
            while len(results) < max_resumes and time.monotonic() < deadline:
                if not can_continue():
                    break
                signature = None
                skipped_batch = 0
                while can_continue():
                    skipped_signature = self._click_next_uncontacted_candidate(page, seen_candidates, should_skip_candidate_signature)
                    if not skipped_signature or "skipped_before_click" not in skipped_signature.splitlines():
                        signature = skipped_signature
                        break
                    seen_candidates.update(skipped_signature.splitlines())
                    display_signature = skipped_signature.splitlines()[0]
                    print(f"已快速跳过重复候选人：{display_signature[:80]}")
                    skipped_batch += 1
                    if on_resume_skipped:
                        on_resume_skipped({
                            "platform_code": self.platform_code,
                            "source_url": page.url,
                            "raw_json": {
                                "candidate_signature": display_signature,
                                "candidate_info": self._parse_candidate_signature(display_signature),
                                "attachment": {},
                            },
                            "raw_html_path": None,
                            "content_hash": "",
                        })
                    if skipped_batch >= 16:
                        break
                if not can_continue():
                    break
                if not signature:
                    logger.warning("未找到新的未联系候选人，尝试滚动列表。")
                    self._print_candidate_candidates(page)
                    page.mouse.wheel(0, 900)
                    page.wait_for_timeout(600)
                    continue

                seen_candidates.update(signature.splitlines())
                display_signature = signature.splitlines()[0]
                print(f"已选择候选人：{display_signature[:80]}")
                if should_skip_candidate_signature and should_skip_candidate_signature(display_signature):
                    if on_resume_skipped:
                        on_resume_skipped({
                            "platform_code": self.platform_code,
                            "source_url": page.url,
                            "raw_json": {
                                "candidate_signature": display_signature,
                                "candidate_info": self._parse_candidate_signature(display_signature),
                                "attachment": {},
                            },
                            "raw_html_path": None,
                            "content_hash": "",
                        })
                    page.wait_for_timeout(50)
                    continue
                page.wait_for_timeout(800)
                if not can_continue():
                    break

                if not self._click_request_attachment_resume(page):
                    logger.warning("当前候选人未找到可点击的'要附件简历'按钮，可能已经收到附件，继续尝试点击'查看简历附件'。")
                page.wait_for_timeout(800)
                if not can_continue():
                    break

                if not self._click_view_attachment_resume(page):
                    logger.warning("当前候选人未找到'查看附件简历'按钮，等待可能的自动链接。")
                    self._print_chat_detail_actions(page)

                candidate_deadline = time.monotonic() + per_candidate_wait_seconds
                saved_current_candidate = False
                while time.monotonic() < candidate_deadline and not saved_current_candidate:
                    if not can_continue():
                        break
                    pages = [item for item in session.context.pages if not item.is_closed()]
                    pending_urls.extend(self._find_attachment_urls_from_pages(pages, captured_urls))
                    while pending_urls and len(results) < max_resumes:
                        download_url = pending_urls.pop(0)
                        try:
                            row = self._download_attachment_with_context(
                                session.context,
                                download_url,
                                filename=self._filename_from_download_url(download_url),
                                mode="auto_chat_candidate_click",
                            )
                        except Exception as exc:
                            logger.warning("自动下载附件失败：{}", exc)
                            continue
                        attachment = row["raw_json"].get("attachment", {})
                        candidate_info = self._extract_current_candidate_info(page, display_signature, attachment.get("file_path"))
                        candidate_info["resume_file_name"] = attachment.get("file_name")
                        row["raw_json"]["candidate_signature"] = display_signature
                        row["raw_json"]["candidate_info"] = candidate_info
                        if should_skip_resume and should_skip_resume(row):
                            if on_resume_skipped:
                                on_resume_skipped(row)
                            saved_current_candidate = True
                            break
                        results.append(row)
                        if on_resume_saved:
                            on_resume_saved(row)
                        saved_current_candidate = True
                        attachment = row["raw_json"]["attachment"]
                        print(f"已自动保存第 {len(results)} 份：{attachment['file_path']}")
                        break
                    page.wait_for_timeout(500)

                if not saved_current_candidate:
                    logger.warning("当前候选人在等待时间内未捕获到附件下载链接。")

            print(f"自动采集结束：已保存 {len(results)} 份。")
            return results
        finally:
            session.close()

    def fetch_resume_list(self) -> list[dict]:
        raise NotImplementedError("智联招聘自动简历列表采集将在页面结构确认后实现。")



    def fetch_resume_detail(self, resume_id: str) -> dict:
        raise NotImplementedError("智联招聘自动简历详情采集将在页面结构确认后实现。")

