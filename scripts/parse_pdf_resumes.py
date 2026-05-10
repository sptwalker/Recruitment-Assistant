import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.parsers.pdf_resume_parser import parse_resume_file


def iter_resume_files(path: Path) -> list[Path]:
    supported_suffixes = {".pdf", ".doc", ".docx"}
    if path.is_file():
        return [path] if path.suffix.lower() in supported_suffixes else []
    return sorted(
        [item for item in path.rglob("*") if item.is_file() and item.suffix.lower() in supported_suffixes],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="解析已归档的 PDF/DOC/DOCX 简历并输出基础字段")
    parser.add_argument("--file", default=None, help="指定单个简历文件路径")
    parser.add_argument("--dir", default=None, help="指定简历目录，不填则扫描附件目录")
    parser.add_argument("--limit", type=int, default=20, help="最多解析数量")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出完整解析结果")
    args = parser.parse_args()

    settings = get_settings()
    target = Path(args.file or args.dir or settings.attachment_dir)
    resume_files = iter_resume_files(target)[: args.limit]
    if not resume_files:
        print(f"未找到 PDF/DOC/DOCX 简历文件：{target}")
        return

    results = []
    for resume_file in resume_files:
        try:
            parsed = parse_resume_file(resume_file)
        except Exception as exc:
            print(f"解析失败：{resume_file} | {exc}")
            continue
        data = parsed.to_dict()
        results.append(data)
        if not args.json:
            print("-" * 80)
            print(f"文件：{data['source_file']}")
            print(f"姓名：{data['name']}")
            print(f"岗位名称：{data['job_title']}")
            print(f"电话：{data['phone']}")
            print(f"邮箱：{data['email']}")
            print(f"城市：{data['current_city']}")
            print(f"学历：{data['highest_degree']}")
            print(f"经验：{data['years_of_experience']}")
            print(f"当前公司：{data['current_company']}")
            print(f"当前职位：{data['current_position']}")
            print(f"期望职位：{data['expected_position']}")
            print(f"技能：{', '.join(data['skills'])}")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
