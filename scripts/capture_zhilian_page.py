import argparse

from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter
from recruitment_assistant.schemas.raw_resume import RawResumeCreate
from recruitment_assistant.services.raw_resume_service import RawResumeService
from recruitment_assistant.storage.db import create_session


def main() -> None:
    parser = argparse.ArgumentParser(description="智联招聘当前页面快照采集")
    parser.add_argument("--account", default="default", help="账号标识")
    parser.add_argument("--url", default=None, help="打开指定 URL，不填则打开智联招聘首页")
    parser.add_argument("--wait", type=int, default=30, help="页面等待秒数")
    args = parser.parse_args()

    adapter = ZhilianAdapter(account_name=args.account)
    data = adapter.capture_current_page(target_url=args.url, wait_seconds=args.wait)
    with create_session() as session:
        raw_resume = RawResumeService(session).create_raw_resume(RawResumeCreate(**data))
    print(f"已保存原始页面：raw_resume_id={raw_resume.id}, snapshot={data['raw_html_path']}")


if __name__ == "__main__":
    main()
