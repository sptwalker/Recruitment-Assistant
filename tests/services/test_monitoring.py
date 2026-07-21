"""系统操作日志 + AI 用量监测服务测试（用临时 SQLite 库）。"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from recruitment_assistant.services import monitoring


def test_record_and_list_operations(temp_resume_db):
    monitoring.record_operation("简历解析入库", target="10份", status="完成",
                                detail="成功8 跳过1 失败1",
                                started_at=datetime.now() - timedelta(seconds=5))
    rows = monitoring.list_operations(date.today())
    assert len(rows) == 1
    r = rows[0]
    assert r["操作"] == "简历解析入库" and r["结果"] == "完成"
    assert r["耗时(秒)"] and float(r["耗时(秒)"]) >= 4.5   # ~5s
    assert r["起始时间"]  # 有起始时间


def test_operation_summary(temp_resume_db):
    monitoring.record_operation("岗位匹配", status="完成")
    monitoring.record_operation("岗位匹配", status="完成")
    monitoring.record_operation("面试评价", status="已保存")
    summ = monitoring.operation_summary(date.today())
    assert summ.get("岗位匹配") == 2 and summ.get("面试评价") == 1


def test_record_ai_usage_with_tokens(temp_resume_db):
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=40, total_tokens=140)
    monitoring.record_ai_usage("match", "GLM", "GLM-5.2", False, usage)
    rows = monitoring.list_ai_usage(date.today())
    assert len(rows) == 1
    assert rows[0]["功能模块"] == "岗位匹配"   # feature label mapped
    assert rows[0]["total"] == 140 and rows[0]["prompt"] == 100


def test_record_ai_usage_none_usage_records_zero(temp_resume_db):
    monitoring.record_ai_usage("parse", "DS", "deepseek-chat", True, None)
    rows = monitoring.list_ai_usage(date.today())
    assert len(rows) == 1
    assert rows[0]["total"] == 0 and rows[0]["降级"] == "是"


def test_ai_usage_summary(temp_resume_db):
    monitoring.record_ai_usage("match", "GLM", "m", False, SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))
    monitoring.record_ai_usage("match", "GLM", "m", False, SimpleNamespace(prompt_tokens=20, completion_tokens=5, total_tokens=25))
    monitoring.record_ai_usage("parse", "DS", "m2", False, SimpleNamespace(prompt_tokens=8, completion_tokens=2, total_tokens=10))
    s = monitoring.ai_usage_summary()
    assert s["today_calls"] == 3 and s["all_calls"] == 3
    assert s["today_tokens"] == 50 and s["all_tokens"] == 50
    by = {d["功能模块"]: d for d in s["by_feature"]}
    assert by["岗位匹配"]["累计调用"] == 2 and by["岗位匹配"]["累计tokens"] == 40
    assert by["简历解析"]["累计调用"] == 1


def test_list_operations_date_filter(temp_resume_db):
    """只返回当天的记录。"""
    monitoring.record_operation("岗位匹配", status="完成")
    yesterday = date.today() - timedelta(days=1)
    assert monitoring.list_operations(yesterday) == []
    assert len(monitoring.list_operations(date.today())) == 1
