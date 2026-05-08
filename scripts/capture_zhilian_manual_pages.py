import argparse

from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter
from recruitment_assistant.schemas.raw_resume import RawResumeCreate
from recruitment_assistant.services.raw_resume_service import RawResumeService
from recruitment_assistant.storage.db import create_session


def main() -> None:
    parser = argparse.ArgumentParser(description="智联招聘手动打开候选人页面并保存快照")
    parser.add_argument("--account", default="default", help="账号标识")
    parser.add_argument("--url", default=None, help="起始 URL，不填则打开智联招聘首页")
    parser.add_argument("--max-pages", type=int, default=5, help="最多保存页面数量")
    args = parser.parse_args()

    adapter = ZhilianAdapter(account_name=args.account)
    rows = adapter.capture_manual_resume_pages(target_url=args.url, max_pages=args.max_pages)
    with create_session() as session:
        service = RawResumeService(session)
        for row in rows:
            raw_resume = service.create_raw_resume(RawResumeCreate(**row))
            print(f"已保存：raw_resume_id={raw_resume.id}, url={row['source_url']}")


if __name__ == "__main__":
    main()
