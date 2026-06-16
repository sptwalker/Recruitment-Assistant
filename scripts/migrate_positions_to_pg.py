"""一次性迁移脚本：将 SQLite job_positions 数据迁移到 PostgreSQL job_position 表。

运行方式：
    python -m scripts.migrate_positions_to_pg

迁移内容：
1. 读取 SQLite job_positions 所有记录
2. 在 PostgreSQL job_position 表中创建对应记录
3. 更新 SQLite 中引用 position_id 的 4 张表（interview_evaluations /
   interview_invitations / position_matches / interview_outlines），
   将旧 SQLite position_id 替换为新 PostgreSQL id
4. 输出 ID 映射表供回溯
"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from recruitment_assistant.storage.db import create_session, init_database
from recruitment_assistant.storage.models import JobPosition
from recruitment_assistant.storage.resume_db import create_resume_session, init_resume_database


SQLITE_FK_TABLES = [
    "interview_evaluations",
    "interview_invitations",
    "position_matches",
    "interview_outlines",
]


def migrate() -> None:
    # 初始化两个数据库
    init_database()
    init_resume_database()

    # ---- 1. 读取 SQLite 中所有岗位 ----
    sqlite_session = create_resume_session()
    rows = sqlite_session.execute(text("SELECT * FROM job_positions")).mappings().all()
    if not rows:
        print("SQLite job_positions 表为空，无需迁移。")
        sqlite_session.close()
        return

    print(f"发现 {len(rows)} 条 SQLite 岗位记录，开始迁移…")

    # ---- 2. 写入 PostgreSQL ----
    pg_session = create_session()
    id_map: dict[int, int] = {}  # old_sqlite_id → new_pg_id

    for row in rows:
        status = "active" if row["status"] == "open" else (row["status"] or "active")
        pos = JobPosition(
            title=row["title"],
            department=row.get("department") or None,
            work_city=row.get("work_city") or None,
            salary_range=row.get("salary_range") or None,
            min_education=row.get("min_education") or None,
            min_experience=row.get("min_experience") or None,
            # 旧的 requirements 字段映射到 job_requirements
            job_requirements=row.get("requirements") or None,
            responsibilities=None,  # 新字段，用户后续填写
            status=status,
        )
        pg_session.add(pos)
        pg_session.flush()  # 获取自增 id
        id_map[row["position_id"]] = pos.id

    pg_session.commit()
    print(f"已写入 PostgreSQL {len(id_map)} 条岗位记录。")
    print(f"ID 映射：{id_map}")

    # ---- 3. 更新 SQLite FK 引用 ----
    updated = 0
    for old_id, new_id in id_map.items():
        for table in SQLITE_FK_TABLES:
            result = sqlite_session.execute(
                text(f"UPDATE {table} SET position_id = :new_id WHERE position_id = :old_id"),
                {"new_id": new_id, "old_id": old_id},
            )
            updated += result.rowcount

    sqlite_session.commit()
    print(f"已更新 SQLite FK 引用 {updated} 条。")

    # ---- 清理 ----
    pg_session.close()
    sqlite_session.close()
    print("迁移完成！")


if __name__ == "__main__":
    migrate()
