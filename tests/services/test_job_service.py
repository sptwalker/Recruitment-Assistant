"""JobService 关键路径：软删岗位时级联清理派生匹配、保留面试历史。"""
from recruitment_assistant.services.job_service import JobService
from recruitment_assistant.services.resume_archive_service import ResumeArchiveService
from recruitment_assistant.storage.resume_models import Candidate


def _candidate(session, name="张三", phone="13800000001") -> int:
    c = Candidate(name=name, age=28, education_level="本科", phone=phone, current_city="北京")
    session.add(c)
    session.commit()
    return c.candidate_id


def test_delete_position_clears_matches_keeps_interview(temp_resume_db):
    SessionLocal, _ = temp_resume_db
    s = SessionLocal()
    try:
        job = JobService(s)
        arc = ResumeArchiveService(s)
        cid = _candidate(s)
        pid = job.create_position(title="Golang工程师").id
        arc.save_position_match(pid, cid, 88, "good", jd_hash="H")
        arc.create_interview_eval(cid, position_id=pid, interview_round="一面", conclusion="通过")

        assert len(arc.list_position_matches(pid, min_score=0)) == 1
        assert job.delete_position(pid) is True

        # 派生匹配随岗位删除被清理（消灭孤儿）
        assert arc.list_position_matches(pid, min_score=0) == []
        # 面试历史保留
        assert len(arc.list_interview_evals(cid)) == 1
        # 岗位软删后不再出现在列表
        assert all(p.id != pid for p in job.list_positions())
        # 重复删除幂等返回 False
        assert job.delete_position(pid) is False
    finally:
        s.close()
