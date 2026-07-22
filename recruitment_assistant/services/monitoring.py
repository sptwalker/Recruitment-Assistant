"""系统操作日志 + AI 用量监测：写入与查询。

设计：best-effort、各开各的 session、绝不抛异常打断业务。
监测与业务事务解耦——业务失败不产生日志、日志失败不影响业务。
"""
from __future__ import annotations

from datetime import date, datetime, time
from datetime import datetime as _dt

from loguru import logger
from sqlalchemy import func, select

from recruitment_assistant.storage import db  # 用模块引用调用 create_session，便于测试 monkeypatch
from recruitment_assistant.storage.resume_models import AiUsageLog, OperationLog


# ---------------- 写入 ----------------

def record_operation(action: str, target: str = "", status: str = "",
                     detail: str = "", started_at: datetime | None = None) -> None:
    """记录一条操作日志。finished_at=now；有 started_at 则算 duration_seconds。"""
    try:
        now = _dt.now()
        duration = (now - started_at).total_seconds() if started_at else None
        s = db.create_session()
        try:
            from recruitment_assistant.storage import tenancy
            s.add(OperationLog(
                action=action, target=target or None, status=status or None,
                detail=detail or None, started_at=started_at, finished_at=now,
                duration_seconds=duration,
                actor_user_id=tenancy.current_user_id(),  # 有请求上下文时留痕 actor/租户
                tenant_id=tenancy.current_tenant_id(),
            ))
            s.commit()
        finally:
            s.close()
    except Exception as exc:
        logger.warning("[操作日志] 记录失败（忽略）: {} — {}", action, exc)


def record_ai_usage(feature: str, endpoint_name: str | None, model: str | None,
                    is_failover: bool, usage) -> None:
    """记录一次 AI 调用用量。usage 为 resp.usage（可能 None → 记 0）。"""
    try:
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        tt = int(getattr(usage, "total_tokens", 0) or 0) or (pt + ct)
        s = db.create_session()
        try:
            s.add(AiUsageLog(
                feature=feature, endpoint_name=endpoint_name, model=model,
                is_failover=bool(is_failover),
                prompt_tokens=pt, completion_tokens=ct, total_tokens=tt,
            ))
            s.commit()
        finally:
            s.close()
    except Exception as exc:
        logger.warning("[AI用量] 记录失败（忽略）: {} — {}", feature, exc)


# ---------------- 查询 ----------------

def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    end = datetime.combine(day, time.max)
    return start, end


def _fmt(dt: datetime | None) -> str:
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""
    except Exception:
        return str(dt or "")


def list_operations(day: date, limit: int = 2000, tenant_id: int | None = None) -> list[dict]:
    """按日期返回操作日志（当天，倒序）。tenant_id 非空则只返回该租户（M2 多租户隔离）。"""
    start, end = _day_bounds(day)
    s = db.create_session()
    try:
        stmt = (
            select(OperationLog)
            .where(OperationLog.created_at >= start, OperationLog.created_at <= end)
        )
        if tenant_id is not None:
            stmt = stmt.where(OperationLog.tenant_id == tenant_id)
        rows = s.scalars(
            stmt.order_by(OperationLog.created_at.desc()).limit(limit)
        ).all()
        return [{
            "时间": _fmt(r.created_at),
            "操作": r.action,
            "对象": r.target or "",
            "结果": r.status or "",
            "详情": r.detail or "",
            "耗时(秒)": round(r.duration_seconds, 1) if r.duration_seconds is not None else "",
            "起始时间": _fmt(r.started_at),
        } for r in rows]
    finally:
        s.close()


def operation_summary(day: date, tenant_id: int | None = None) -> dict[str, int]:
    """当天各 action 计数。tenant_id 非空则只统计该租户。"""
    start, end = _day_bounds(day)
    s = db.create_session()
    try:
        stmt = (
            select(OperationLog.action, func.count(OperationLog.id))
            .where(OperationLog.created_at >= start, OperationLog.created_at <= end)
        )
        if tenant_id is not None:
            stmt = stmt.where(OperationLog.tenant_id == tenant_id)
        rows = s.execute(stmt.group_by(OperationLog.action)).all()
        return {action: int(cnt) for action, cnt in rows}
    finally:
        s.close()


def list_ai_usage(day: date, limit: int = 3000) -> list[dict]:
    """按日期返回 AI 用量明细（当天，倒序）。"""
    start, end = _day_bounds(day)
    s = db.create_session()
    try:
        rows = s.scalars(
            select(AiUsageLog)
            .where(AiUsageLog.created_at >= start, AiUsageLog.created_at <= end)
            .order_by(AiUsageLog.created_at.desc())
            .limit(limit)
        ).all()
        return [{
            "时间": _fmt(r.created_at),
            "功能模块": _feature_label(r.feature),
            "接口": r.endpoint_name or "",
            "模型": r.model or "",
            "降级": "是" if r.is_failover else "",
            "prompt": r.prompt_tokens,
            "completion": r.completion_tokens,
            "total": r.total_tokens,
        } for r in rows]
    finally:
        s.close()


_FEATURE_LABELS = {
    "parse": "简历解析",
    "match": "岗位匹配",
    "outline": "面试大纲",
    "连通性测试": "连通性测试",
}


def _feature_label(feature: str) -> str:
    return _FEATURE_LABELS.get(feature, feature)


def ai_usage_summary() -> dict:
    """今日/累计 调用次数 + token；并按 feature 分组（累计）。"""
    today_start, today_end = _day_bounds(_dt.now().date())
    s = db.create_session()
    try:
        def _agg(stmt):
            cnt, tot = s.execute(stmt).one()
            return int(cnt or 0), int(tot or 0)

        today_calls, today_tokens = _agg(
            select(func.count(AiUsageLog.id), func.coalesce(func.sum(AiUsageLog.total_tokens), 0))
            .where(AiUsageLog.created_at >= today_start, AiUsageLog.created_at <= today_end)
        )
        all_calls, all_tokens = _agg(
            select(func.count(AiUsageLog.id), func.coalesce(func.sum(AiUsageLog.total_tokens), 0))
        )
        by_feature = []
        rows = s.execute(
            select(AiUsageLog.feature, func.count(AiUsageLog.id),
                   func.coalesce(func.sum(AiUsageLog.total_tokens), 0))
            .group_by(AiUsageLog.feature)
        ).all()
        for feat, cnt, tot in rows:
            by_feature.append({"功能模块": _feature_label(feat),
                               "累计调用": int(cnt), "累计tokens": int(tot)})
        return {
            "today_calls": today_calls, "today_tokens": today_tokens,
            "all_calls": all_calls, "all_tokens": all_tokens,
            "by_feature": by_feature,
        }
    finally:
        s.close()
