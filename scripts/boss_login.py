"""BOSS直聘手动登录脚本 - 启动纯净 Chrome，不注入任何自动化代码。

登录阶段完全不连接 Playwright，避免 CDP 命令被 Boss 反检测系统识别。
用户手动完成登录后，cookie 自动保存在 Chrome profile 中。
"""

import subprocess
import time

from loguru import logger

from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.core.cdp_browser import find_chrome_executable
from recruitment_assistant.platforms.boss.adapter import BossAdapter


def main() -> None:
    adapter = BossAdapter()
    settings = get_settings()
    user_data_dir = adapter.user_data_dir.resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    chrome_path = find_chrome_executable()
    boss_url = "https://www.zhipin.com/web/user/?ka=header-login"

    chrome_args = [
        str(chrome_path),
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        boss_url,
    ]

    logger.info("启动纯净 Chrome（无自动化注入）...")
    logger.info("Profile 目录: {}", user_data_dir)
    logger.info("即将打开: {}", boss_url)

    proc = subprocess.Popen(chrome_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    time.sleep(2)
    if proc.poll() is not None:
        logger.error(
            "Chrome 启动后立即退出。可能原因：\n"
            "  1. 已有 Chrome 实例使用了相同 profile\n"
            "  2. 请关闭所有 Chrome 窗口后重试\n"
            "提示：如果你需要保持日常 Chrome 打开，可在 .env 中设置不同的 profile 路径。"
        )
        return

    logger.info("Chrome 已启动。请在浏览器中完成以下操作：")
    logger.info("  1. 登录 BOSS直聘（扫码或手机号）")
    logger.info("  2. 登录成功后进入沟通页面 (https://www.zhipin.com/web/chat/index)")
    logger.info("  3. 确认页面正常显示后，回到此终端按 Enter 键")
    logger.info("")

    input("登录完成后按 Enter 继续...")

    # 检查 profile 中是否存在 Boss cookie（不连接 Playwright，避免触发检测）
    cookies_db = user_data_dir / "Default" / "Cookies"
    network_dir = user_data_dir / "Default" / "Network"
    if cookies_db.exists() or network_dir.exists():
        logger.info("Chrome profile 中已检测到 Cookie 数据，登录态应已保存。")
        logger.info("后续可运行 check_boss_login.py 验证登录态是否有效。")
    else:
        logger.warning("未检测到 Cookie 文件，请确认是否已成功登录。")


if __name__ == "__main__":
    main()
