"""审计 data/resume_archive.db 的填充情况，输出按表 / 字段的覆盖率报告。"""

import sqlite3
import sys
from pathlib import Path

# Windows 控制台默认 GBK，强制 stdout 用 utf-8 避免 emoji / 中文报错
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

DB = Path("data/resume_archive.db")

if not DB.exists():
    print(f"[FAIL] 数据库不存在: {DB.resolve()}")
    raise SystemExit(1)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()


def count(table: str) -> int:
    return cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def col_fill_rate(table: str) -> list[tuple[str, int, int, float]]:
    """返回每列 [(col, non_null, total, pct), ...]，按 pct 升序。"""
    total = count(table)
    if total == 0:
        return []
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    rows = []
    for c in cols:
        n = cur.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {c} IS NOT NULL AND TRIM(CAST({c} AS TEXT)) != ''"
        ).fetchone()[0]
        rows.append((c, n, total, n / total * 100))
    rows.sort(key=lambda x: x[3])
    return rows


def fill_flag(pct: float) -> str:
    if pct < 30:
        return "[BAD]"
    if pct < 70:
        return "[MID]"
    return "[OK ]"


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


# ---------- 1. 全局规模 ----------
section("1. 全局规模")
print(f"候选人总数:    {count('candidates')}")
print(f"教育经历:      {count('education')}")
print(f"工作经历:      {count('work_experience')}")
print(f"项目经历:      {count('project_experience')}")
print(f"技能/证书:     {count('skills_certificates')}")
print(f"求职意向:      {count('job_intention')}")
print(f"荣誉:          {count('honors')}")
print(f"简历来源:      {count('resume_source')}")
print(f"系统评价:      {count('system_evaluation')}")

# ---------- 2. 按平台分布 ----------
section("2. 按平台分布")
rows = cur.execute(
    "SELECT COALESCE(source_platform,'未知'), COUNT(*) FROM resume_source GROUP BY source_platform"
).fetchall()
for p, n in rows:
    print(f"  {p:<15s} {n}")

# ---------- 3. candidates 字段覆盖率 ----------
section("3. candidates 表字段填充率（升序，<50% 重点关注）")
for col, n, total, pct in col_fill_rate("candidates"):
    print(f"  {fill_flag(pct)} {col:<22s} {n:>3d} / {total} = {pct:5.1f}%")

# ---------- 4. 各子表字段覆盖率 ----------
for table, label in [
    ("education", "education 教育经历"),
    ("work_experience", "work_experience 工作经历"),
    ("project_experience", "project_experience 项目经历"),
    ("skills_certificates", "skills_certificates 技能/证书"),
    ("job_intention", "job_intention 求职意向"),
    ("honors", "honors 荣誉"),
    ("resume_source", "resume_source 简历来源"),
    ("system_evaluation", "system_evaluation 系统评价"),
]:
    rows = col_fill_rate(table)
    if not rows:
        continue
    section(f"4.{label} 字段填充率")
    for col, n, total, pct in rows:
        print(f"  {fill_flag(pct)} {col:<22s} {n:>3d} / {total} = {pct:5.1f}%")

# ---------- 5. 多对多比例（每候选人平均子条目数） ----------
section("5. 每候选人平均子条目数（看 AI 是否捕获到完整结构）")
total_cand = count("candidates")
if total_cand:
    for table, label in [
        ("education", "教育经历"),
        ("work_experience", "工作经历"),
        ("project_experience", "项目经历"),
        ("skills_certificates", "技能/证书"),
        ("honors", "荣誉"),
    ]:
        n = count(table)
        avg = n / total_cand
        print(f"  {label:<10s} 总 {n:>3d} 条，人均 {avg:.2f} 条")

# ---------- 6. 抽样 3 个候选人详情 ----------
section("6. 抽样 3 个候选人完整画像")
samples = cur.execute(
    "SELECT candidate_id, name, age, education_level, current_city, phone FROM candidates ORDER BY candidate_id LIMIT 3"
).fetchall()
for row in samples:
    cid = row["candidate_id"]
    print(f"\n  ── 候选人 #{cid} {row['name']} ({row['age'] or '?'}岁, {row['education_level'] or '?'}, {row['current_city'] or '?'})")
    print(f"     phone={row['phone'] or '空'}")
    edu_n = cur.execute("SELECT COUNT(*) FROM education WHERE candidate_id=?", (cid,)).fetchone()[0]
    work_n = cur.execute("SELECT COUNT(*) FROM work_experience WHERE candidate_id=?", (cid,)).fetchone()[0]
    proj_n = cur.execute("SELECT COUNT(*) FROM project_experience WHERE candidate_id=?", (cid,)).fetchone()[0]
    skill_n = cur.execute("SELECT COUNT(*) FROM skills_certificates WHERE candidate_id=?", (cid,)).fetchone()[0]
    honor_n = cur.execute("SELECT COUNT(*) FROM honors WHERE candidate_id=?", (cid,)).fetchone()[0]
    has_intent = cur.execute("SELECT 1 FROM job_intention WHERE candidate_id=?", (cid,)).fetchone()
    has_source = cur.execute("SELECT 1 FROM resume_source WHERE candidate_id=?", (cid,)).fetchone()
    print(f"     education={edu_n}, work={work_n}, project={proj_n}, skills={skill_n}, honors={honor_n}, intent={'Y' if has_intent else 'N'}, source={'Y' if has_source else 'N'}")

# ---------- 7. 异常诊断 ----------
section("7. 异常诊断")
zero_edu = cur.execute(
    "SELECT COUNT(*) FROM candidates c WHERE NOT EXISTS (SELECT 1 FROM education e WHERE e.candidate_id=c.candidate_id)"
).fetchone()[0]
zero_work = cur.execute(
    "SELECT COUNT(*) FROM candidates c WHERE NOT EXISTS (SELECT 1 FROM work_experience w WHERE w.candidate_id=c.candidate_id)"
).fetchone()[0]
zero_intent = cur.execute(
    "SELECT COUNT(*) FROM candidates c WHERE NOT EXISTS (SELECT 1 FROM job_intention j WHERE j.candidate_id=c.candidate_id)"
).fetchone()[0]
no_phone = cur.execute("SELECT COUNT(*) FROM candidates WHERE phone IS NULL OR phone=''").fetchone()[0]
no_age = cur.execute("SELECT COUNT(*) FROM candidates WHERE age IS NULL").fetchone()[0]
print(f"  无任何教育经历的候选人:   {zero_edu} / {total_cand}")
print(f"  无任何工作经历的候选人:   {zero_work} / {total_cand}")
print(f"  无求职意向的候选人:       {zero_intent} / {total_cand}")
print(f"  无电话的候选人:           {no_phone} / {total_cand}")
print(f"  无年龄的候选人:           {no_age} / {total_cand}")

conn.close()
