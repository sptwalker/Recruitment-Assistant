import re
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse



from loguru import logger
from playwright.sync_api import BrowserContext, Download, Error as PlaywrightError, Page, Request, Response, Route, TimeoutError as PlaywrightTimeoutError



from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.core.browser import get_state_path, open_browser_session, save_storage_state
from recruitment_assistant.parsers.pdf_resume_parser import clean_candidate_signature, parse_resume_file
from recruitment_assistant.platforms.base import BasePlatformAdapter
from recruitment_assistant.utils.hash_utils import text_hash
from recruitment_assistant.utils.snapshot_utils import safe_filename, save_text_snapshot



class ZhilianAdapter(BasePlatformAdapter):
    platform_code = "zhilian"
    login_url = "https://passport.zhaopin.com/org/login?bkurl=https%3A%2F%2Frd6.zhaopin.com%2F"
    home_url = "https://rd5.zhaopin.com/"

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
        use_profile = (force_profile or self._has_persistent_profile()) and not force_storage_state
        try:
            if use_profile:
                return open_browser_session(state_path=self.state_path, headless=headless, user_data_dir=self.user_data_dir)
            return open_browser_session(state_path=self.state_path, headless=headless)
        except TypeError:
            return open_browser_session(state_path=self.state_path, headless=headless)

    def _is_login_or_security_page(self, page: Page) -> bool:
        try:
            current_url = page.url.lower()
        except Exception:
            current_url = ""
        try:
            title = page.title().lower()
        except Exception:
            title = ""
        try:
            text = page.locator("body").inner_text(timeout=3000).lower()
        except Exception:
            text = ""
        full_text = " ".join([current_url, title, text])
        invalid_markers = [
            "passport.zhaopin.com",
            "/login",
            "org/login",
            "登录页",
            "扫码登录",
            "账号登录",
            "security verification",
            "验证连接安全性",
            "tencent cloud edgeone",
            "protected by tencent cloud edgeone",
            "请勾选下方复选框",
        ]
        return any(marker in full_text for marker in invalid_markers)

    def _open_authenticated_session(self, target_url: str | None = None, headless: bool = False):
        url = target_url or self.home_url
        attempts = []
        if self._has_persistent_profile():
            attempts.append(("profile", {"force_profile": True}))
        if self.state_path.exists():
            attempts.append(("storage_state", {"force_storage_state": True}))
        if not attempts:
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")
        last_error = None
        for label, kwargs in attempts:
            session = None
            try:
                session = self._open_stateful_session(headless=headless, **kwargs)
                session.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                session.page.wait_for_timeout(5000)
                if not self._is_login_or_security_page(session.page):
                    logger.info("智联自动登录通道可用：{}，当前页面={}", label, session.page.url)
                    return session
                logger.warning("智联自动登录通道无效：{}，当前页面={}，将尝试下一通道。", label, session.page.url)
            except Exception as exc:
                last_error = exc
                logger.warning("智联自动登录通道打开失败：{}，原因={}，将尝试下一通道。", label, exc)
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
        detail = f"最后错误：{last_error}" if last_error else "未进入已登录页面"
        raise RuntimeError(f"智联自动登录未成功：浏览器档案和 JSON 登录态均未进入已登录页面，请重新保存登录态。{detail}")

    def login(self) -> None:
        self.login_manually(wait_seconds=180)

    def login_manually(self, wait_seconds: int = 180, keep_open: bool = False, enter_home: bool = True) -> Path:
        session = self._open_stateful_session(headless=False, force_profile=True)
        try:
            page = session.page
            page.goto(self.login_url, wait_until="domcontentloaded")
            logger.info("请在打开的智联企业端登录页完成人工扫码登录。")
            deadline = time.monotonic() + wait_seconds
            logged_in = False
            while time.monotonic() < deadline:
                try:
                    page.wait_for_timeout(2000)
                    current_url = page.url.lower()
                    if "passport" not in current_url and "login" not in current_url and not self._is_login_or_security_page(page):
                        logged_in = True
                        save_storage_state(session.context, self.state_path)
                        logger.info("检测到智联登录跳转完成：{}", page.url)
                        break
                except PlaywrightError as exc:
                    raise RuntimeError("登录窗口已关闭或登录流程已取消") from exc
            if not logged_in:
                raise RuntimeError("智联登录未完成：仍停留在登录/验证页面，未保存无效登录态。")
            if enter_home:
                try:
                    page.goto(self.home_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(8000)
                    if self._is_login_or_security_page(page):
                        raise RuntimeError("进入系统首页后仍在登录/验证页面，未覆盖已保存登录态。")
                    logged_in = True
                    save_storage_state(session.context, self.state_path)
                    logger.info("已进入智联系统首页：{}", page.url)
                except RuntimeError:
                    raise
                except Exception as exc:
                    logger.warning("登录后进入智联系统首页失败：{}", exc)
            save_storage_state(session.context, self.state_path)
            logger.info("智联招聘登录态已保存：{}", self.state_path)
            if keep_open:
                input("登录态已保存。浏览器将保持打开，按 Enter 后关闭窗口...")
            return self.state_path
        finally:
            session.close()


    def is_logged_in(self, headless: bool = True) -> bool:
        if not self.state_path.exists() and not self.user_data_dir.exists():
            return False
        session = self._open_stateful_session(headless=headless, force_profile=True)

        try:
            page = session.page
            page.goto(self.home_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            current_url = page.url.lower()
            title = page.title().lower()
            content = page.content().lower()
            text = ""
            try:
                text = page.locator("body").inner_text(timeout=5000).lower()
            except Exception:
                text = ""
            full_text = " ".join([current_url, title, content, text])
            if self._is_login_or_security_page(page):
                return False
            valid_markers = ["rd5.zhaopin.com", "rd6.zhaopin.com", "/app/", "app-menu", "智联招聘"]
            return any(marker in full_text for marker in valid_markers)
        except Exception as exc:
            logger.warning("智联招聘登录态检测失败：{}", exc)
            return False
        finally:
            session.close()

    def capture_current_page(self, target_url: str | None = None, wait_seconds: int = 30) -> dict:
        if not self.state_path.exists() and not self._has_persistent_profile():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

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

    def capture_manual_resume_pages(self, target_url: str | None = None, max_pages: int = 5) -> list[dict]:
        if not self.state_path.exists() and not self._has_persistent_profile():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = self._open_stateful_session(headless=False)
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
        website = "智联招聘"
        suffix = source_path.suffix.lower() or str(attachment.get("file_ext") or ".pdf")
        filename_stem = f"{name}-{age}-{education}-{website}-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-{sequence:03d}"
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
                    "mime_type": (
                        "application/pdf" if suffix == ".pdf"
                        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if suffix == ".docx"
                        else "application/msword" if suffix == ".doc"
                        else None
                    ),
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
        if not self.state_path.exists() and not self._has_persistent_profile():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = self._open_stateful_session(headless=False)
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
        lower_content = content[:4096].lower().lstrip()
        if lower_content.startswith((b"<html", b"<?xml")) and (
            b"schemas-microsoft-com:office:word" in lower_content
            or b"microsoft word" in lower_content
            or b"urn:schemas-microsoft-com:office:word" in lower_content
        ):
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
            lower_content = content[:4096].lower()
            return (
                content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
                or (
                    lower_content.lstrip().startswith((b"<html", b"<?xml"))
                    and (
                        b"schemas-microsoft-com:office:word" in lower_content
                        or b"microsoft word" in lower_content
                        or b"urn:schemas-microsoft-com:office:word" in lower_content
                    )
                )
            )
        return False

    def _is_resume_attachment_download_url(self, url: str) -> bool:
        url_lower = (url or "").lower()
        if not url_lower.startswith(("http://", "https://")):
            return False
        host = urlparse(url_lower).netloc
        path_query = f"{urlparse(url_lower).path}?{urlparse(url_lower).query}"
        if "attachment.zhaopin.com" in host and "downloadfiletemporary" in path_query:
            return True
        if "zhaopin.com" not in host:
            return False
        return any(
            token in path_query
            for token in [
                "downloadfiletemporary",
                "downloadfile",
                "downfile",
                "downloadresume",
                "resume/download",
                "resumeattachment",
                "attachment/download",
                "download/attachment",
                "file/download",
                "downloadurl",
            ]
        )

    def _is_resume_attachment_response(self, response: Response) -> bool:
        url = response.url or ""
        if self._is_resume_attachment_download_url(url):
            return True
        if "zhaopin.com" not in url.lower():
            return False
        headers = response.headers or {}
        content_type = (headers.get("content-type") or "").lower()
        disposition = (headers.get("content-disposition") or "").lower()
        if "attachment" in disposition and any(ext in disposition for ext in [".pdf", ".doc", ".docx"]):
            return True
        return any(
            mime in content_type
            for mime in [
                "application/pdf",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/octet-stream",
            ]
        ) and any(token in url.lower() for token in ["resume", "attach", "file", "download"])


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
            timeout=18000,
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
                const attrs = ['href', 'src', 'data-url', 'data-href', 'data-download-url', 'data-file-url', 'data-src', 'title', 'aria-label', 'download'];
                const push = (value) => { if (value) values.push(value); };
                for (const el of Array.from(document.querySelectorAll('a, iframe, embed, object, [href], [src], [data-url], [data-href], [data-download-url], [data-file-url], [data-src], [download], [title], [aria-label]'))) {
                    for (const attr of attrs) {
                        push(el.getAttribute(attr));
                    }
                    push(el.innerText);
                    push(el.textContent);
                }
                const html = document.documentElement ? document.documentElement.innerHTML : '';
                for (const match of html.matchAll(/https?:\\/\\/[^'"<>\\s]+/g)) values.push(match[0]);
                for (const match of html.matchAll(/\/[^'"<>\\s]*(?:downloadFileTemporary|downloadFile|downFile|downloadResume|resumeAttachment|attachment|resume|file\/download|download\/file|downloadUrl)[^'"<>\\s]*/ig)) values.push(match[0]);

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

    def _find_latest_chat_attachment_urls(self, page: Page, captured_urls: set[str], mark_captured: bool = False) -> list[str]:
        urls: list[str] = []
        script = r"""
            () => {
                const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
                const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
                const chatLeft = Math.max(420, Math.round(viewportW * 0.34));
                const bottomTop = Math.max(70, Math.round(viewportH * 0.52));
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const textOf = (el) => normalize([
                    el.innerText,
                    el.textContent,
                    el.getAttribute('title'),
                    el.getAttribute('aria-label'),
                    el.getAttribute('href'),
                    el.getAttribute('download'),
                    el.getAttribute('data-url'),
                    el.getAttribute('data-href'),
                    el.getAttribute('data-download-url'),
                    el.getAttribute('data-file-url'),
                ].filter(Boolean).join(' '));
                const attrs = ['href', 'src', 'data-url', 'data-href', 'data-download-url', 'data-file-url', 'data-src', 'download'];
                const urlLike = /(?:downloadFileTemporary|downloadFile|downFile|downloadResume|resumeAttachment|attachment|file\/download|download\/file|downloadUrl|attachment\.zhaopin\.com)/i;
                const attachText = /查看附件简历|查看简历附件|下载附件简历|下载简历附件|\.pdf|\.docx?|pdf|docx?/i;
                const requestOnly = /已向对方要附件简历|要附件简历|索要附件简历|请求附件简历|获取附件简历|要简历/;
                const candidates = [];
                for (const el of Array.from(document.querySelectorAll('a, button, [role="button"], [tabindex], [download], [href], [src], [data-url], [data-href], [data-download-url], [data-file-url], [class*="file"], [class*="attach"], [class*="resume"], [class*="message"], [class*="bubble"], div, span, p, li, section, article'))) {
                    if (!isVisible(el)) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.left <= chatLeft || rect.top <= bottomTop || rect.top >= viewportH - 8 || rect.width > 920 || rect.height > 380) continue;
                    const text = textOf(el);
                    if (!text || requestOnly.test(text) || (!attachText.test(text) && !urlLike.test(text))) continue;
                    const values = [];
                    for (const attr of attrs) {
                        const value = el.getAttribute(attr);
                        if (value) values.push(value);
                    }
                    for (const match of text.matchAll(/https?:\/\/[^'"<>\s]+/g)) values.push(match[0]);
                    for (const match of text.matchAll(/\/[^'"<>\s]*(?:downloadFileTemporary|downloadFile|downFile|downloadResume|resumeAttachment|attachment|file\/download|download\/file|downloadUrl)[^'"<>\s]*/ig)) values.push(match[0]);
                    if (!values.length) continue;
                    candidates.push({ top: rect.top, left: rect.left, values });
                }
                candidates.sort((a, b) => b.top - a.top || a.left - b.left);
                return candidates.flatMap((item) => item.values).slice(0, 12);
            }
        """
        if page.is_closed():
            return urls
        page_values: list[str] = []
        for frame in page.frames:
            try:
                page_values.extend(frame.evaluate(script))
            except Exception:
                continue
        for url in page_values:
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
                print(f"已从当前候选人最新聊天区域捕获附件下载链接：{url}")
        return urls



    def auto_capture_chat_attachment_resumes(
        self, target_url: str | None = None, max_resumes: int = 5, wait_seconds: int = 600
    ) -> list[dict]:
        if not self.state_path.exists() and not self._has_persistent_profile():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = self._open_stateful_session(headless=False)
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
        if not self.state_path.exists() and not self._has_persistent_profile():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = self._open_stateful_session(headless=False)
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
                const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
                const minTop = 220;
                const maxBottom = Math.max(minTop + 80, viewportH - 12);
                const inViewport = (rect) => rect.top >= minTop && rect.bottom <= maxBottom && (rect.top + rect.height / 2) >= minTop && (rect.top + rect.height / 2) <= maxBottom;
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && inViewport(rect) && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
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
                        if (rect.left < 170 || rect.left > 215 || rect.top < 220 || rect.width > 34 || rect.height > 34 || !inViewport(rect)) return false;
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
                        item.rect.left >= 170 && item.rect.left <= 440 && item.rect.top >= 220 && inViewport(item.rect) &&
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
                    if (!signature || seen.includes(signature) || used.has(key) || skip(signature) || !inViewport(rect)) continue;
                    const clickX = Math.min(410, Math.max(230, rect.left + rect.width * 0.55));
                    const clickY = rect.top + rect.height / 2;
                    if (clickY < minTop || clickY > maxBottom) continue;
                    used.add(key);
                    output.push({
                        signature,
                        stableId,
                        positionKey,
                        x: clickX,
                        y: clickY,
                        viewportH,
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
        skip_samples: list[str] = []
        for result in targets:
            signature = result["signature"]
            normalized_signature = re.sub(r"\s+", " ", signature).strip()
            identity_key = self._candidate_identity_key(signature)
            if normalized_signature in emitted_skips or identity_key in emitted_skips:
                continue
            position_key = result["positionKey"]
            stable_id = result.get("stableId") or ""
            skip_hit = should_skip_candidate_signature(signature) if should_skip_candidate_signature else False
            internal_seen_hit = bool(not skip_hit and identity_key in seen_signatures)
            logger.info(
                "点击前候选人判定：skip_hit={}，internal_seen={}，stable_id={}，identity_key={}，position={}，click=({},{})，signature={}",
                skip_hit,
                internal_seen_hit,
                stable_id,
                identity_key,
                position_key,
                result.get("x"),
                result.get("y"),
                signature[:120],
            )
            if skip_hit or internal_seen_hit:
                emitted_skips.add(normalized_signature)
                emitted_skips.add(identity_key)
                seen_signatures.add(signature)
                seen_signatures.add(identity_key)
                if len(skip_samples) < 3:
                    skip_samples.append(f"{identity_key or '空'}|{signature[:40]}")

                if skip_hit:
                    skipped_count += 1
                    print(f"已快速跳过重复候选人：{signature[:80]}")
                    if on_skipped_signature:
                        on_skipped_signature(signature)
                else:
                    logger.info("已内部略过本轮已处理候选人，不计入跳过统计：{}", signature[:80])
                continue
            page.mouse.click(result["x"], result["y"])
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if skipped_count:
                logger.info("本轮扫描已批量跳过重复候选人 {} 位，耗时 {}ms。", skipped_count, elapsed_ms)
            clicked_signature = signature
            return {
                "status": "clicked",
                "signature": clicked_signature,
                "skipped_count": skipped_count,
                "elapsed_ms": elapsed_ms,
                "target_count": len(targets),
                "click_x": result.get("x"),
                "click_y": result.get("y"),
                "position_key": position_key,
                "stable_id": stable_id,
                "identity_key": identity_key,
                "skip_samples": skip_samples,
            }
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if skipped_count:
            logger.info("本轮扫描候选人均为重复，已跳过 {} 位，耗时 {}ms。", skipped_count, elapsed_ms)
            return {"status": "skipped_only", "signature": "", "skipped_count": skipped_count, "elapsed_ms": elapsed_ms, "target_count": len(targets), "skip_samples": skip_samples}
        return {"status": "not_found", "signature": "", "skipped_count": 0, "elapsed_ms": elapsed_ms, "target_count": len(targets), "skip_samples": skip_samples}

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

    def _candidate_identity_key(self, signature: str) -> str:
        name, _job_title = clean_candidate_signature(signature or "")
        name = self._clean_candidate_name(name or signature or "")
        text = re.sub(r"\s+", " ", str(signature or "")).strip()
        age_match = re.search(r"(\d{2})\s*岁", text)
        education_match = re.search(r"(博士|硕士|本科|大专|专科|高中|中专)", text)
        if name and name != "待识别":
            parts = ["candidate", name]
            if age_match:
                parts.append(age_match.group(1))
            if education_match:
                parts.append(education_match.group(1))
            return ":".join(parts)
        normalized = re.sub(r"^(?:\d+|[一二三四五六七八九十]+)[\.、\)）\s]+", "", text)
        normalized = re.sub(r"(已向对方要附件简历|这是我的附件简历|附件简历[，,。 ]*请查收|要附件简历|查看附件简历|下载附件简历)", " ", normalized)
        return "sig:" + re.sub(r"\s+", " ", normalized).strip()[:80]

    def _extract_chat_detail_text(self, page: Page) -> str:
        script = r"""
            () => {
                const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
                const chatLeft = Math.max(420, Math.round(viewportW * 0.34));
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const blocks = Array.from(document.querySelectorAll('main, section, article, aside, div'))
                    .filter((el) => isVisible(el))
                    .map((el) => ({ el, rect: el.getBoundingClientRect(), text: normalize(el.innerText || el.textContent) }))
                    .filter((item) => item.rect.left >= chatLeft && item.rect.top >= 60 && item.rect.width >= 240 && item.rect.height >= 120 && item.text.length >= 2)
                    .sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));
                const best = blocks[0];
                return best ? best.text.slice(0, 5000) : '';
            }
        """
        texts = []
        for frame in page.frames:
            try:
                text = frame.evaluate(script)
            except Exception:
                continue
            if text:
                texts.append(str(text))
        return "\n".join(texts)

    def _wait_candidate_detail_switched(
        self,
        page: Page,
        candidate_signature: str,
        timeout_ms: int = 5000,
        previous_detail_text: str = "",
    ) -> dict:
        signature_info = self._parse_candidate_signature(candidate_signature)
        expected_name = signature_info.get("name") or ""
        signature_text = re.sub(r"\s+", " ", str(candidate_signature or "")).strip()
        age_match = re.search(r"(\d{2})\s*岁", signature_text)
        education_match = re.search(r"(博士|硕士|本科|大专|专科|高中|中专)", signature_text)
        expected_age = age_match.group(1) if age_match else ""
        expected_education = education_match.group(1) if education_match else ""
        reliable_tokens = [
            token for token in [expected_name, expected_age, expected_education]
            if token and token != "待识别" and len(token) >= 2
        ]
        previous_compact = re.sub(r"\s+", "", previous_detail_text or "")
        before_hash = text_hash(previous_compact)[:10] if previous_compact else "空"

        def valid_detail_signal(detail_text: str) -> bool:
            normalized = re.sub(r"\s+", " ", detail_text or "").strip()
            compact = re.sub(r"\s+", "", normalized)
            if not compact:
                return False
            button_only = re.sub(r"查看详情|要附件简历|已向对方要附件简历|查看附件简历|下载附件简历|查看简历附件|下载简历附件|设置备注|不合适|\s+", "", normalized)
            if not button_only:
                return False
            if expected_name and expected_name != "待识别" and expected_name in compact:
                return True
            if expected_age and f"{expected_age}岁" in compact:
                return True
            if expected_education and expected_education in compact:
                return True
            return bool(
                re.search(r"\d{2}\s*岁", normalized)
                or re.search(r"博士|硕士|研究生|本科|大专|专科|高中|中专", normalized)
                or re.search(r"期望[:：].{1,80}·.{1,40}·.{1,40}", normalized)
                or re.search(r"1[3-9]\d(?:\s*\d){8}", normalized)
                or re.search(r"查看电话|交换电话|电话", normalized)
            )

        if self._dismiss_violation_candidate_dialog(page):
            return {
                "switched": False,
                "reason": "violation_dialog",
                "expected_name": expected_name,
                "expected_age": expected_age,
                "expected_education": expected_education,
                "matched": 0,
                "before_hash": before_hash,
                "after_hash": "空",
                "detail_preview": "",
            }
        deadline = time.monotonic() + max(1, timeout_ms) / 1000
        last_text = ""
        last_matched = 0
        while time.monotonic() < deadline:
            if self._dismiss_violation_candidate_dialog(page):
                return {
                    "switched": False,
                    "reason": "violation_dialog",
                    "expected_name": expected_name,
                    "expected_age": expected_age,
                    "expected_education": expected_education,
                    "matched": last_matched,
                    "before_hash": before_hash,
                    "after_hash": text_hash(re.sub(r"\s+", "", last_text))[:10] if last_text else "空",
                    "detail_preview": re.sub(r"\s+", " ", last_text).strip()[:160],
                }
            last_text = self._extract_chat_detail_text(page)
            compact_text = re.sub(r"\s+", "", last_text)
            after_hash = text_hash(compact_text)[:10] if compact_text else "空"
            if compact_text and previous_compact and compact_text != previous_compact and valid_detail_signal(last_text):
                return {
                    "switched": True,
                    "reason": "detail_text_changed",
                    "expected_name": expected_name,
                    "expected_age": expected_age,
                    "expected_education": expected_education,
                    "matched": last_matched,
                    "before_hash": before_hash,
                    "after_hash": after_hash,
                    "detail_preview": re.sub(r"\s+", " ", last_text).strip()[:160],
                }
            if not reliable_tokens:
                page.wait_for_timeout(800)
                return {
                    "switched": True,
                    "reason": "no_reliable_tokens",
                    "expected_name": expected_name,
                    "expected_age": expected_age,
                    "expected_education": expected_education,
                    "matched": 0,
                    "before_hash": before_hash,
                    "after_hash": after_hash,
                    "detail_preview": re.sub(r"\s+", " ", last_text).strip()[:160],
                }
            matched = 0
            if expected_name and expected_name != "待识别" and expected_name in compact_text:
                matched += 2
            if expected_age and (f"{expected_age}岁" in compact_text or expected_age in compact_text):
                matched += 1
            if expected_education and expected_education in compact_text:
                matched += 1
            last_matched = matched
            if matched >= 2 or (len(reliable_tokens) == 1 and matched >= 1):
                return {
                    "switched": True,
                    "reason": "token_matched",
                    "expected_name": expected_name,
                    "expected_age": expected_age,
                    "expected_education": expected_education,
                    "matched": matched,
                    "before_hash": before_hash,
                    "after_hash": after_hash,
                    "detail_preview": re.sub(r"\s+", " ", last_text).strip()[:160],
                }
            if expected_name and not expected_age and not expected_education:
                page.wait_for_timeout(800)
                dismissed = self._dismiss_violation_candidate_dialog(page)
                refreshed_text = self._extract_chat_detail_text(page)
                refreshed_compact = re.sub(r"\s+", "", refreshed_text)
                refreshed_hash = text_hash(refreshed_compact)[:10] if refreshed_compact else "空"
                name_matched = bool(expected_name and expected_name != "待识别" and expected_name in refreshed_compact)
                detail_changed = bool(refreshed_compact and previous_compact and refreshed_compact != previous_compact and valid_detail_signal(refreshed_text))
                switched = bool(not dismissed and (detail_changed or name_matched))
                return {
                    "switched": switched,
                    "reason": (
                        "violation_dialog" if dismissed else
                        "single_name_changed" if detail_changed else
                        "single_name_matched" if name_matched else
                        "single_name_not_matched"
                    ),
                    "expected_name": expected_name,
                    "expected_age": expected_age,
                    "expected_education": expected_education,
                    "matched": 2 if name_matched else matched,
                    "before_hash": before_hash,
                    "after_hash": refreshed_hash,
                    "detail_preview": re.sub(r"\s+", " ", refreshed_text or last_text).strip()[:160],
                }
            page.wait_for_timeout(250)
        logger.warning(
            "候选人详情切换确认失败：signature={}，expected_name={}，age={}，education={}，matched={}，before_hash={}，detail_text={}",
            signature_text[:120],
            expected_name,
            expected_age,
            expected_education,
            last_matched,
            before_hash,
            re.sub(r"\s+", " ", last_text).strip()[:180],
        )
        compact_text = re.sub(r"\s+", "", last_text)
        return {
            "switched": False,
            "reason": "timeout",
            "expected_name": expected_name,
            "expected_age": expected_age,
            "expected_education": expected_education,
            "matched": last_matched,
            "before_hash": before_hash,
            "after_hash": text_hash(compact_text)[:10] if compact_text else "空",
            "detail_preview": re.sub(r"\s+", " ", last_text).strip()[:160],
        }

    def _get_request_attachment_button_state(self, page: Page) -> str:
        script = r"""
            () => {
                const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
                const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
                const chatLeft = Math.max(420, Math.round(viewportW * 0.34));
                const bottomTop = Math.max(70, Math.round(viewportH * 0.50));
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const allText = (el) => normalize([el.innerText, el.textContent, el.getAttribute('title'), el.getAttribute('aria-label')].filter(Boolean).join(' '));
                const disabledLike = (el) => {
                    const cls = String(el.className || '').toLowerCase();
                    const style = window.getComputedStyle(el);
                    return el.disabled === true || el.getAttribute('disabled') !== null || el.getAttribute('aria-disabled') === 'true' || cls.includes('is-disabled') || /(^|[-_\s])disabled($|[-_\s])/.test(cls) || style.pointerEvents === 'none';
                };
                const inRightBottom = (rect) => rect.left > chatLeft && rect.top > bottomTop && rect.top < viewportH - 8 && rect.width <= 900 && rect.height <= 320;
                const classify = (text) => {
                    if (/查看附件简历|查看简历附件|下载附件简历|下载简历附件/.test(text)) return 'view';
                    if (/已向对方要附件简历|已要附件简历|已索要/.test(text)) return 'already_requested';
                    if (/要附件简历|索要附件简历|请求附件简历|获取附件简历|要简历/.test(text)) return 'request';
                    return '';
                };
                const nodes = Array.from(document.querySelectorAll('button, a, [role="button"], [tabindex], span, div, [class*="button"], [class*="btn"], [class*="attach"], [class*="resume"]'));
                const candidates = [];
                for (const el of nodes) {
                    if (!isVisible(el)) continue;
                    const rect = el.getBoundingClientRect();
                    if (!inRightBottom(rect)) continue;
                    const text = allText(el);
                    const kind = classify(text);
                    if (!kind) continue;
                    candidates.push({ kind, disabled: disabledLike(el), top: rect.top, left: rect.left, area: rect.width * rect.height });
                }
                candidates.sort((a, b) => b.top - a.top || a.area - b.area || a.left - b.left);
                const item = candidates[0];
                if (!item) return 'missing';
                if (item.kind === 'view') return item.disabled ? 'view_disabled' : 'view';
                if (item.kind === 'already_requested') return 'already_requested';
                return item.disabled ? 'disabled' : 'enabled';
            }
        """
        for frame in page.frames:
            try:
                state = frame.evaluate(script)
            except Exception:
                continue
            if state in {"enabled", "disabled", "already_requested", "view", "view_disabled"}:
                return state
        return "missing"

    def _has_disabled_request_attachment_button(self, page: Page) -> bool:
        return self._get_request_attachment_button_state(page) == "disabled"

    def _click_text_in_chat_detail(
        self,
        page: Page,
        texts: list[str],
        timeout: int = 8000,
        exclude_texts: list[str] | None = None,
        double_click: bool = False,
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
                    candidates.push({ el: target, rect, text: combinedText, tag, href, cls, score: exactScore + strongScore + fileScore + tagScore + hrefScore + smallScore + rect.top / 20 });
                }
                candidates.sort((a, b) => b.score - a.score || b.rect.top - a.rect.top || a.rect.left - b.rect.left);
                const item = candidates[0];
                if (!item) return null;
                item.el.scrollIntoView({ block: 'center', inline: 'center' });
                const rect = item.el.getBoundingClientRect();
                const x = rect.left + Math.min(Math.max(rect.width * 0.5, 8), Math.max(rect.width - 8, 8));
                const y = rect.top + Math.min(Math.max(rect.height * 0.5, 8), Math.max(rect.height - 8, 8));
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
                    page.mouse.move(result["x"], result["y"])
                    page.mouse.click(result["x"], result["y"], delay=40)
                    if double_click:
                        page.wait_for_timeout(80)
                        page.mouse.click(result["x"], result["y"], delay=40)
                    page.wait_for_timeout(120)
                    logger.info(
                        "已点击聊天详情按钮：text={} tag={} href={} score={} class={}",
                        result.get("text"),
                        result.get("tag"),
                        result.get("href"),
                        result.get("score"),
                        result.get("cls"),
                    )
                    self._last_chat_detail_click = {
                        "source": "chat_detail_button",
                        "text": result.get("text"),
                        "tag": result.get("tag"),
                        "href": result.get("href"),
                        "score": result.get("score"),
                        "cls": result.get("cls"),
                        "x": result.get("x"),
                        "y": result.get("y"),
                    }
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

    def _has_view_attachment_resume(self, page: Page) -> bool:
        script = r"""
            () => {
                const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
                const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
                const chatLeft = Math.max(420, Math.round(viewportW * 0.34));
                const bottomTop = Math.max(70, Math.round(viewportH * 0.50));
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const textOf = (el) => normalize([el.innerText, el.textContent, el.getAttribute('title'), el.getAttribute('aria-label')].filter(Boolean).join(' '));
                const disabledLike = (el) => {
                    const cls = String(el.className || '').toLowerCase();
                    const style = window.getComputedStyle(el);
                    return el.disabled || el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true' || cls.includes('disabled') || cls.includes('disable') || style.pointerEvents === 'none';
                };
                for (const el of Array.from(document.querySelectorAll('button, a, [role="button"], [tabindex], span, div, [class*="button"], [class*="btn"], [class*="attach"], [class*="resume"]'))) {
                    if (!isVisible(el) || disabledLike(el)) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.left <= chatLeft || rect.top <= bottomTop || rect.top >= viewportH - 8 || rect.width > 900 || rect.height > 320) continue;
                    const text = textOf(el);
                    if (/查看附件简历|查看简历附件|下载附件简历|下载简历附件/.test(text) && !/要附件简历|索要附件简历|请求附件简历|获取附件简历/.test(text)) return true;
                }
                return false;
            }
        """
        for frame in page.frames:
            try:
                if frame.evaluate(script):
                    return True
            except Exception:
                continue
        return False

    def _wait_for_requested_attachment_ready(self, page: Page, wait_seconds: int, can_continue: Callable[[], bool] | None = None) -> bool:
        deadline = time.monotonic() + max(1, wait_seconds)
        while time.monotonic() < deadline:
            if can_continue and not can_continue():
                return False
            if self._has_view_attachment_resume(page):
                return True
            page.wait_for_timeout(500)
        return False

    def _click_attachment_message_card(self, page: Page) -> bool:
        script = r"""
            () => {
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
                    el.getAttribute('data-url'),
                    el.getAttribute('data-href'),
                ].filter(Boolean).join(' '));
                const hasDownloadableAttachmentHint = (text) => /查看简历附件|查看附件简历|下载附件简历|下载简历附件|\.pdf|\.docx?/i.test(text);
                const hasNonDownloadableState = (text) => /简历索要中|附件简历索要中|已向对方要附件简历|要附件简历|索要附件简历|请求附件简历|获取附件简历|要简历|查看详情|这是我的附件简历|附件简历[，,。 ]*请查收/.test(text) && !/查看简历附件|查看附件简历|下载附件简历|下载简历附件|\.pdf|\.doc/i.test(text);
                const hasAttachmentHint = (text) => hasDownloadableAttachmentHint(text) && !hasNonDownloadableState(text);
                const hasRequestOnly = (text) => hasNonDownloadableState(text);
                const clickableAncestor = (el) => {
                    let best = el;
                    let cur = el;
                    for (let i = 0; cur && i < 10; i += 1, cur = cur.parentElement) {
                        if (!isVisible(cur)) continue;
                        const rect = cur.getBoundingClientRect();
                        if (rect.left <= 420 || rect.top <= 70 || rect.width > 880 || rect.height > 360) continue;
                        const tag = cur.tagName ? cur.tagName.toLowerCase() : '';
                        const role = cur.getAttribute('role') || '';
                        const cls = String(cur.className || '').toLowerCase();
                        const style = window.getComputedStyle(cur);
                        const text = allText(cur);
                        if (hasRequestOnly(text)) continue;
                        if (tag === 'a' || tag === 'button' || role === 'button' || typeof cur.onclick === 'function' || cls.includes('file') || cls.includes('attach') || cls.includes('resume') || cls.includes('message') || cls.includes('bubble') || style.cursor === 'pointer' || cur.hasAttribute('tabindex')) {
                            best = cur;
                            if (tag === 'a' || tag === 'button' || hasAttachmentHint(text)) break;
                        }
                    }
                    return best;
                };
                const nodes = Array.from(document.querySelectorAll('a, button, [role="button"], [tabindex], [download], [href], [data-url], [data-href], [class*="file"], [class*="attach"], [class*="resume"], [class*="message"], [class*="bubble"], div, span, p, li, section, article'));
                const candidates = [];
                for (const el of nodes) {
                    if (!isVisible(el)) continue;
                    const ownText = allText(el);
                    if (!hasAttachmentHint(ownText) || hasRequestOnly(ownText)) continue;
                    const target = clickableAncestor(el);
                    const rect = target.getBoundingClientRect();
                    if (rect.left <= 420 || rect.top <= 70 || rect.width > 880 || rect.height > 360) continue;
                    const text = allText(target) || ownText;
                    if (!hasAttachmentHint(text) || hasRequestOnly(text)) continue;
                    const tag = target.tagName ? target.tagName.toLowerCase() : '';
                    const href = target.getAttribute('href') || el.getAttribute('href') || target.getAttribute('data-url') || el.getAttribute('data-url') || '';
                    const cls = String(target.className || '');
                    const score = (href ? 50 : 0) + (tag === 'a' ? 35 : tag === 'button' ? 30 : 0) + (/查看|下载|\.pdf|\.doc/i.test(text) ? 35 : 0) + rect.top / 20;
                    candidates.push({ el: target, rect, text, tag, href, cls, score });
                }
                candidates.sort((a, b) => b.score - a.score || b.rect.top - a.rect.top || a.rect.left - b.rect.left);
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
                return { text: item.text.slice(0, 180), x, y, tag: item.tag, href: item.href || '', cls: String(item.cls || '').slice(0, 80), score: item.score };
            }
        """
        for frame in page.frames:
            try:
                result = frame.evaluate(script)
            except Exception:
                continue
            if result:
                page.mouse.move(result["x"], result["y"])
                page.mouse.click(result["x"], result["y"], click_count=2, delay=80)
                logger.info(
                    "已点击附件消息卡片：text={} tag={} href={} score={} class={}",
                    result.get("text"),
                    result.get("tag"),
                    result.get("href"),
                    result.get("score"),
                    result.get("cls"),
                )
                self._last_chat_detail_click = {
                    "source": "attachment_message_card",
                    "text": result.get("text"),
                    "tag": result.get("tag"),
                    "href": result.get("href"),
                    "score": result.get("score"),
                    "cls": result.get("cls"),
                    "x": result.get("x"),
                    "y": result.get("y"),
                }
                return True
        return False

    def _click_bottom_view_attachment_resume(self, page: Page) -> bool:
        script = r"""
            () => {
                const viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
                const viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
                const chatLeft = Math.max(420, Math.round(viewportW * 0.34));
                const bottomTop = Math.max(70, Math.round(viewportH * 0.54));
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
                const hasViewAttachment = (text) => /查看附件简历|查看简历附件|下载附件简历|下载简历附件/.test(text);
                const requestOnly = (text) => /已向对方要附件简历|要附件简历|索要附件简历|请求附件简历|获取附件简历|要简历/.test(text) && !/查看|下载/.test(text);
                const disabledLike = (el) => {
                    const cls = String(el.className || '').toLowerCase();
                    const ariaDisabled = el.getAttribute('aria-disabled') === 'true';
                    const disabledAttr = el.disabled || el.hasAttribute('disabled');
                    const style = window.getComputedStyle(el);
                    return disabledAttr || ariaDisabled || cls.includes('disabled') || cls.includes('disable') || style.pointerEvents === 'none';
                };
                const inRightBottom = (rect) => rect.left > chatLeft && rect.top > bottomTop && rect.top < viewportH - 12 && rect.width <= 900 && rect.height <= 320;
                const clickableAncestor = (el) => {
                    let best = el;
                    let cur = el;
                    for (let i = 0; cur && i < 8; i += 1, cur = cur.parentElement) {
                        if (!isVisible(cur)) continue;
                        const rect = cur.getBoundingClientRect();
                        if (!inRightBottom(rect)) continue;
                        const tag = cur.tagName ? cur.tagName.toLowerCase() : '';
                        const role = cur.getAttribute('role') || '';
                        const cls = String(cur.className || '').toLowerCase();
                        const style = window.getComputedStyle(cur);
                        if (tag === 'button' || tag === 'a' || role === 'button' || typeof cur.onclick === 'function' || cls.includes('button') || cls.includes('btn') || cls.includes('link') || style.cursor === 'pointer' || cur.hasAttribute('tabindex')) {
                            best = cur;
                            if (tag === 'button' || tag === 'a') break;
                        }
                    }
                    return best;
                };
                const nodes = Array.from(document.querySelectorAll('button, a, [role="button"], [tabindex], [download], [href], span, div, p, [class*="button"], [class*="btn"], [class*="link"], [class*="attach"], [class*="resume"]'));
                const candidates = [];
                for (const el of nodes) {
                    if (!isVisible(el)) continue;
                    const ownText = allText(el);
                    if (!hasViewAttachment(ownText) || requestOnly(ownText)) continue;
                    const target = clickableAncestor(el);
                    if (!isVisible(target) || disabledLike(target)) continue;
                    const rect = target.getBoundingClientRect();
                    if (!inRightBottom(rect)) continue;
                    const text = allText(target) || ownText;
                    if (!hasViewAttachment(text) || requestOnly(text)) continue;
                    let cur = target;
                    let disabled = false;
                    for (let i = 0; cur && i < 4; i += 1, cur = cur.parentElement) {
                        if (disabledLike(cur)) {
                            disabled = true;
                            break;
                        }
                    }
                    if (disabled) continue;
                    const tag = target.tagName ? target.tagName.toLowerCase() : '';
                    const href = target.getAttribute('href') || el.getAttribute('href') || '';
                    const cls = String(target.className || '');
                    const exactScore = /查看附件简历|查看简历附件/.test(text) ? 90 : 60;
                    const clickableScore = tag === 'button' ? 35 : tag === 'a' ? 32 : target.getAttribute('role') === 'button' ? 26 : 0;
                    const bottomScore = Math.max(0, Math.round(rect.top - bottomTop) / 20);
                    candidates.push({ el: target, rect, text, tag, href, cls, score: exactScore + clickableScore + bottomScore });
                }
                candidates.sort((a, b) => b.score - a.score || b.rect.top - a.rect.top || a.rect.left - b.rect.left);
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
                return { text: item.text.slice(0, 180), x, y, tag: item.tag, href: item.href || '', cls: String(item.cls || '').slice(0, 80), score: item.score };
            }
        """
        for frame in page.frames:
            try:
                result = frame.evaluate(script)
            except Exception:
                continue
            if result:
                page.mouse.move(result["x"], result["y"])
                page.mouse.down()
                page.wait_for_timeout(80)
                page.mouse.up()
                page.wait_for_timeout(120)
                page.mouse.click(result["x"], result["y"], click_count=2, delay=80)
                logger.info(
                    "已优先点击右侧底部查看附件简历按钮：text={} tag={} href={} score={} class={}",
                    result.get("text"),
                    result.get("tag"),
                    result.get("href"),
                    result.get("score"),
                    result.get("cls"),
                )
                self._last_chat_detail_click = {
                    "source": "bottom_view_attachment_button",
                    "text": result.get("text"),
                    "tag": result.get("tag"),
                    "href": result.get("href"),
                    "score": result.get("score"),
                    "cls": result.get("cls"),
                    "x": result.get("x"),
                    "y": result.get("y"),
                }
                return True
        return False

    def _click_view_attachment_resume(self, page: Page) -> bool:
        if self._click_bottom_view_attachment_resume(page):
            return True
        clicked = self._click_text_in_chat_detail(
            page,
            ["查看简历附件", "查看附件简历", "下载附件简历", "下载简历附件"],
            timeout=5000,
            exclude_texts=["已向对方要附件简历", "要附件简历", "索要附件简历", "请求附件简历", "获取附件简历", "要简历"],
        )
        if clicked:
            return True
        return self._click_attachment_message_card(page)

    def _dismiss_violation_candidate_dialog(self, page: Page) -> bool:
        script = r"""
            () => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const violationText = /求职者存在违规行为|系统已为您自动屏蔽|违规|违法|风险|警告|异常|无法查看|无法沟通|限制/;
                const actionText = /知道了|我知道了|确定|确认|关闭/;
                const dialogNodes = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"], .ant-modal, .el-dialog, .modal, div'))
                    .filter((el) => isVisible(el))
                    .map((el) => ({ el, rect: el.getBoundingClientRect(), text: normalize(el.innerText || el.textContent) }))
                    .filter((item) => item.rect.width >= 220 && item.rect.height >= 100 && item.text.length <= 1200 && violationText.test(item.text) && actionText.test(item.text));
                dialogNodes.sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
                const dialog = dialogNodes[0];
                if (!dialog) return null;
                const buttons = Array.from(dialog.el.querySelectorAll('button, a, [role="button"], [tabindex], span, div'))
                    .filter((el) => isVisible(el))
                    .map((el) => ({ el, rect: el.getBoundingClientRect(), text: normalize(el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label')) }))
                    .filter((item) => /^(知道了|我知道了|确定|确认|关闭)$/.test(item.text) || /知道了/.test(item.text));
                buttons.sort((a, b) => {
                    const aKnow = /知道了/.test(a.text) ? 1 : 0;
                    const bKnow = /知道了/.test(b.text) ? 1 : 0;
                    return bKnow - aKnow || b.rect.top - a.rect.top || a.rect.left - b.rect.left;
                });
                const button = buttons[0];
                if (!button) return null;
                button.el.scrollIntoView({ block: 'center', inline: 'center' });
                const rect = button.el.getBoundingClientRect();
                const x = rect.left + rect.width / 2;
                const y = rect.top + rect.height / 2;
                for (const type of ['pointerover', 'mouseover', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    const eventOptions = { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: type.includes('down') ? 1 : 0, pointerType: 'mouse' };
                    const event = type.startsWith('pointer') ? new PointerEvent(type, eventOptions) : new MouseEvent(type, eventOptions);
                    button.el.dispatchEvent(event);
                }
                if (typeof button.el.click === 'function') button.el.click();
                return { dialogText: dialog.text.slice(0, 180), buttonText: button.text, x, y };
            }
        """
        for frame in page.frames:
            try:
                result = frame.evaluate(script)
            except Exception:
                continue
            if result:
                page.mouse.click(result["x"], result["y"])
                logger.info("已关闭违规求职者警告窗口：button={} text={}", result.get("buttonText"), result.get("dialogText"))
                return True
        return False


    def _clean_candidate_name(self, value: str) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return ""
        exact_noise = {"请查收", "这是我的", "附件简历", "简历附件", "要附件简历", "查看附件简历", "下载附件简历"}
        job_noise_tokens = [
            "运营", "电商", "视频", "剪辑", "主播", "客服", "销售", "产品", "设计", "开发", "工程师",
            "经理", "主管", "专员", "顾问", "助理", "总监", "算法", "测试", "前端", "后端",
            "架构", "实施", "运维", "财务", "出纳", "法务", "分析师", "采购", "招聘", "人事", "行政",
        ]
        stop_tokens = [
            "沟通", "聊天", "附件", "简历", "查看", "下载", "电话", "手机号", "求职", "职位", "岗位",
            "未读", "已读", "在线", "打招呼", "要附件", "本科", "专科", "硕士", "博士", "经验",
            "岁", "性别", "工作", "学历", "平台", "快捷", "发送", "复制", "不合适", "约面试",
            "设置备注", "已向对方要附件简历", "待识别", "请查收", "这是我的", "新招呼", "快速处理",
            *job_noise_tokens,
        ]
        text = re.sub(r"(姓名|候选人|联系人)[:：]", " ", text)
        text = re.sub(r"^(?:\d+|[一二三四五六七八九十]+)[\.、\)）\s]+", "", text)
        for part in re.split(r"[｜|/\\,，;；:：\n\r\t ]+", text):
            part = re.sub(r"^(?:\d+|[一二三四五六七八九十]+)[\.、\)）\s]+", "", part)
            part = re.sub(r"^[A-Za-z](?=[\u4e00-\u9fa5]{2,4}$)", "", part)
            part = part.strip(" ·-—_()（）[]【】")
            if not part or part in exact_noise or any(token in part for token in stop_tokens):
                continue
            if re.fullmatch(r"[\u4e00-\u9fa5]{1,3}(?:先生|女士)", part):
                return part
            if re.fullmatch(r"男|女|男性|女性", part) or re.search(r"\d|岁|年", part):
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
            "架构", "实施", "运维", "财务", "出纳", "法务", "分析师", "需求分析", "策划", "企划", "营销", "品牌",
            "市场", "编导", "导演", "剪辑", "摄像", "摄影", "视频", "深度学习", "图像识别", "图像处理", "机器视觉",
            "Golang", "Go开发", "后台开发", "后端开发", "玩具设计", "动画设计", "商业/经营分析", "经营分析", "质量管理",
            "质量测试", "移动产品经理", "美术设计师", "视觉设计", "电气工程师", "电商运营", "国内电商运营",
        ]
        company_noise = ["有限公司", "分公司", "集团", "科技", "公司", "企业", "中心", "事业部", "工作室", "系统集成"]
        section_noise = ["工作经历", "项目经历", "教育经历", "实习经历", "培训经历", "校园经历"]
        direction_noise = ["AI方向", "ai方向", "A I方向", "方向"]
        text = re.sub(r"[（(][^（）()]{0,24}方向[）)]", "", text)
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
            if any(token in part for token in company_noise + section_noise + direction_noise):
                continue
            if any(keyword.lower() in part.lower() for keyword in job_keywords):
                text = part
                break
        if not text:
            text = candidates[-1] if candidates else ""
        if not text or text == candidate_name:
            return ""
        has_job_keyword = any(keyword.lower() in text.lower() for keyword in job_keywords)
        if not has_job_keyword and self._clean_candidate_name(text) == text:
            return ""
        if any(token in text for token in ["聊天", "沟通", "附件", "简历", "查看", "下载", "电话", "手机", "未读", "已读", "快捷回复", "设置备注", "不合适"]):
            return ""
        if any(token in text for token in company_noise + section_noise + direction_noise):
            return ""
        return text if 2 <= len(text) <= 40 else ""

    def _parse_candidate_signature(self, signature: str) -> dict:
        raw_signature = re.sub(r"\s+", " ", str(signature or "")).strip()
        direct_name = ""
        direct_match = re.match(r"^(?:\d+|[一二三四五六七八九十]+)?[\.、\)）\s]*(?:[A-Za-z])?([\u4e00-\u9fa5]{2,4})(?=\s|$)", raw_signature)
        if direct_match:
            direct_name = self._clean_candidate_name(direct_match.group(1))
        name, job_title = clean_candidate_signature(signature or "")
        name = direct_name or self._clean_candidate_name(name or "")
        job_title = self._clean_candidate_job_title(job_title or raw_signature, name)
        return {"name": name or "待识别", "job_title": job_title or "待识别", "extractor": "candidate_signature"}

    def _is_unknown_or_noise(self, value: str) -> bool:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return not text or text == "待识别" or any(
            token in text
            for token in ["设置备注", "不合适", "已向对方要附件简历", "要附件简历", "查看附件简历", "请查收", "这是我的", "附件简历"]
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

    def _extract_profile_summary_fields(self, text: str, expected_name: str = "") -> dict:
        merged = re.sub(r"\s+", " ", str(text or "")).strip()
        result: dict[str, str] = {}
        if not merged:
            return result
        degree_pattern = r"博士|硕士|研究生|本科|大专|专科|高中|中专"
        name_pattern = re.escape(expected_name) if expected_name and expected_name != "待识别" else r"[\u4e00-\u9fa5]{2,4}|[A-Za-z][A-Za-z .·-]{1,30}"
        summary_match = re.search(
            rf"(?P<name>{name_pattern})\s+(?P<age>\d{{2}})\s*岁\s*(?P<education>{degree_pattern})?\s*(?P<status>[^\s，,。；;·|｜]{{0,24}})",
            merged,
        )
        if summary_match:
            result["name"] = self._clean_candidate_name(summary_match.group("name")) or summary_match.group("name")
            result["age"] = f"{summary_match.group('age')}岁"
            education = summary_match.group("education") or ""
            if education == "研究生":
                education = "硕士"
            elif education == "专科":
                education = "大专"
            if education:
                result["education"] = education
                result["highest_degree"] = education
            status_text = summary_match.group("status") or ""
            if status_text:
                result["resignation_status"] = self._extract_resignation_status_from_text(status_text) or status_text.strip(" -—")
        expectation_match = re.search(
            r"期望[:： ]*\s*(?P<city>[^·\n\r]{1,40})\s*·\s*(?P<job>[^·\n\r,，；;]{2,40})\s*·\s*(?P<salary>[^·\n\r,，；;]{2,40})",
            merged,
        )
        if expectation_match:
            city = expectation_match.group("city").strip()
            if city:
                result["expected_city"] = city
            job_title = self._clean_candidate_job_title(expectation_match.group("job"), result.get("name") or expected_name)
            if job_title:
                result["job_title"] = job_title
            salary = re.sub(r"\s+", "", expectation_match.group("salary").strip())
            if salary:
                result["salary_expectation"] = salary
        phone_match = re.search(r"(?<!\d)(1[3-9]\d(?:\s*\d){8})(?!\d)", merged)
        if phone_match:
            result["phone"] = re.sub(r"\D", "", phone_match.group(1))
        if re.search(r"性别[:： ]*男|(^|[^\u4e00-\u9fa5])男([^\u4e00-\u9fa5]|$)", merged):
            result["gender"] = "男"
        elif re.search(r"性别[:： ]*女|(^|[^\u4e00-\u9fa5])女([^\u4e00-\u9fa5]|$)", merged):
            result["gender"] = "女"
        return result

    def _extract_education_from_text(self, text: str) -> str:
        summary = self._extract_profile_summary_fields(text)
        if summary.get("education"):
            return summary["education"]
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

    def _extract_expected_info_from_text(self, text: str, expected_name: str = "") -> dict:
        merged = re.sub(r"\s+", " ", str(text or "")).strip()
        result: dict[str, str] = {}
        if not merged:
            return result
        expectation_match = re.search(
            r"期望[:： ]*\s*(?P<city>[^·\n\r]{1,40})\s*·\s*(?P<job>[^·\n\r,，；;]{2,40})\s*·\s*(?P<salary>[^·\n\r,，；;]{2,40})",
            merged,
        )
        if not expectation_match:
            return result
        city = expectation_match.group("city").strip()
        job_title = self._clean_candidate_job_title(expectation_match.group("job"), expected_name)
        salary = re.sub(r"\s+", "", expectation_match.group("salary").strip())
        if city:
            result["expected_city"] = city
        if job_title:
            result["job_title"] = job_title
        if salary:
            result["salary_expectation"] = salary
        result["extractor"] = "expected_line"
        return result

    def _parse_candidate_info_text(self, source_text: str, fallback_signature: str = "", extractor: str = "dom_fallback") -> dict:
        lines = [re.sub(r"\s+", " ", line).strip() for line in str(source_text or "").splitlines()]
        lines = [line for line in lines if line and len(line) <= 220]
        merged = " ".join(lines)
        summary_fields = self._extract_profile_summary_fields(merged)
        summary_fields["profile_text"] = "\n".join(lines)
        summary_fields["extractor"] = extractor
        for key in ["name", "gender", "age", "education", "highest_degree", "job_title", "phone", "resignation_status", "salary_expectation"]:
            if not summary_fields.get(key):
                summary_fields[key] = "待识别"
        if summary_fields.get("education") != "待识别" and summary_fields.get("highest_degree") == "待识别":
            summary_fields["highest_degree"] = summary_fields["education"]
        return summary_fields

    def _extract_top_summary_text_by_dom(self, page: Page) -> str:
        script = r"""
            () => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
                };
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1440;
                const rightLeft = Math.max(420, viewportWidth * 0.42);
                const summaryCore = /\d{2}\s*岁|博士|硕士|研究生|本科|大专|专科|高中|中专|离职|在职|期望[:：]|1[3-9]\d(?:\s*\d){8}/;
                const excludes = /聊天记录|快捷回复|发送|表情|请输入|已读|未读|要附件简历|查看附件简历|下载简历|工作经历|项目经历|教育经历|自我评价|求职信/;
                const nodes = Array.from(document.querySelectorAll('aside, section, header, article, div'))
                    .filter((el) => isVisible(el))
                    .map((el) => {
                        const rect = el.getBoundingClientRect();
                        const text = normalize(el.innerText || el.textContent || el.getAttribute('title') || el.getAttribute('aria-label'));
                        const cls = String(el.className || '');
                        const area = rect.width * rect.height;
                        const topSummaryZone = rect.left >= rightLeft && rect.top >= 40 && rect.top <= 360 && rect.width >= 260 && rect.height >= 36 && rect.height <= 360;
                        const classScore = /candidate|profile|detail|resume|user|person|talent|card|info|basic|summary/i.test(cls) ? 30 : 0;
                        const textScore = (summaryCore.test(text) ? 50 : 0) + (/期望[:：].*·.*·/.test(text) ? 45 : 0) + (/1[3-9]\d(?:\s*\d){8}/.test(text) ? 35 : 0);
                        return { el, rect, text, cls, area, topSummaryZone, score: classScore + textScore + Math.max(0, 40 - rect.top / 10) + Math.min(area / 12000, 20) };
                    })
                    .filter((item) => item.topSummaryZone && item.text && item.text.length >= 4 && item.text.length <= 1200 && !excludes.test(item.text) && summaryCore.test(item.text));
                nodes.sort((a, b) => b.score - a.score || a.rect.top - b.rect.top || b.area - a.area);
                const best = nodes[0];
                if (!best) return '';
                const parts = [];
                const seen = new Set();
                const add = (text) => {
                    const line = normalize(text);
                    if (!line || seen.has(line) || line.length > 260 || excludes.test(line)) return;
                    seen.add(line);
                    parts.push(line);
                };
                add(best.text);
                for (const child of Array.from(best.el.querySelectorAll('div, span, p, li')).filter(isVisible)) {
                    add(child.innerText || child.textContent || child.getAttribute('title') || child.getAttribute('aria-label'));
                    if (parts.length >= 16) break;
                }
                return parts.join('\n');
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
        summary_text = self._extract_top_summary_text_by_dom(page)
        signature_info = self._parse_candidate_signature(fallback_signature) if fallback_signature else {}
        expected_name = signature_info.get("name") or ""
        top_summary_info = self._parse_candidate_info_text(summary_text, "", extractor="top_summary")
        best_info = dict(top_summary_info)
        detail_text = self._extract_chat_detail_text(page)
        detail_summary_text = re.split(r"工作经历|项目经历|教育经历|自我评价|求职信", detail_text or "", maxsplit=1)[0]
        detail_summary_info = self._parse_candidate_info_text(detail_summary_text, "", extractor="detail_summary_prefix")
        summary_expected_info = self._extract_expected_info_from_text(summary_text, expected_name)
        detail_expected_info = self._extract_expected_info_from_text(detail_summary_text or detail_text, expected_name)
        for key in ["expected_city", "salary_expectation"]:
            if (not best_info.get(key) or best_info.get(key) == "待识别") and detail_summary_info.get(key):
                best_info[key] = detail_summary_info[key]
        expected_source = "none"
        expected_info = {}
        if detail_expected_info.get("job_title"):
            expected_info = detail_expected_info
            expected_source = "detail_expected"
        elif summary_expected_info.get("job_title"):
            expected_info = summary_expected_info
            expected_source = "top_summary_expected"
        elif detail_summary_info.get("job_title") and (best_info.get("job_title") == "待识别" or "期望" not in (summary_text or "")):
            expected_info = detail_summary_info
            expected_source = "detail_summary_prefix"
        if expected_info.get("job_title"):
            best_info["job_title"] = expected_info["job_title"]
        for key in ["expected_city", "salary_expectation"]:
            if expected_info.get(key):
                best_info[key] = expected_info[key]
        profile_texts = [summary_text, detail_summary_text if expected_source != "none" else ""]
        best_info["profile_text"] = "\n".join(dict.fromkeys("\n".join(text for text in profile_texts if text).splitlines())) or best_info.get("profile_text") or ""
        if expected_source != "none" and expected_source != "top_summary_expected":
            best_info["extractor"] = f"top_summary+{expected_source}"
        best_info["resume_file_parsed"] = bool(resume_file_path)
        best_info["signature_checked"] = self._candidate_identity_key(fallback_signature) or re.sub(r"\s+", " ", str(fallback_signature or "")).strip()
        logger.info(
            "候选人顶部摘要预览：{}",
            re.sub(r"\s+", " ", summary_text or best_info.get("profile_text") or "").strip()[:220] or "空",
        )
        logger.info(
            "岗位来源诊断：detail_expected_job={}，top_summary_job={}，detail_prefix_job={}，final_job={}，source={}",
            detail_expected_info.get("job_title") or "待识别",
            top_summary_info.get("job_title") or "待识别",
            detail_summary_info.get("job_title") or "待识别",
            best_info.get("job_title") or "待识别",
            expected_source,
        )
        logger.info(
            "候选人信息提取结果：source=top_summary，name={}, age={}, education={}, job_title={}, phone={}, extractor={}",
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
        if not self.state_path.exists() and not self._has_persistent_profile():
            raise RuntimeError("智联招聘登录态不存在，请先完成登录。")

        session = self._open_authenticated_session(target_url or self.home_url, headless=False)
        results = []
        captured_urls: set[str] = set()
        downloaded_urls: set[str] = set()
        pending_urls: list[dict] = []
        ignored_attachment_urls: set[str] = set()
        failed_download_urls: set[str] = set()
        pending_downloads: list[dict] = []
        bound_download_pages: set[int] = set()
        seen_candidates: set[str] = set()
        seen_content_hashes: dict[str, str] = {}
        last_download_monotonic = 0.0
        active_candidate_started_at = 0.0
        active_candidate_identity = ""
        active_candidate_page_ids: set[int] = set()


        def can_continue() -> bool:
            return should_continue() if should_continue else True

        def diag(message) -> None:
            text = message if isinstance(message, str) else str(message)
            logger.info(text)
            if on_diagnostic:
                on_diagnostic(message)

        def diag_event(
            stage: str,
            action: str = "",
            status: str = "",
            cost_ms: int | float | None = None,
            wait_ms: int | float | None = None,
            candidate: str = "",
            **fields,
        ) -> None:
            payload = {
                "stage": stage,
                "action": action,
                "status": status,
                "cost_ms": int(cost_ms) if cost_ms is not None else None,
                "wait_ms": int(wait_ms) if wait_ms is not None else None,
                "candidate": candidate,
                **fields,
            }
            logger.info("STEP {}.{} | status={} | cost={}ms | wait={}ms | candidate={}", stage, action, status, payload.get("cost_ms"), payload.get("wait_ms"), candidate)
            if on_diagnostic:
                on_diagnostic(payload)


        def handle_request(request: Request) -> None:
            url = request.url
            now = time.monotonic()
            if (
                self._is_resume_attachment_download_url(url)
                and not any(item.get("url") == url for item in pending_urls)
                and url not in downloaded_urls
                and url not in ignored_attachment_urls
                and url not in failed_download_urls
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
                    diag_event(
                        "attachment",
                        "request_event",
                        "ignored",
                        candidate=active_candidate_identity,
                        reason="non_current_page",
                        page_id=page_id,
                        url_hash=text_hash(url)[:10] if text_hash(url) else "空",
                    )
                    return
                add_pending_url(url, now, page_id, dict(request.headers), source="network_request")
                logger.info("已从网络请求捕获当前候选人附件下载链接：{}", url)

        def handle_response(response: Response) -> None:
            now = time.monotonic()
            if not active_candidate_started_at or now < active_candidate_started_at:
                return
            try:
                if not self._is_resume_attachment_response(response):
                    return
            except Exception:
                return
            url = response.url or ""
            if (
                not url
                or any(item.get("url") == url for item in pending_urls)
                or url in downloaded_urls
                or url in ignored_attachment_urls
                or url in failed_download_urls
            ):
                return
            source_page = None
            request_headers = {}
            try:
                source_page = response.request.frame.page
            except Exception:
                source_page = None
            try:
                request_headers = dict(response.request.headers)
            except Exception:
                request_headers = {}
            page_id = id(source_page) if source_page and not source_page.is_closed() else 0
            if active_candidate_page_ids and page_id and page_id not in active_candidate_page_ids:
                diag_event(
                    "attachment",
                    "response",
                    "ignored",
                    candidate=active_candidate_identity,
                    reason="non_current_page",
                    page_id=page_id,
                    url_hash=text_hash(url)[:10] if text_hash(url) else "空",
                )
                return
            add_pending_url(url, now, page_id, request_headers, source="network_response")
            logger.info(
                "附件响应已捕获：candidate={}，status={}，url_hash={}，content_type={}，content_disposition={}",
                active_candidate_identity or "空",
                response.status,
                text_hash(url)[:10] if text_hash(url) else "空",
                (response.headers or {}).get("content-type", ""),
                (response.headers or {}).get("content-disposition", "")[:80],
            )

        def handle_route(route: Route, request: Request) -> None:

            url = request.url
            now = time.monotonic()
            if self._is_resume_attachment_download_url(url):
                if (
                    active_candidate_started_at
                    and now >= active_candidate_started_at
                    and url not in downloaded_urls
                    and url not in ignored_attachment_urls
                    and url not in failed_download_urls
                ):
                    source_page = None
                    try:
                        source_page = request.frame.page
                    except Exception:
                        source_page = None
                    page_id = id(source_page) if source_page and not source_page.is_closed() else 0
                    if active_candidate_page_ids and page_id and page_id not in active_candidate_page_ids:
                        diag_event(
                            "attachment",
                            "route",
                            "ignored",
                            candidate=active_candidate_identity,
                            reason="non_current_page",
                            page_id=page_id,
                            url_hash=text_hash(url)[:10] if text_hash(url) else "空",
                        )
                        route.continue_()
                        return
                    add_pending_url(url, now, page_id, dict(request.headers), source="route_intercept")
                    logger.info(
                        "附件路由已捕获：candidate={}，url_hash={}",
                        active_candidate_identity or "空",
                        text_hash(url)[:10] if text_hash(url) else "空",
                    )
                    route.continue_()
                    return
                else:
                    diag_event(
                        "attachment",
                        "route",
                        "blocked",
                        candidate=active_candidate_identity,
                        reason="non_current_or_stale",
                        url_hash=text_hash(url)[:10] if text_hash(url) else "空",
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
                    "candidate_identity": active_candidate_identity,
                }
            )
            diag_event(
                "attachment",
                "browser_download",
                "captured",
                candidate=active_candidate_identity,
                filename=download.suggested_filename or "空",
                url_hash=text_hash(download.url or "")[:10] if text_hash(download.url or "") else "空",
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
            logger.info("已监听新弹出页面的下载事件。")

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

        def add_pending_url(url: str, created_at: float, page_id: int = 0, request_headers: dict | None = None, source: str = "unknown") -> None:
            if not url:
                return
            candidate_identity = active_candidate_identity
            url_hash = text_hash(url)[:10] if text_hash(url) else "空"
            if active_candidate_page_ids and not page_id and source in {"network_request", "network_response", "route_intercept"}:
                logger.info("附件链接未入队：reason=missing_page_id，source={}，identity={}，url_hash={}", source, candidate_identity or "空", url_hash)
                return
            if active_candidate_page_ids and page_id and page_id not in active_candidate_page_ids:
                logger.info("附件链接未入队：reason=non_current_page，source={}，page_id={}，url_hash={}", source, page_id, url_hash)
                return
            if url in downloaded_urls:
                logger.info("附件链接未入队：reason=downloaded，source={}，page_id={}，url_hash={}", source, page_id, url_hash)
                return
            if url in ignored_attachment_urls:
                logger.info("附件链接未入队：reason=ignored_old_or_polluted，source={}，page_id={}，url_hash={}", source, page_id, url_hash)
                return
            if url in failed_download_urls:
                logger.info("附件链接未入队：reason=failed，source={}，page_id={}，url_hash={}", source, page_id, url_hash)
                return
            if any(item.get("url") == url for item in pending_urls):
                logger.info("附件链接未入队：reason=duplicate_pending，source={}，page_id={}，url_hash={}", source, page_id, url_hash)
                return
            item = {"url": url, "created_at": created_at, "page_id": page_id, "headers": request_headers or {}, "source": source, "candidate_identity": candidate_identity}
            if source in {"route_intercept", "network_response", "network_request"}:
                pending_urls.insert(0, item)
            else:
                pending_urls.append(item)
            diag_event(
                "attachment",
                "queue_url",
                "queued",
                candidate=candidate_identity or "",
                source=source,
                page_id=page_id,
                url_hash=url_hash,
                pending_urls=len(pending_urls),
                pending_downloads=len(pending_downloads),
            )


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
            session.context.on("response", handle_response)
            session.context.on("page", handle_new_page)

            page = session.page
            bind_existing_download_pages()
            page.goto(target_url or self.home_url, wait_until="domcontentloaded", timeout=30000)
            if not self._open_chat_interface(page):
                raise RuntimeError("未能进入智联聊天界面，请检查页面是否已登录或传入 --url 聊天页面地址。")
            save_debug_snapshot("opened_chat")
            diag_event(
                "collect",
                "open_chat",
                "ready",
                target=max_resumes,
                max_run_ms=wait_seconds * 1000,
            )

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
                previous_detail_text = self._extract_chat_detail_text(page)
                click_result = self._click_next_uncontacted_candidate(
                    page,
                    seen_candidates,
                    should_skip_candidate_signature,
                    emit_skipped_candidate,
                )
                diag_event(
                    "candidate",
                    "scan",
                    click_result.get("status") or "unknown",
                    cost_ms=click_result.get("elapsed_ms"),
                    target_count=click_result.get("target_count"),
                    skipped=click_result.get("skipped_count"),
                    seen=len(seen_candidates),
                )
                if click_result.get("skip_samples"):
                    logger.info("候选人扫描跳过样本：{}", "; ".join(click_result.get("skip_samples") or []))
                if not can_continue():
                    break
                status = click_result.get("status")
                if status == "skipped_only":
                    scrolled = self._scroll_candidate_list(page, 900)
                    diag_event(
                        "candidate",
                        "scan_round",
                        "skipped_only",
                        cost_ms=(time.monotonic() - iter_started) * 1000,
                        wait_ms=300,
                        scrolled=scrolled,
                    )
                    page.wait_for_timeout(300)
                    continue
                if status == "not_found":
                    consecutive_not_found += 1
                    backoff_ms = min(3000, 500 + consecutive_not_found * 300)
                    scrolled = self._scroll_candidate_list(page, 900)
                    diag_event(
                        "candidate",
                        "scan_round",
                        "not_found",
                        cost_ms=(time.monotonic() - iter_started) * 1000,
                        wait_ms=backoff_ms,
                        scrolled=scrolled,
                        consecutive=consecutive_not_found,
                    )
                    logger.warning("未找到新的候选人卡片，尝试滚动左侧候选列表。")
                    page.wait_for_timeout(backoff_ms)
                    if consecutive_not_found in {4, 8, 12}:
                        self._print_candidate_candidates(page)
                    if consecutive_not_found >= 18:
                        diag_event(
                            "candidate",
                            "scan_round",
                            "exhausted",
                            saved=len(results),
                            target=max_resumes,
                            consecutive=consecutive_not_found,
                        )
                        break
                    continue

                consecutive_not_found = 0
                signature = click_result.get("signature") or ""
                pending_urls.clear()
                ignored_attachment_urls.clear()
                closed_stale_pages = close_non_chat_pages(page)
                if closed_stale_pages:
                    logger.info("已关闭候选人切换前残留简历/附件页面：{} 个。", closed_stale_pages)
                ignored_attachment_urls.update(
                    self._find_latest_chat_attachment_urls(
                        page,
                        captured_urls,
                        mark_captured=False,
                    )
                )



                display_signature = re.sub(r"^(?:\d+|[一二三四五六七八九十]+)[\.、\)）\s]+", "", signature.splitlines()[0]).strip()
                seen_candidates.update(signature.splitlines())
                normalized_signature = re.sub(r"\s+", " ", signature).strip()
                if normalized_signature:
                    seen_candidates.add(normalized_signature)
                candidate_identity_key = self._candidate_identity_key(signature)
                active_candidate_identity = candidate_identity_key or re.sub(r"\s+", " ", display_signature).strip()
                if candidate_identity_key:
                    seen_candidates.add(candidate_identity_key)
                previous_detail_hash = text_hash(re.sub(r"\s+", "", previous_detail_text or ""))[:10] if previous_detail_text else "空"
                diag_event(
                    "candidate",
                    "click",
                    "selected",
                    candidate=display_signature[:80],
                    click=f"{click_result.get('click_x')},{click_result.get('click_y')}",
                    position=click_result.get("position_key") or "空",
                    stable_id=click_result.get("stable_id") or "空",
                    identity=click_result.get("identity_key") or candidate_identity_key or "空",
                    before_detail_hash=previous_detail_hash,
                )
                logger.info("已选择候选人：{}", display_signature[:80])
                page.wait_for_timeout(300)
                if self._dismiss_violation_candidate_dialog(page):
                    diag_event(
                        "candidate",
                        "violation_dialog",
                        "skipped",
                        candidate=display_signature[:80],
                        reason="violation_candidate_dialog",
                    )
                    if on_resume_skipped:
                        on_resume_skipped({
                            "platform_code": self.platform_code,
                            "source_url": page.url,
                            "raw_json": {
                                "candidate_signature": display_signature,
                                "candidate_info": self._parse_candidate_signature(display_signature),
                                "attachment": {},
                                "skip_stage": "violation_candidate_dialog",
                            },
                            "raw_html_path": None,
                            "content_hash": "",
                        })
                    self._scroll_candidate_list(page, 520)
                    page.wait_for_timeout(300)
                    continue
                detail_switch_started = time.monotonic()
                detail_switch = self._wait_candidate_detail_switched(page, display_signature, timeout_ms=6000, previous_detail_text=previous_detail_text)
                diag_event(
                    "candidate",
                    "detail_switch",
                    "ok" if detail_switch.get("switched") else "skipped",
                    cost_ms=(time.monotonic() - detail_switch_started) * 1000,
                    wait_ms=(time.monotonic() - detail_switch_started) * 1000,
                    candidate=display_signature[:80],
                    reason=detail_switch.get("reason"),
                    matched=detail_switch.get("matched"),
                    expected=f"{detail_switch.get('expected_name') or '空'}/{detail_switch.get('expected_age') or '空'}/{detail_switch.get('expected_education') or '空'}",
                    hash=f"{detail_switch.get('before_hash')}->{detail_switch.get('after_hash')}",
                )
                if not detail_switch.get("switched"):
                    diag_event(
                        "candidate",
                        "detail_switch",
                        "skipped",
                        candidate=display_signature[:80],
                        reason="candidate_detail_not_switched",
                    )
                    pending_urls.clear()
                    ignored_attachment_urls.update(
                        self._find_latest_chat_attachment_urls(
                            page,
                            captured_urls,
                            mark_captured=False,
                        )
                    )

                    if on_resume_skipped:
                        on_resume_skipped({
                            "platform_code": self.platform_code,
                            "source_url": page.url,
                            "raw_json": {
                                "candidate_signature": display_signature,
                                "candidate_info": self._parse_candidate_signature(display_signature),
                                "attachment": {},
                                "skip_stage": "candidate_detail_not_switched",
                            },
                            "raw_html_path": None,
                            "content_hash": "",
                        })
                    self._scroll_candidate_list(page, 520)
                    page.wait_for_timeout(300)
                    continue
                profile_extract_started = time.monotonic()
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
                diag_event(
                    "profile",
                    "extract",
                    "ok",
                    cost_ms=(time.monotonic() - profile_extract_started) * 1000,
                    candidate=profile_label,
                    duplicate=duplicate_before_download,
                )
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
                active_candidate_started_at = time.monotonic()
                active_candidate_page_ids.clear()
                active_candidate_page_ids.add(id(page))
                candidate_download_started = active_candidate_started_at
                stale_download_count = drain_stale_downloads(candidate_download_started)
                if stale_download_count:
                    logger.info("已丢弃候选人切换前残留浏览器下载事件：{} 个。", stale_download_count)
                bind_existing_download_pages()
                request_started = time.monotonic()
                request_button_state = self._get_request_attachment_button_state(page)
                request_clicked = False
                attachment_ready = request_button_state == "view"
                if request_button_state == "enabled":
                    request_clicked = self._click_request_attachment_resume(page)
                    if request_clicked:
                        ready_wait_seconds = min(per_candidate_wait_seconds, 3)
                        ready_started = time.monotonic()
                        attachment_ready = self._wait_for_requested_attachment_ready(page, ready_wait_seconds, can_continue)
                        diag_event(
                            "attachment",
                            "request_wait",
                            "ready" if attachment_ready else "timeout",
                            cost_ms=(time.monotonic() - ready_started) * 1000,
                            wait_ms=(time.monotonic() - ready_started) * 1000,
                            candidate=profile_label,
                            wait_limit_ms=ready_wait_seconds * 1000,
                        )
                elif request_button_state == "view":
                    logger.info("右下角按钮为查看附件简历，跳过索要按钮，直接进入查看附件流程。")
                elif request_button_state == "already_requested":
                    logger.info("右下角按钮显示已索要附件简历，跳过索要按钮并短等待。")
                elif request_button_state in {"disabled", "view_disabled"}:
                    logger.info("右下角附件按钮不可用，button_state={}。", request_button_state)
                else:
                    logger.warning("右下角未找到附件相关按钮，继续尝试点击'查看简历附件'。")
                diag_event(
                    "attachment",
                    "request_button",
                    "ready" if attachment_ready else request_button_state or "unknown",
                    cost_ms=(time.monotonic() - request_started) * 1000,
                    wait_ms=(time.monotonic() - request_started) * 1000 if request_clicked else 0,
                    candidate=profile_label,
                    request_clicked=request_clicked,
                    button_state=request_button_state,
                    attachment_ready=attachment_ready,
                )
                save_debug_snapshot(f"candidate_{len(results) + 1}_requested")
                if not can_continue():
                    break

                current_candidate_polluted = False
                if request_clicked and not attachment_ready and not pending_urls and not pending_downloads:
                    wait_started = time.monotonic()
                    diag_event(
                        "attachment",
                        "capture",
                        "timeout",
                        cost_ms=0,
                        wait_ms=0,
                        candidate=profile_label,
                        planned_wait_ms=0,
                        request_clicked=request_clicked,
                        button_state=request_button_state,
                        attachment_ready=attachment_ready,
                        view_clicked=False,
                        pending_urls=0,
                        pending_downloads=0,
                        reason="requested_attachment_not_ready_fast_skip",
                    )
                    if on_resume_skipped:
                        on_resume_skipped({
                            "platform_code": self.platform_code,
                            "source_url": page.url,
                            "raw_json": {
                                "candidate_signature": display_signature,
                                "pre_download_candidate_info": profile_info_before_download,
                                "candidate_info": profile_info_before_download,
                                "attachment": {},
                                "skip_stage": "requested_attachment_not_ready",
                            },
                            "raw_html_path": None,
                            "content_hash": "",
                        })
                    diag_event(
                        "candidate",
                        "summary",
                        "skipped",
                        cost_ms=(time.monotonic() - iter_started) * 1000,
                        wait_ms=(time.monotonic() - wait_started) * 1000,
                        candidate=profile_label,
                        reason="requested_attachment_not_ready",
                    )
                    closed_current_pages = cleanup_current_candidate_pages()
                    if closed_current_pages:
                        logger.info("已关闭本候选人产生的简历/附件页面：{} 个。", closed_current_pages)
                    active_candidate_started_at = 0.0
                    active_candidate_identity = ""
                    page.wait_for_timeout(50)
                    continue

                def process_downloaded_row(row: dict, source_label: str, source_url: str = "") -> bool:
                    nonlocal last_download_monotonic, current_candidate_polluted
                    last_download_monotonic = time.monotonic()
                    attachment = row.setdefault("raw_json", {}).setdefault("attachment", {})
                    downloaded_candidate_info = self._extract_current_candidate_info(
                        page,
                        display_signature,
                        attachment.get("file_path"),
                        use_scrapling=False,
                    )
                    candidate_info = downloaded_candidate_info
                    self._rename_attachment_for_candidate(row, candidate_info, len(results) + 1)
                    candidate_info["resume_file_name"] = attachment.get("file_name")
                    content_hash = str(row.get("content_hash") or attachment.get("file_hash") or "")
                    current_identity = self._candidate_identity_key(display_signature) or re.sub(r"\s+", " ", display_signature).strip()
                    if content_hash:
                        previous_identity = seen_content_hashes.get(content_hash)
                        if previous_identity and previous_identity != current_identity:
                            current_candidate_polluted = True
                            polluted_url = source_url or row.get("source_url") or attachment.get("url") or ""
                            if polluted_url:
                                ignored_attachment_urls.add(polluted_url)
                                failed_download_urls.add(polluted_url)
                                downloaded_urls.add(polluted_url)
                            captured_urls.update(item.get("url") or "" for item in pending_urls if item.get("url"))
                            for item in pending_urls:
                                item_url = item.get("url") or ""
                                if item_url:
                                    ignored_attachment_urls.add(item_url)
                                    failed_download_urls.add(item_url)
                                    downloaded_urls.add(item_url)
                            for item in pending_downloads:
                                item_url = item.get("url") or ""
                                if item_url:
                                    ignored_attachment_urls.add(item_url)
                                    failed_download_urls.add(item_url)
                                    downloaded_urls.add(item_url)
                            pending_urls.clear()
                            pending_downloads.clear()
                            try:
                                file_path = attachment.get("file_path")
                                if file_path:
                                    Path(file_path).unlink(missing_ok=True)
                            except Exception:
                                pass
                            closed_count = cleanup_current_candidate_pages()
                            diag_event(
                                "attachment",
                                "owner_check",
                                "failed",
                                candidate=display_signature[:40],
                                reason="content_hash_owner_mismatch",
                                content_hash=content_hash[:12],
                                previous_candidate=previous_identity[:40],
                                source=source_label,
                                url_hash=text_hash(polluted_url)[:10] if polluted_url else "空",
                                closed_pages=closed_count,
                            )
                            return False
                    row["raw_json"]["candidate_signature"] = display_signature
                    row["raw_json"]["pre_download_candidate_info"] = profile_info_before_download
                    row["raw_json"]["candidate_info"] = candidate_info
                    if content_hash:
                        seen_content_hashes[content_hash] = current_identity
                    results.append(row)
                    pending_urls.clear()
                    retained_downloads = [
                        item for item in pending_downloads
                        if (item.get("candidate_identity") or "") and item.get("candidate_identity") != active_candidate_identity
                    ]
                    cleared_download_count = len(pending_downloads) - len(retained_downloads)
                    pending_downloads[:] = retained_downloads
                    if cleared_download_count:
                        logger.info("已清理当前候选人保存后的残留浏览器下载事件：{} 个。", cleared_download_count)
                    if on_resume_saved:
                        on_resume_saved(row)
                    save_debug_snapshot(f"candidate_{len(results)}_info")
                    logger.info("已自动保存第 {} 份：{}", len(results), attachment.get("file_path"))
                    diag_event(
                        "attachment",
                        "save",
                        "success",
                        cost_ms=(time.monotonic() - candidate_download_started) * 1000,
                        wait_ms=(time.monotonic() - wait_started) * 1000 if "wait_started" in locals() else None,
                        candidate=profile_label,
                        source=source_label,
                        file=attachment.get("file_name") or "空",
                    )
                    return True

                self._last_chat_detail_click = {}
                view_started = time.monotonic()
                pages_before_view_list = [item for item in session.context.pages if not item.is_closed()]
                pages_before_view_ids = {id(item) for item in pages_before_view_list}
                pages_before_view = len(pages_before_view_list)
                view_clicked = self._click_view_attachment_resume(page)
                post_view_wait_ms = 0 if pending_urls or pending_downloads else 300
                if post_view_wait_ms:
                    page.wait_for_timeout(post_view_wait_ms)
                bind_existing_download_pages()
                new_page_count = remember_candidate_pages(pages_before_view_ids)
                pages_after_view = len([item for item in session.context.pages if not item.is_closed()])
                visible_new_page_count = max(0, pages_after_view - pages_before_view)
                click_meta = getattr(self, "_last_chat_detail_click", {}) or {}
                diag_event(
                    "attachment",
                    "view_button",
                    "clicked" if view_clicked else "not_found",
                    cost_ms=(time.monotonic() - view_started) * 1000,
                    wait_ms=post_view_wait_ms,
                    candidate=profile_label,
                    entry=click_meta.get("source") or "not_clicked",
                    target_text=str(click_meta.get("text") or "空")[:80],
                    click=f"{click_meta.get('x', '空')},{click_meta.get('y', '空')}",
                    tag=click_meta.get("tag") or "空",
                    href_hash=text_hash(click_meta.get("href") or "")[:10] if click_meta.get("href") else "空",
                    score=click_meta.get("score") or "空",
                    pages=f"{pages_before_view}->{pages_after_view}",
                    new_pages=visible_new_page_count,
                    bound_new_pages=new_page_count,
                    pending_downloads=len(pending_downloads),
                    pending_urls=len(pending_urls),
                )
                if not view_clicked:
                    logger.warning("当前候选人未找到'查看附件简历'按钮，等待可能的自动链接。")
                    self._print_chat_detail_actions(page)
                latest_page_urls = self._find_latest_chat_attachment_urls(page, captured_urls, mark_captured=False)
                if latest_page_urls:
                    diag_event(
                        "attachment",
                        "latest_chat_scan",
                        "captured",
                        candidate=profile_label,
                        count=len(latest_page_urls),
                    )
                    for url in latest_page_urls:
                        add_pending_url(url, time.monotonic(), id(page), source="latest_chat_after_view")
                elif not pending_urls:
                    logger.info("最新聊天区域未捕获附件链接，启用当前候选人页面DOM兜底扫描。")
                    for url in self._find_attachment_urls_from_pages([page], captured_urls, mark_captured=False):
                        add_pending_url(url, time.monotonic(), id(page), source="dom_fallback_after_view")
                if not view_clicked and pending_urls:
                    diag_event(
                        "attachment",
                        "dom_fallback",
                        "captured",
                        candidate=profile_label,
                        pending_urls=len(pending_urls),
                    )

                wait_started = time.monotonic()
                initial_pages = candidate_pages()
                for item_page in initial_pages:
                    if item_page == page:
                        latest_urls = self._find_latest_chat_attachment_urls(item_page, captured_urls, mark_captured=False)
                        for url in latest_urls:
                            add_pending_url(url, time.monotonic(), id(item_page), source="latest_chat_initial")
                        if latest_urls:
                            continue
                    if not view_clicked:
                        for url in self._find_attachment_urls_from_pages([item_page], captured_urls, mark_captured=False):
                            add_pending_url(url, time.monotonic(), id(item_page), source="dom_fallback_initial")
                effective_wait_seconds = per_candidate_wait_seconds
                if pending_urls or pending_downloads:
                    effective_wait_seconds = min(per_candidate_wait_seconds, 12)
                elif view_clicked:
                    effective_wait_seconds = min(per_candidate_wait_seconds, 15)
                elif request_button_state == "already_requested":
                    effective_wait_seconds = min(per_candidate_wait_seconds, 3)
                elif request_clicked and not attachment_ready:
                    effective_wait_seconds = min(per_candidate_wait_seconds, 3)
                elif request_clicked or attachment_ready:
                    effective_wait_seconds = min(per_candidate_wait_seconds, 15)
                else:
                    effective_wait_seconds = min(per_candidate_wait_seconds, 8)

                candidate_deadline = time.monotonic() + effective_wait_seconds
                saved_current_candidate = False
                skipped_or_failed_current_candidate = False

                while time.monotonic() < candidate_deadline and not saved_current_candidate and not skipped_or_failed_current_candidate:
                    if not can_continue():
                        break
                    bind_existing_download_pages()
                    remember_candidate_pages(pages_before_view_ids)
                    for item_page in candidate_pages():
                        if item_page == page:
                            latest_urls = self._find_latest_chat_attachment_urls(item_page, captured_urls, mark_captured=False)
                            for url in latest_urls:
                                add_pending_url(url, time.monotonic(), id(item_page), source="latest_chat_polling")
                            if latest_urls:
                                continue
                        if not pending_urls:
                            for url in self._find_attachment_urls_from_pages([item_page], captured_urls, mark_captured=False):
                                add_pending_url(url, time.monotonic(), id(item_page), source="dom_fallback_polling")
                    while pending_downloads and len(results) < max_resumes:
                        download_item = pending_downloads.pop(0)
                        candidate_identity = download_item.get("candidate_identity") or ""
                        if active_candidate_identity and candidate_identity and candidate_identity != active_candidate_identity:
                            diag_event(
                                "attachment",
                                "download_event",
                                "ignored",
                                candidate=profile_label,
                                reason="non_current_identity",
                                identity=candidate_identity,
                                current=active_candidate_identity,
                                filename=download_item.get("suggested_filename") or "空",
                            )
                            continue
                        download_page = download_item.get("page")
                        download_page_id = id(download_page) if download_page and not download_page.is_closed() else 0
                        if download_item.get("created_at", 0) < candidate_download_started:
                            continue
                        if download_page_id and download_page_id not in active_candidate_page_ids:
                            diag_event(
                                "attachment",
                                "download_event",
                                "ignored",
                                candidate=profile_label,
                                reason="non_current_page",
                                filename=download_item.get("suggested_filename") or "空",
                                page_id=download_page_id,
                            )
                            continue
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
                    if saved_current_candidate or skipped_or_failed_current_candidate:
                        break
                    while pending_urls and len(results) < max_resumes:
                        download_item = pending_urls.pop(0)
                        download_url = download_item.get("url") or ""
                        if download_item.get("created_at", 0) < candidate_download_started:
                            continue
                        page_id = int(download_item.get("page_id") or 0)
                        candidate_identity = download_item.get("candidate_identity") or ""
                        if active_candidate_identity and candidate_identity and candidate_identity != active_candidate_identity:
                            diag_event(
                                "attachment",
                                "url",
                                "ignored",
                                candidate=profile_label,
                                reason="non_current_identity",
                                identity=candidate_identity,
                                current=active_candidate_identity,
                                url_hash=text_hash(download_url)[:10] if text_hash(download_url) else "空",
                            )
                            continue
                        if page_id and page_id not in active_candidate_page_ids:
                            diag_event(
                                "attachment",
                                "url",
                                "ignored",
                                candidate=profile_label,
                                reason="non_current_page",
                                page_id=page_id,
                                url_hash=text_hash(download_url)[:10] if text_hash(download_url) else "空",
                            )
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
                            failed_download_urls.add(download_url)
                            ignored_attachment_urls.add(download_url)
                            pending_urls[:] = [item for item in pending_urls if item.get("url") != download_url]
                            diag_event(
                                "attachment",
                                "download",
                                "failed",
                                candidate=profile_label,
                                url_hash=error_payload["url_hash"] or "空",
                                remaining_urls=len(pending_urls),
                                reason=exc,
                            )
                            logger.warning("自动下载附件失败，继续尝试其他链接：{}", exc)
                            if not pending_urls:
                                if on_download_failed:
                                    on_download_failed(error_payload)
                                if display_signature:
                                    seen_candidates.add(re.sub(r"\s+", " ", display_signature).strip())
                                skipped_or_failed_current_candidate = True
                                break
                            continue
                        downloaded_urls.add(download_url)
                        result_saved = process_downloaded_row(row, "url_capture", download_url)
                        if result_saved:
                            saved_current_candidate = True
                        else:
                            skipped_or_failed_current_candidate = True
                        break
                    page.wait_for_timeout(200)

                if current_candidate_polluted:
                    diag_event(
                        "attachment",
                        "owner_check",
                        "skipped",
                        candidate=display_signature[:80],
                        reason="content_hash_polluted",
                    )
                    if on_resume_skipped:
                        on_resume_skipped({
                            "platform_code": self.platform_code,
                            "source_url": page.url,
                            "raw_json": {
                                "candidate_signature": display_signature,
                                "pre_download_candidate_info": profile_info_before_download,
                                "candidate_info": profile_info_before_download,
                                "attachment": {},
                                "skip_stage": "attachment_owner_polluted",
                            },
                            "raw_html_path": None,
                            "content_hash": "",
                        })

                if not saved_current_candidate and not skipped_or_failed_current_candidate:
                    diag_event(
                        "attachment",
                        "capture",
                        "timeout",
                        cost_ms=(time.monotonic() - wait_started) * 1000,
                        wait_ms=(time.monotonic() - wait_started) * 1000,
                        candidate=profile_label,
                        planned_wait_ms=int(effective_wait_seconds * 1000),
                        request_clicked=request_clicked,
                        button_state=request_button_state,
                        attachment_ready=attachment_ready,
                        view_clicked=view_clicked,
                        pending_urls=len(pending_urls),
                        pending_downloads=len(pending_downloads),
                    )
                    logger.warning("当前候选人在快速等待窗口内未捕获到附件下载链接。")
                    if on_resume_skipped:
                        if request_button_state == "disabled":
                            skip_stage = "request_attachment_disabled"
                        elif request_clicked and not attachment_ready:
                            skip_stage = "requested_attachment_not_ready"
                        else:
                            skip_stage = "attachment_url_not_captured"
                        on_resume_skipped({
                            "platform_code": self.platform_code,
                            "source_url": page.url,
                            "raw_json": {
                                "candidate_signature": display_signature,
                                "pre_download_candidate_info": profile_info_before_download,
                                "candidate_info": profile_info_before_download,
                                "attachment": {},
                                "skip_stage": skip_stage,
                            },
                            "raw_html_path": None,
                            "content_hash": "",
                        })
                    diag_event(
                        "candidate",
                        "summary",
                        "skipped",
                        cost_ms=(time.monotonic() - iter_started) * 1000,
                        wait_ms=(time.monotonic() - wait_started) * 1000,
                        candidate=profile_label,
                        reason=skip_stage if 'skip_stage' in locals() else "attachment_url_not_captured",
                    )
                    skipped_or_failed_current_candidate = True

                else:
                    diag_event(
                        "candidate",
                        "summary",
                        "success" if saved_current_candidate else "skipped",
                        cost_ms=(time.monotonic() - iter_started) * 1000,
                        wait_ms=(time.monotonic() - wait_started) * 1000,
                        candidate=profile_label,
                    )
                closed_current_pages = cleanup_current_candidate_pages()
                if closed_current_pages:
                    logger.info("已关闭本候选人产生的简历/附件页面：{} 个。", closed_current_pages)
                active_candidate_started_at = 0.0
                active_candidate_identity = ""

            diag_event("collect", "finish", "success", saved=len(results))
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

