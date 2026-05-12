"""BOSS直聘沟通页附件简历自动下载脚本。"""

import argparse

from loguru import logger

from recruitment_assistant.platforms.boss.adapter import BossAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description="BOSS直聘附件简历自动下载")
    parser.add_argument("--max-resumes", type=int, default=5, help="最大下载数量")
    parser.add_argument("--wait-seconds", type=int, default=900, help="总等待时间（秒）")
    parser.add_argument("--account", type=str, default="default", help="账号标识")
    args = parser.parse_args()

    adapter = BossAdapter(account_name=args.account)
    logger.info("开始 BOSS直聘简历下载，最大数量={}, 超时={}s", args.max_resumes, args.wait_seconds)

    def on_saved(row: dict) -> None:
        info = row.get("raw_json", {}).get("candidate_info", {})
        filename = row.get("raw_json", {}).get("attachment", {}).get("file_name", "")
        logger.info("已下载: {} -> {}", info.get("name", "未知"), filename)

    def on_skipped(row: dict) -> None:
        sig = row.get("raw_json", {}).get("candidate_signature", "")
        reason = row.get("raw_json", {}).get("skip_stage", "")
        logger.info("已跳过: {} ({})", sig, reason)

    def on_diagnostic(line: str) -> None:
        logger.debug(line)

    results = adapter.auto_click_chat_attachment_resumes(
        max_resumes=args.max_resumes,
        wait_seconds=args.wait_seconds,
        on_resume_saved=on_saved,
        on_resume_skipped=on_skipped,
        on_diagnostic=on_diagnostic,
    )

    logger.info("下载完成，共获取 {} 份简历。", len(results))
    for i, row in enumerate(results, 1):
        attachment = row.get("raw_json", {}).get("attachment", {})
        logger.info("  {}. {} ({})", i, attachment.get("file_name", ""), attachment.get("file_path", ""))


if __name__ == "__main__":
    main()
