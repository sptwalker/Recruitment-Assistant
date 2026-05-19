"""抽样跑一份简历看结构化结果。

用法：
  python scripts/test_parse_one.py "data/attachments/zhilian/xxx/yyy.pdf"
"""

import json
import sys
from pathlib import Path

from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.parsers.pdf_resume_parser import (
    extract_text_from_docx,
    extract_text_from_pdf,
)
from recruitment_assistant.services.resume_ai_service import ResumeAIService


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("用法：python scripts/test_parse_one.py <简历路径>")
    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f"文件不存在：{path}")

    settings = get_settings()
    text = extract_text_from_pdf(path) if path.suffix.lower() == ".pdf" else extract_text_from_docx(path)
    print(f"--- 提取文本 {len(text)} 字符 ---")
    print(text[:500])
    print("...\n")

    ai = ResumeAIService(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        model=settings.ai_model,
    )
    data = ai.parse_resume_text(text)
    if not data:
        sys.exit("AI 返回空")
    print("--- 结构化结果 ---")
    print(json.dumps(data.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
