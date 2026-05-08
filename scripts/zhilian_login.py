import argparse

from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description="智联招聘人工登录并保存登录态")
    parser.add_argument("--account", default="default", help="账号标识，不需要是真实账号")
    parser.add_argument("--wait", type=int, default=180, help="等待人工登录秒数")
    args = parser.parse_args()

    adapter = ZhilianAdapter(account_name=args.account)
    state_path = adapter.login_manually(wait_seconds=args.wait)
    print(f"登录态已保存：{state_path}")


if __name__ == "__main__":
    main()
