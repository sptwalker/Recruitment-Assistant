"""检测 BOSS直聘登录态是否有效 - 使用 CDP 真实 Chrome。"""

from loguru import logger

from recruitment_assistant.platforms.boss.adapter import BossAdapter


def main() -> None:
    adapter = BossAdapter()
    logger.info("正在检测 BOSS直聘登录态...")

    if not adapter.state_path.exists() and not adapter._has_persistent_profile():
        logger.warning("未找到 BOSS直聘登录态文件，请先运行 boss_login.py 完成登录。")
        return

    is_logged_in = adapter.is_logged_in(headless=False)
    if is_logged_in:
        logger.info("BOSS直聘登录态有效，已登录。")
    else:
        logger.warning("BOSS直聘登录态已失效，请重新登录。")


if __name__ == "__main__":
    main()
