"""归档服务的去重 / 匹配复用 / 备份 关键路径（用临时 SQLite 库）。"""
from recruitment_assistant.services.resume_archive_service import ResumeArchiveService
from recruitment_assistant.storage.models import JobPosition
from recruitment_assistant.storage.resume_models import Candidate, ResumeSource


def _add_position(session, title="Golang工程师") -> int:
    p = JobPosition(title=title, status="active")
    session.add(p)
    session.commit()
    return p.id


def _add_candidate(session, name="张三", age=28, edu="本科", phone="13800000001",
                   city="北京", file_path="/data/att/zhangsan.pdf"):
    c = Candidate(name=name, age=age, education_level=edu, phone=phone, current_city=city)
    if file_path:
        c.resume_source = ResumeSource(
            source_platform="BOSS直聘", file_name=file_path.split("/")[-1],
            file_type="PDF", file_path=file_path,
        )
    session.add(c)
    session.commit()
    return c.candidate_id


def test_is_duplicate_by_phone(temp_resume_db):
    SessionLocal, _ = temp_resume_db
    s = SessionLocal()
    try:
        _add_candidate(s, phone="13900000000")
        svc = ResumeArchiveService(s)
        assert svc.is_duplicate(phone="13900000000") is True
        assert svc.is_duplicate(phone="13911112222") is False
    finally:
        s.close()


def test_is_duplicate_by_name_age_edu(temp_resume_db):
    SessionLocal, _ = temp_resume_db
    s = SessionLocal()
    try:
        _add_candidate(s, name="李四", age=30, edu="硕士", phone=None)
        svc = ResumeArchiveService(s)
        assert svc.is_duplicate(name="李四", age=30, education_level="硕士") is True
        assert svc.is_duplicate(name="李四", age=31, education_level="硕士") is False
        # 只有姓名、无 age/edu → 不判重（避免误杀重名）
        assert svc.is_duplicate(name="李四") is False
    finally:
        s.close()


def test_resume_source_exists(temp_resume_db):
    SessionLocal, _ = temp_resume_db
    s = SessionLocal()
    try:
        _add_candidate(s, file_path="/data/att/wangwu.pdf")
        svc = ResumeArchiveService(s)
        assert svc.resume_source_exists("/data/att/wangwu.pdf") is True
        assert svc.resume_source_exists("/data/att/nobody.pdf") is False
    finally:
        s.close()


def test_scored_candidate_reuse_by_jd_hash(temp_resume_db):
    SessionLocal, _ = temp_resume_db
    s = SessionLocal()
    try:
        cid = _add_candidate(s)
        pid = _add_position(s)
        pid2 = _add_position(s, title="别的岗位")
        svc = ResumeArchiveService(s)
        svc.save_position_match(pid, cid, 88, "good",
                                dimensions={"skill_match": 90, "experience_match": 85,
                                            "education_match": 80, "location_match": 95},
                                jd_hash="HASH_A")
        # 同 JD 命中复用
        assert cid in svc.get_scored_candidate_ids(pid, "HASH_A")
        # JD 变化不命中 → 会重新评分
        assert cid not in svc.get_scored_candidate_ids(pid, "HASH_B")
        # 不同岗位不串
        assert cid not in svc.get_scored_candidate_ids(pid2, "HASH_A")
    finally:
        s.close()


def test_save_position_match_upsert(temp_resume_db):
    SessionLocal, _ = temp_resume_db
    s = SessionLocal()
    try:
        cid = _add_candidate(s)
        pid = _add_position(s)
        svc = ResumeArchiveService(s)
        svc.save_position_match(pid, cid, 70, "v1", jd_hash="H1")
        svc.save_position_match(pid, cid, 92, "v2", jd_hash="H2")  # 同 (pos,cand) 覆盖
        rows = svc.list_position_matches(pid, min_score=0)
        assert len(rows) == 1
        match_row, _cand = rows[0]
        assert match_row.score == 92 and match_row.jd_hash == "H2"
    finally:
        s.close()


def test_save_position_match_bad_position_fk(temp_resume_db):
    """position_id 无对应 job_position → 真 FK 拦截。"""
    import pytest
    SessionLocal, _ = temp_resume_db
    s = SessionLocal()
    try:
        cid = _add_candidate(s)
        svc = ResumeArchiveService(s)
        with pytest.raises(Exception):
            svc.save_position_match(999999, cid, 80, "x", jd_hash="H")
    finally:
        s.close()


def test_export_candidates(temp_resume_db):
    SessionLocal, _ = temp_resume_db
    s = SessionLocal()
    try:
        _add_candidate(s, name="导出甲", phone="13700000001")
        _add_candidate(s, name="导出乙", phone="13700000002", file_path=None)
        rows = ResumeArchiveService(s).export_candidates()
        assert len(rows) == 2
        names = {r["姓名"] for r in rows}
        assert names == {"导出甲", "导出乙"}
        assert set(rows[0].keys()) >= {"姓名", "电话", "学历", "来源平台", "归档时间"}
    finally:
        s.close()
