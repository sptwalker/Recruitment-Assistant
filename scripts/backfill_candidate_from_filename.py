"""一次性回填脚本：从 ResumeSource.file_name 反推 name/age/education_level 补齐 Candidate 缺失字段。

用法：
    python scripts/backfill_candidate_from_filename.py            # 演示哪些会被更新
    python scripts/backfill_candidate_from_filename.py --apply    # 真正写库

策略：仅在 candidate 字段为 NULL/空时回填；如已有值，不覆盖（避免和现有数据起争议）。
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from recruitment_assistant.storage.resume_db import create_resume_session  # noqa: E402
from recruitment_assistant.storage.resume_models import Candidate, ResumeSource  # noqa: E402


_DEGREE_NORMALIZE = {
    "研究生": "硕士",
    "专科": "大专",
}


def parse_filename(file_name: str | None) -> tuple[str | None, int | None, str | None]:
    if not file_name:
        return None, None, None
    stem = Path(file_name).stem
    parts = [part.strip() for part in re.split(r"[-_｜|]", stem) if part.strip()]
    name = parts[0] if parts else None
    age = None
    education_level = None
    for part in parts[1:5]:
        match = re.search(r"(\d{1,2})\s*岁", part)
        if match:
            age = int(match.group(1))
        if part in {"高中", "中专", "大专", "专科", "本科", "硕士", "研究生", "博士"}:
            education_level = _DEGREE_NORMALIZE.get(part, part)
    if name and not re.search(r"[一-鿿A-Za-z]", name):
        name = None
    return name, age, education_level


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库；不带此参数仅 dry-run")
    args = ap.parse_args()

    with create_resume_session() as session:
        rows = session.execute(
            select(Candidate, ResumeSource.file_name)
            .join(ResumeSource, Candidate.candidate_id == ResumeSource.candidate_id, isouter=True)
        ).all()

        plan: list[tuple[Candidate, dict[str, object]]] = []
        for cand, fname in rows:
            f_name, f_age, f_edu = parse_filename(fname)
            updates: dict[str, object] = {}
            if cand.age is None and f_age:
                updates["age"] = f_age
            if (not cand.education_level) and f_edu:
                updates["education_level"] = f_edu
            if (not cand.name or cand.name in ("未知", "未识别")) and f_name:
                updates["name"] = f_name
            if updates:
                plan.append((cand, updates))

        if not plan:
            print("No candidates need backfill.")
            return

        print(f"Found {len(plan)} candidate(s) eligible for backfill:")
        for cand, updates in plan:
            updates_repr = ", ".join(f"{k}={v!r}" for k, v in updates.items())
            print(f"  cid={cand.candidate_id:>4} name={cand.name!r:<20} updates: {updates_repr}")

        if not args.apply:
            print("\n(dry-run) Re-run with --apply to write changes.")
            return

        for cand, updates in plan:
            for k, v in updates.items():
                setattr(cand, k, v)
        session.commit()
        print(f"\nApplied to {len(plan)} candidate(s).")


if __name__ == "__main__":
    main()
