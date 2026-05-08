import argparse

from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description="检测智联招聘登录态")
    parser.add_argument("--account", default="default", help="账号标识，不需要是真实账号")
    args = parser.parse_args()

    adapter = ZhilianAdapter(account_name=args.account)
    print("已登录" if adapter.is_logged_in() else "未登录或登录态已失效")


if __name__ == "__main__":
    main()
