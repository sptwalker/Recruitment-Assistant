from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, MetaData, String, Table, Text, func, insert, or_, select, update
from sqlalchemy.orm import Session

from recruitment_assistant.storage.models import CrawlTask
from recruitment_assistant.utils.hash_utils import text_hash

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

    def list_tasks(self, limit: int = 50) -> list[CrawlTask]:
        stmt = select(CrawlTask).order_by(CrawlTask.id.desc()).limit(limit)
        return list(self.session.scalars(stmt).all())

    def success_summary(self) -> tuple[int, int]:
        stmt = select(func.count(CrawlTask.id), func.coalesce(func.sum(CrawlTask.success_count), 0)).where(
            CrawlTask.status == "success"
        )
        task_count, resume_count = self.session.execute(stmt).one()
        return int(task_count or 0), int(resume_count or 0)


class PlatformCandidateRecordService:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def build_candidate_key(platform_code: str, row: dict) -> str:
        raw_json = row.get("raw_json", {}) or {}
        info = raw_json.get("candidate_info", {}) or {}
        attachment = raw_json.get("attachment", {}) or {}
        parts = [
            platform_code,
            row.get("source_candidate_id") or "",
            info.get("phone") or "",
            info.get("name") or raw_json.get("candidate_signature") or "",
            info.get("job_title") or "",
        ]
        return text_hash("|".join(str(part).strip() for part in parts if part)) or ""

    @staticmethod
    def build_signature_key(platform_code: str, candidate_signature: str) -> str:
        parts = [platform_code, candidate_signature]
        return text_hash("|".join(str(part).strip() for part in parts if part)) or ""

    def list_duplicate_lookup_keys(self, platform_code: str) -> set[str]:
        stmt = select(
            platform_candidate_record.c.candidate_key,
            platform_candidate_record.c.candidate_signature,
            platform_candidate_record.c.name,
            platform_candidate_record.c.job_title,
            platform_candidate_record.c.phone,
        ).where(platform_candidate_record.c.platform_code == platform_code)
        keys: set[str] = set()
        for row in self.session.execute(stmt).mappings():
            candidate_key = row.get("candidate_key")
            if candidate_key:
                keys.add(candidate_key)
            signature = row.get("candidate_signature")
            if signature:
                keys.add(self.build_signature_key(platform_code, signature))
            parts = [platform_code, row.get("phone") or "", row.get("name") or "", row.get("job_title") or ""]
            compact_key = text_hash("|".join(str(part).strip() for part in parts if part)) or ""
            if compact_key:
                keys.add(compact_key)
            name_job_key = text_hash(
                "|".join(str(part).strip() for part in [platform_code, row.get("name") or "", row.get("job_title") or ""] if part)
            ) or ""
            if name_job_key:
                keys.add(name_job_key)
        return keys

    def candidate_signature_exists(self, platform_code: str, candidate_signature: str) -> bool:
        signature_key = self.build_signature_key(platform_code, candidate_signature)
        if not signature_key:
            return False
        stmt = select(platform_candidate_record.c.id).where(
            platform_candidate_record.c.platform_code == platform_code,
            or_(
                platform_candidate_record.c.candidate_signature == candidate_signature,
                platform_candidate_record.c.candidate_key == signature_key,
            ),
        )
        return self.session.scalar(stmt.limit(1)) is not None

    def candidate_exists(self, platform_code: str, row: dict) -> bool:
        candidate_key = self.build_candidate_key(platform_code, row)
        if not candidate_key:
            return False
        stmt = select(platform_candidate_record.c.id).where(
            platform_candidate_record.c.platform_code == platform_code,
            platform_candidate_record.c.candidate_key == candidate_key,
        )
        return self.session.scalar(stmt.limit(1)) is not None

    def upsert_from_raw_resume(
        self,
        *,
        platform_code: str,
        target_site: str,
        row: dict,
        raw_resume_id: int | None = None,
        task_id: int | None = None,
    ) -> dict | None:
        candidate_key = self.build_candidate_key(platform_code, row)
        if not candidate_key:
            return None

        raw_json = row.get("raw_json", {}) or {}
        info = raw_json.get("candidate_info", {}) or {}
        attachment = raw_json.get("attachment", {}) or {}
        existing = self.session.execute(
            select(platform_candidate_record).where(
                platform_candidate_record.c.platform_code == platform_code,
                platform_candidate_record.c.candidate_key == candidate_key,
            )
        ).mappings().first()
        if existing:
            self.session.execute(
                update(platform_candidate_record)
                .where(platform_candidate_record.c.id == existing["id"])
                .values(
                    hit_count=int(existing.get("hit_count") or 0) + 1,
                    last_seen_at=datetime.now(),
                    raw_resume_id=existing.get("raw_resume_id") or raw_resume_id,
                    task_id=existing.get("task_id") or task_id,
                )
            )
            self.session.commit()
            return dict(existing)

        result = self.session.execute(
            insert(platform_candidate_record)
            .values(
                platform_code=platform_code,
                target_site=target_site,
                candidate_key=candidate_key,
                candidate_signature=raw_json.get("candidate_signature"),
                name=info.get("name"),
                gender=info.get("gender"),
                job_title=info.get("job_title"),
                phone=info.get("phone"),
                resume_file_name=info.get("resume_file_name") or attachment.get("file_name"),
                source_url=row.get("source_url"),
                content_hash=row.get("content_hash"),
                raw_resume_id=raw_resume_id,
                task_id=task_id,
            )
            .returning(platform_candidate_record.c.id)
        )
        self.session.commit()
        return {"id": result.scalar_one()}

