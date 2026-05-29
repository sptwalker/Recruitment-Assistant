from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, String, Table, Text, delete, func, select, update
from sqlalchemy.orm import Session

from recruitment_assistant.storage.db import engine
from recruitment_assistant.storage.models import BossCandidateRecord, CrawlTask

metadata = MetaData()
platform_candidate_record = Table(
    "platform_candidate_record",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("platform_code", String(32), nullable=False, index=True),
    Column("target_site", String(64), nullable=False, index=True),
    Column("candidate_key", String(64), nullable=False, index=True),
    Column("candidate_signature", String(512)),
    Column("name", String(128), index=True),
    Column("gender", String(16)),
    Column("job_title", String(255), index=True),
    Column("phone", String(64)),
    Column("resume_file_name", String(255)),
    Column("source_url", Text),
    Column("content_hash", String(64), index=True),
    Column("raw_resume_id", BigInteger, index=True),
    Column("task_id", BigInteger, index=True),
    Column("hit_count", Integer, nullable=False, default=1),
    Column("first_seen_at", DateTime(timezone=True), server_default=func.now()),
    Column("last_seen_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
)


class CrawlTaskService:
    def __init__(self, session: Session):
        self.session = session

    def create_task(
        self,
        *,
        platform_code: str,
        task_name: str,
        task_type: str,
        query_params: dict | None = None,
        planned_count: int | None = None,
    ) -> CrawlTask:
        task = CrawlTask(
            platform_code=platform_code,
            task_name=task_name,
            task_type=task_type,
            status="running",
            query_params=query_params,
            planned_count=planned_count,
            started_at=datetime.now(),
        )
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def finish_task(
        self,
        task: CrawlTask,
        *,
        status: str,
        success_count: int = 0,
        failed_count: int = 0,
        error_message: str | None = None,
    ) -> CrawlTask:
        task.status = status
        task.success_count = success_count
        task.failed_count = failed_count
        task.error_message = error_message
        task.finished_at = datetime.now()
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def list_tasks(self, limit: int = 50, platform_code: str | None = None) -> list[CrawlTask]:
        stmt = select(CrawlTask)
        if platform_code:
            stmt = stmt.where(CrawlTask.platform_code == platform_code)
        stmt = stmt.order_by(CrawlTask.id.desc()).limit(limit)
        return list(self.session.scalars(stmt).all())

    def success_summary(self, platform_code: str | None = None) -> tuple[int, int]:
        stmt = select(func.count(CrawlTask.id), func.coalesce(func.sum(CrawlTask.success_count), 0)).where(
            CrawlTask.status == "success"
        )
        if platform_code:
            stmt = stmt.where(CrawlTask.platform_code == platform_code)
        task_count, resume_count = self.session.execute(stmt).one()
        return int(task_count or 0), int(resume_count or 0)

    def reap_stale_running_tasks(self, platform_code: str | None = None) -> int:
        # Streamlit/Chrome 异常退出会留下 status='running' 但进程已死的孤儿任务，
        # 启动时统一收尾为 cancelled，避免首页 "运行中" 永久误报。
        stmt = update(CrawlTask).where(CrawlTask.status == "running").values(
            status="cancelled",
            finished_at=datetime.now(),
            error_message="进程异常退出，启动时自动收尾",
        )
        if platform_code:
            stmt = stmt.where(CrawlTask.platform_code == platform_code)
        result = self.session.execute(stmt)
        self.session.commit()
        return int(result.rowcount or 0)


class PlatformCandidateRecordService:
    def __init__(self, session: Session):
        self.session = session

    def list_profile_lookup_keys(self, platform_code: str) -> set[str]:
        stmt = select(platform_candidate_record.c.candidate_key).where(
            platform_candidate_record.c.platform_code == platform_code
        )
        return {str(key) for key in self.session.execute(stmt).scalars() if key}


class BossCandidateRecordService:
    def __init__(self, session: Session):
        self.session = session

    def ensure_table(self) -> None:
        BossCandidateRecord.__table__.create(bind=engine, checkfirst=True)
        # 启动自适应迁移：旧表可能没有 talking_position 列；幂等地补齐。
        from recruitment_assistant.storage.db import _ensure_boss_candidate_record_columns

        _ensure_boss_candidate_record_columns()

    def list_candidate_keys(self, platform_code: str = "boss") -> set[str]:
        self.ensure_table()
        stmt = select(BossCandidateRecord.candidate_key).where(BossCandidateRecord.platform_code == platform_code)
        return {str(key) for key in self.session.execute(stmt).scalars() if key}

    def list_candidate_signatures(self, platform_code: str = "boss") -> set[str]:
        self.ensure_table()
        stmt = select(BossCandidateRecord.candidate_signature).where(BossCandidateRecord.platform_code == platform_code)
        return {str(signature) for signature in self.session.execute(stmt).scalars() if signature}

    def count_records(self, platform_code: str = "boss") -> int:
        self.ensure_table()
        stmt = select(func.count(BossCandidateRecord.id)).where(BossCandidateRecord.platform_code == platform_code)
        return int(self.session.scalar(stmt) or 0)

    def clear_records(self, platform_code: str = "boss") -> int:
        self.ensure_table()
        result = self.session.execute(delete(BossCandidateRecord).where(BossCandidateRecord.platform_code == platform_code))
        self.session.commit()
        return int(result.rowcount or 0)

    def list_records_for_display(self, platform_code: str = "boss", limit: int = 5000) -> list[dict]:
        """读取指定平台的全部 boss_candidate_record，按 first_seen_at 升序，返回 UI 渲染所需结构。

        UI 列：序号 / 姓名 / 年龄 / 学历 / 沟通职位 / 电话 / 归档时间 / 简历文件名。
        """
        self.ensure_table()
        stmt = (
            select(BossCandidateRecord)
            .where(BossCandidateRecord.platform_code == platform_code)
            .order_by(BossCandidateRecord.first_seen_at.asc(), BossCandidateRecord.id.asc())
            .limit(limit)
        )
        rows = list(self.session.scalars(stmt).all())

        records: list[dict] = []
        for index, row in enumerate(rows, start=1):
            sig = row.candidate_signature or ""
            parts = sig.split("/") if sig else []
            age = parts[1] if len(parts) > 1 and parts[1] else "—"
            education = parts[2] if len(parts) > 2 and parts[2] else "—"
            first_seen = ""
            if row.first_seen_at:
                try:
                    first_seen = row.first_seen_at.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    first_seen = str(row.first_seen_at)
            records.append({
                "id": int(row.id),
                "序号": index,
                "姓名": row.name or "—",
                "年龄": age,
                "学历": education,
                "沟通职位": row.talking_position or "—",
                "电话": row.phone or "—",
                "归档时间": first_seen,
                "简历文件名": row.resume_file_name or "—",
            })
        return records

    def delete_record_by_id(self, record_id: int) -> str | None:
        """按主键删除单条 boss_candidate_record。返回被删记录的 candidate_key（用于同步内存去重）。"""
        self.ensure_table()
        key = self.session.execute(
            select(BossCandidateRecord.candidate_key).where(BossCandidateRecord.id == int(record_id))
        ).scalar()
        if key is None:
            return None
        self.session.execute(
            delete(BossCandidateRecord).where(BossCandidateRecord.id == int(record_id))
        )
        self.session.commit()
        return str(key)

    def delete_all_by_platform(self, platform_code: str) -> set[str]:
        """删除指定平台的全部已入库候选人记录。返回被删记录的 candidate_key 集合。"""
        self.ensure_table()
        keys = {str(k) for k in self.session.execute(
            select(BossCandidateRecord.candidate_key).where(BossCandidateRecord.platform_code == platform_code)
        ).scalars() if k}
        self.session.execute(
            delete(BossCandidateRecord).where(BossCandidateRecord.platform_code == platform_code)
        )
        self.session.commit()
        return keys

    def upsert_candidate_record(
        self,
        *,
        platform_code: str,
        target_site: str,
        candidate_key: str,
        candidate_signature: str | None = None,
        name: str | None = None,
        gender: str | None = None,
        job_title: str | None = None,
        talking_position: str | None = None,
        phone: str | None = None,
        resume_file_name: str | None = None,
        source_url: str | None = None,
        content_hash: str | None = None,
        raw_resume_id: int | None = None,
        task_id: int | None = None,
    ) -> bool:
        self.ensure_table()
        existing = self.session.execute(
            select(BossCandidateRecord).where(
                BossCandidateRecord.platform_code == platform_code,
                BossCandidateRecord.candidate_key == candidate_key,
            )
        ).scalar_one_or_none()
        if existing:
            existing.hit_count = int(existing.hit_count or 0) + 1
            existing.candidate_signature = existing.candidate_signature or candidate_signature
            existing.name = existing.name or name
            existing.gender = existing.gender or gender
            existing.job_title = existing.job_title or job_title
            existing.talking_position = existing.talking_position or talking_position
            existing.phone = existing.phone or phone
            existing.resume_file_name = existing.resume_file_name or resume_file_name
            existing.source_url = existing.source_url or source_url
            existing.content_hash = existing.content_hash or content_hash
            existing.raw_resume_id = existing.raw_resume_id or raw_resume_id
            existing.task_id = existing.task_id or task_id
            self.session.add(existing)
            self.session.commit()
            return False

        self.session.add(
            BossCandidateRecord(
                platform_code=platform_code,
                target_site=target_site,
                candidate_key=candidate_key,
                candidate_signature=candidate_signature,
                name=name,
                gender=gender,
                job_title=job_title,
                talking_position=talking_position,
                phone=phone,
                resume_file_name=resume_file_name,
                source_url=source_url,
                content_hash=content_hash,
                raw_resume_id=raw_resume_id,
                task_id=task_id,
            )
        )
        self.session.commit()
        return True

