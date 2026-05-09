import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlparse



from loguru import logger
from playwright.sync_api import BrowserContext, Download, Page, Request, TimeoutError as PlaywrightTimeoutError



from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.core.browser import get_state_path, open_browser_session, save_storage_state
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
        raw_name = (query.get("fileName") or [""])[0]
        if raw_name:
            return f"{safe_filename(raw_name, max_length=80)}.pdf"
        return "resume.pdf"

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
        if ".pdf" not in suggested_filename.lower() and "pdf" in content_type.lower():
            suggested_filename = f"{suggested_filename}.pdf"
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
                    "file_ext": target_path.suffix.lower(),
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


    def _click_next_uncontacted_candidate(self, page: Page, seen_signatures: set[str]) -> str | None:
        script = r"""
            (seen) => {
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
                const used = new Set();
                for (const target of targets) {
                    const rect = target.getBoundingClientRect();
                    const signature = normalize(target.innerText || target.textContent).slice(0, 220);
                    const key = `${Math.round(rect.left)}:${Math.round(rect.top)}:${signature}`;
                    if (!signature || seen.includes(signature) || used.has(key) || skip(signature)) continue;
                    used.add(key);
                    const x = Math.min(410, Math.max(230, rect.left + rect.width * 0.55));
                    const y = rect.top + rect.height / 2;
                    const before = normalize(document.body ? document.body.innerText : '');
                    target.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, clientX: x, clientY: y }));
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }));
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }));
                    target.click();
                    const pointTarget = document.elementFromPoint(x, y);
                    if (pointTarget && pointTarget !== target) {
                        pointTarget.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }));
                        pointTarget.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 }));
                        pointTarget.click();
                    }
                    return { signature, x, y };
                }
                return null;
            }
        """
        for frame in page.frames:
            try:
                result = frame.evaluate(script, list(seen_signatures))
            except Exception:
                continue
            if result:
                page.mouse.click(result["x"], result["y"])
                page.wait_for_timeout(1500)
                return result["signature"]
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

    def auto_click_chat_attachment_resumes(
        self,
        target_url: str | None = None,
        max_resumes: int = 5,
        wait_seconds: int = 900,
        per_candidate_wait_seconds: int = 60,
    ) -> list[dict]:
        if not self.state_path.exists():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = open_browser_session(state_path=self.state_path, headless=False)
        results = []
        captured_urls: set[str] = set()
        pending_urls: list[str] = []
        seen_candidates: set[str] = set()

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
                signature = self._click_next_uncontacted_candidate(page, seen_candidates)
                if not signature:
                    logger.warning("未找到新的未联系候选人，尝试滚动列表。")
                    self._print_candidate_candidates(page)
                    page.mouse.wheel(0, 900)
                    page.wait_for_timeout(3000)
                    continue

                seen_candidates.add(signature)
                print(f"已选择候选人：{signature[:80]}")
                page.wait_for_timeout(3000)

                if not self._click_request_attachment_resume(page):
                    logger.warning("当前候选人未找到可点击的'要附件简历'按钮，可能已经收到附件，继续尝试点击'查看简历附件'。")
                page.wait_for_timeout(2000)

                if not self._click_view_attachment_resume(page):
                    logger.warning("当前候选人未找到'查看附件简历'按钮，等待可能的自动链接。")
                    self._print_chat_detail_actions(page)

                candidate_deadline = time.monotonic() + per_candidate_wait_seconds
                saved_current_candidate = False
                while time.monotonic() < candidate_deadline and not saved_current_candidate:
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
                        row["raw_json"]["candidate_signature"] = signature
                        results.append(row)
                        saved_current_candidate = True
                        attachment = row["raw_json"]["attachment"]
                        print(f"已自动保存第 {len(results)} 份：{attachment['file_path']}")
                        break
                    page.wait_for_timeout(1000)

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

