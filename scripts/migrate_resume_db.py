"""resume_archive.db 数据治理迁移（幂等）。

四个阶段：
  1. fix-platform：BOSS → BOSS直聘
  2. drop-columns：DROP COLUMN 19 个已删字段
  3. merge-skills：同候选人 + 同 skill_type 合并
  4. ai-fill：phone 作 key 跑 AI 补全 candidates 缺失字段

用法：
  python scripts/migrate_resume_db.py --dry-run            # 全部阶段干跑
  python scripts/migrate_resume_db.py                      # 全部阶段执行
  python scripts/migrate_resume_db.py --phase ai-fill      # 单跑 AI 补全
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DB_PATH = Path("data/resume_archive.db")

DROP_COLUMNS = {
    "candidates": ["qq", "native_place", "political_status", "ethnicity", "height"],
    "education": ["main_courses", "honors"],
    "work_experience": [
        "company_type", "department", "job_level",
        "work_duration", "performance", "manage_scope",
    ],
    "project_experience": ["project_industry"],
    "skills_certificates": ["certificate_org", "get_date"],
    "job_intention": ["work_nature", "arrival_time", "industry_prefer"],
    "honors": ["issue_by"],
}


def _open() -> sqlite3.Connection:
    if not DB_PATH.exists():
        sys.exit(f"[FAIL] 数据库不存在：{DB_PATH.resolve()}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def phase_fix_platform(conn: sqlite3.Connection, dry: bool) -> None:
    print("\n[阶段 1] fix-platform: BOSS → BOSS直聘")
    cur = conn.execute(
        "SELECT COUNT(*) FROM resume_source WHERE source_platform = 'BOSS'"
    )
    n = cur.fetchone()[0]
    print(f"  待修正记录数：{n}")
    if n == 0:
        print("  ✓ 已经是规范命名（幂等通过）")
        return
    if dry:
        print("  [dry-run] 跳过实际写入")
        return
    conn.execute(
        "UPDATE resume_source SET source_platform = 'BOSS直聘' WHERE source_platform = 'BOSS'"
    )
    conn.commit()
    print(f"  ✓ 已更新 {n} 条")


def phase_drop_columns(conn: sqlite3.Connection, dry: bool) -> None:
    print("\n[阶段 2] drop-columns: 19 个字段")
    existing_tables = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, cols in DROP_COLUMNS.items():
        if table not in existing_tables:
            print(f"  [{table}] 表不存在，跳过")
            continue
        existing_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        to_drop = [c for c in cols if c in existing_cols]
        already = [c for c in cols if c not in existing_cols]
        if already:
            print(f"  [{table}] 已删除：{already}（幂等跳过）")
        if not to_drop:
            continue
        print(f"  [{table}] 待删除：{to_drop}")
        if dry:
            print("    [dry-run] 跳过实际 DROP")
            continue
        for col in to_drop:
            try:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
                print(f"    ✓ DROP {col}")
            except sqlite3.OperationalError as exc:
                sys.exit(f"    [FAIL] DROP COLUMN 失败：{exc}（需 SQLite 3.35+）")
        conn.commit()


def phase_merge_skills(conn: sqlite3.Connection, dry: bool) -> None:
    print("\n[阶段 3] merge-skills: 同候选人 + 同 skill_type 合并")
    rows = conn.execute("""
        SELECT candidate_id, skill_type, COUNT(*) as n
        FROM skills_certificates
        WHERE skill_type IS NOT NULL
        GROUP BY candidate_id, skill_type
        HAVING n > 1
    """).fetchall()
    print(f"  发现 {len(rows)} 组重复 (candidate_id, skill_type)")
    total_dropped = 0
    for r in rows:
        cid, stype, _n = r["candidate_id"], r["skill_type"], r["n"]
        items = conn.execute(
            "SELECT skill_id, skill_name, proficiency, is_core FROM skills_certificates "
            "WHERE candidate_id=? AND skill_type=? ORDER BY skill_id",
            (cid, stype),
        ).fetchall()
        names = [i["skill_name"] for i in items if i["skill_name"]]
        merged_name = "、".join(dict.fromkeys(names))  # 顺序去重
        prof = next((i["proficiency"] for i in items if i["proficiency"]), None)
        core = max((i["is_core"] for i in items), default=0)
        keep_id = items[0]["skill_id"]
        drop_ids = [i["skill_id"] for i in items[1:]]
        print(f"  cid={cid} type={stype} keep #{keep_id} drop {drop_ids} merged='{merged_name}'")
        if dry:
            continue
        conn.execute(
            "UPDATE skills_certificates SET skill_name=?, proficiency=?, is_core=? WHERE skill_id=?",
            (merged_name, prof, core, keep_id),
        )
        conn.execute(
            f"DELETE FROM skills_certificates WHERE skill_id IN ({','.join('?'*len(drop_ids))})",
            drop_ids,
        )
        total_dropped += len(drop_ids)
    if not dry:
        conn.commit()
        print(f"  ✓ 合并完成，物理删除 {total_dropped} 条冗余")


def phase_ai_fill(conn: sqlite3.Connection, dry: bool) -> None:
    print("\n[阶段 4] ai-fill: phone 作 key 调 AI 补全 age/gender/current_city")
    targets = conn.execute("""
        SELECT c.candidate_id, c.name, c.phone, c.age, c.gender, c.current_city,
               rs.file_path
        FROM candidates c
        LEFT JOIN resume_source rs ON rs.candidate_id = c.candidate_id
        WHERE (c.age IS NULL OR c.gender IS NULL OR c.current_city IS NULL)
          AND rs.file_path IS NOT NULL
    """).fetchall()
    print(f"  待补全候选人：{len(targets)}")
    if dry or not targets:
        if dry:
            print("  [dry-run] 跳过 AI 调用")
        return

    from recruitment_assistant.config.settings import get_settings
    from recruitment_assistant.parsers.pdf_resume_parser import (
        extract_text_from_docx,
        extract_text_from_pdf,
    )
    from recruitment_assistant.services.resume_ai_service import ResumeAIService

    settings = get_settings()
    if not settings.ai_api_key:
        sys.exit("  [FAIL] AI_API_KEY 未配置，跳过 ai-fill 阶段")
    ai = ResumeAIService(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        model=settings.ai_model,
    )

    updated = 0
    for row in targets:
        cid, fp = row["candidate_id"], row["file_path"]
        path = Path(fp) if fp else None
        if not path or not path.exists():
            print(f"  cid={cid} 文件不存在，跳过：{fp}")
            continue
        suffix = path.suffix.lower()
        try:
            text = extract_text_from_pdf(path) if suffix == ".pdf" else extract_text_from_docx(path)
        except Exception as exc:
            print(f"  cid={cid} 提取文本失败：{exc}")
            continue
        if len(text.strip()) < 50:
            print(f"  cid={cid} 文本过短，跳过")
            continue
        try:
            data = ai.parse_resume_text(text)
        except Exception as exc:
            print(f"  cid={cid} AI 调用异常：{exc}")
            continue
        if not data:
            continue
        new_age = row["age"] if row["age"] is not None else data.age
        new_gender = row["gender"] or data.gender
        new_city = row["current_city"] or data.current_city
        if (new_age, new_gender, new_city) == (row["age"], row["gender"], row["current_city"]):
            continue
        conn.execute(
            "UPDATE candidates SET age=?, gender=?, current_city=? WHERE candidate_id=?",
            (new_age, new_gender, new_city, cid),
        )
        updated += 1
        print(f"  cid={cid} {row['name']} age:{row['age']}→{new_age} "
              f"gender:{row['gender']}→{new_gender} city:{row['current_city']}→{new_city}")
    conn.commit()
    print(f"  ✓ 补全完成，更新 {updated} 条")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--phase",
        choices=["fix-platform", "drop-columns", "merge-skills", "ai-fill", "all"],
        default="all",
    )
    args = parser.parse_args()

    print(f"DB:    {DB_PATH.resolve()}")
    print(f"Phase: {args.phase}")
    print(f"Mode:  {'DRY-RUN' if args.dry_run else 'EXECUTE'}")

    conn = _open()
    try:
        if args.phase in ("fix-platform", "all"):
            phase_fix_platform(conn, args.dry_run)
        if args.phase in ("drop-columns", "all"):
            phase_drop_columns(conn, args.dry_run)
        if args.phase in ("merge-skills", "all"):
            phase_merge_skills(conn, args.dry_run)
        if args.phase in ("ai-fill", "all"):
            phase_ai_fill(conn, args.dry_run)
    finally:
        conn.close()
    print("\n[DONE]")


if __name__ == "__main__":
    main()
