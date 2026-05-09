import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description="智联招聘人工登录并保存登录态")
    parser.add_argument("--account", default="default", help="账号标识，不需要是真实账号")
    parser.add_argument("--wait", type=int, default=180, help="等待人工登录秒数")
    parser.add_argument("--keep-open", action="store_true", help="登录成功保存状态后保持浏览器打开，按 Enter 后关闭")
    parser.add_argument("--no-enter-home", action="store_true", help="登录后不自动跳转智联系统首页")
    args = parser.parse_args()

    adapter = ZhilianAdapter(account_name=args.account)
    state_path = adapter.login_manually(
        wait_seconds=args.wait,
        keep_open=args.keep_open,
        enter_home=not args.no_enter_home,
    )

    print(f"登录态已保存：{state_path}")


if __name__ == "__main__":
    main()
