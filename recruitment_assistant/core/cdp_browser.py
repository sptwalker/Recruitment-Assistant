import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from recruitment_assistant.config.settings import get_settings


_DEFAULT_CHROME_PATHS = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
]


def find_chrome_executable() -> Path:
    settings = get_settings()
    if settings.chrome_executable_path:
        custom = Path(settings.chrome_executable_path)
        if custom.exists():
            return custom
        raise FileNotFoundError(f"指定的 Chrome 路径不存在: {custom}")

    for candidate in _DEFAULT_CHROME_PATHS:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "未找到 Chrome 浏览器。请安装 Google Chrome 或在 .env 中设置 CHROME_EXECUTABLE_PATH。"
    )


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_cdp_ready(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _is_port_in_use(port):
            return True
        time.sleep(0.5)
    return False


@dataclass
class CDPBrowserSession:
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    page: Page
    chrome_process: subprocess.Popen | None = None
    _owns_process: bool = field(default=False, repr=False)

    def close(self) -> None:
        try:
            self.context.close()
        except Exception:
            pass
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self.playwright.stop()
        except Exception:
            pass
        if self._owns_process and self.chrome_process is not None:
            try:
                self.chrome_process.terminate()
                self.chrome_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.chrome_process.kill()
            except Exception:
                pass


def open_cdp_browser_session(
    user_data_dir: Path | None = None,
    cdp_port: int | None = None,
) -> CDPBrowserSession:
    settings = get_settings()
    port = cdp_port or settings.boss_cdp_port

    if user_data_dir is None:
        user_data_dir = settings.browser_state_dir / "boss_cdp_profile"
    user_data_dir = user_data_dir.resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    chrome_process = None
    owns_process = False

    if not _is_port_in_use(port):
        chrome_path = find_chrome_executable()
        chrome_args = [
            str(chrome_path),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--disable-background-timer-throttling",
        ]
        logger.info("启动 Chrome CDP 浏览器: port={}, profile={}", port, user_data_dir)
        chrome_process = subprocess.Popen(
            chrome_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        owns_process = True

        # Chrome may exit immediately if the profile is locked by another instance
        time.sleep(1)
        if chrome_process.poll() is not None:
            raise RuntimeError(
                f"Chrome 启动后立即退出（可能 profile 被其他 Chrome 实例锁定）。"
                f"请关闭所有 Chrome 窗口后重试，或在 .env 中设置不同的 BOSS_CDP_PORT。"
            )

        if not _wait_for_cdp_ready(port):
            chrome_process.kill()
            raise RuntimeError(f"Chrome CDP 端口 {port} 未在超时时间内就绪。")
    else:
        logger.info("CDP 端口 {} 已被占用，尝试连接已有 Chrome 实例", port)

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
    except Exception:
        pw.stop()
        if owns_process and chrome_process is not None:
            chrome_process.kill()
        raise

    return CDPBrowserSession(
        playwright=pw,
        browser=browser,
        context=context,
        page=page,
        chrome_process=chrome_process,
        _owns_process=owns_process,
    )
