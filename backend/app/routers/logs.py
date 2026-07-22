"""系统日志 / AI 用量路由（M2.3，admin-only）。

操作日志按当前租户过滤（OperationLog 有 tenant_id）。AI 用量表无 tenant_id 列，
故为实例级全量——ponytail: 单机部署下可接受；多租户成本分摊需给 ai_usage_log 加
tenant_id 列后再按租户过滤。
"""
from __future__ import annotations

from datetime import date as _date

from fastapi import APIRouter, Depends, Query

from backend.app.deps import require_role
from recruitment_assistant.services import monitoring
from recruitment_assistant.storage.auth_models import Role, User

router = APIRouter(prefix="/logs", tags=["logs"])
_admin = require_role(Role.admin)


@router.get("/operations")
def operations(
    day: _date = Query(default_factory=_date.today),
    limit: int = Query(2000, ge=1, le=5000),
    user: User = Depends(_admin),
) -> dict:
    return {
        "items": monitoring.list_operations(day, limit=limit, tenant_id=user.org_id),
        "summary": monitoring.operation_summary(day, tenant_id=user.org_id),
    }


@router.get("/ai-usage")
def ai_usage(
    day: _date = Query(default_factory=_date.today),
    _user: User = Depends(_admin),
) -> dict:
    # 实例级全量（见模块 docstring）
    return {
        "items": monitoring.list_ai_usage(day),
        "summary": monitoring.ai_usage_summary(),
    }
