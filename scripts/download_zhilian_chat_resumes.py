import argparse
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recruitment_assistant.platforms.zhilian.adapter import ZhilianAdapter
from recruitment_assistant.schemas.raw_resume import RawResumeCreate
from recruitment_assistant.services.raw_resume_service import RawResumeService
from recruitment_assistant.storage.db import create_session


def main() -> None:
    parser = argparse.ArgumentParser(description="智联招聘聊天附件简历 PDF 手动下载采集")
    parser.add_argument("--account", default="default", help="账号标识")
    parser.add_argument("--url", default=None, help="起始 URL，不填则打开智联招聘首页")
    parser.add_argument("--max-resumes", type=int, default=5, help="最多保存附件简历数量")
    parser.add_argument("--wait", type=int, default=180, help="每份简历等待下载秒数")
    parser.add_argument("--download-url", default=None, help="直接下载智联附件临时链接")
    parser.add_argument("--filename", default=None, help="直接下载时使用的文件名")
    parser.add_argument("--auto", action="store_true", help="自动监听并捕获智联附件下载链接")
    parser.add_argument("--auto-click", action="store_true", help="自动进入聊天并循环点击候选人和附件简历按钮")
    parser.add_argument("--per-candidate-wait", type=int, default=60, help="每个候选人等待附件链接秒数")
    args = parser.parse_args()

    adapter = ZhilianAdapter(account_name=args.account)
    if args.auto_click:
        rows = adapter.auto_click_chat_attachment_resumes(
            target_url=args.url,
            max_resumes=args.max_resumes,
            wait_seconds=args.wait,
            per_candidate_wait_seconds=args.per_candidate_wait,
        )
    elif args.auto:
        rows = adapter.auto_capture_chat_attachment_resumes(
            target_url=args.url,
            max_resumes=args.max_resumes,
            wait_seconds=args.wait,
        )
    elif args.download_url:
        rows = [adapter.download_attachment_by_url(args.download_url, filename=args.filename)]
    else:
        rows = adapter.download_manual_chat_attachment_resumes(
            target_url=args.url,
            max_resumes=args.max_resumes,
            wait_seconds=args.wait,
        )
    try:
        with create_session() as session:
            service = RawResumeService(session)
            for row in rows:
                raw_resume = service.create_raw_resume(RawResumeCreate(**row))
                attachment = row.get("raw_json", {}).get("attachment", {})
                print(
                    f"已入库：raw_resume_id={raw_resume.id}, "
                    f"file={attachment.get('file_path')}, url={row['source_url']}"
                )
    except SQLAlchemyError as exc:
        print(f"数据库写入失败，但文件已保存。请检查 PostgreSQL 配置：{exc}")
        for row in rows:
            attachment = row.get("raw_json", {}).get("attachment", {})
            print(f"已保存文件：{attachment.get('file_path')}")


if __name__ == "__main__":
    main()
