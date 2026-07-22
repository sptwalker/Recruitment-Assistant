"""租户隔离与归属（M2.2）——服务端强制，不依赖调用方自觉过滤。

设计：
- ContextVar 保存当前请求的 tenant_id / user_id（FastAPI 依赖里 set，请求结束 reset）。
- `do_orm_execute` 事件：对所有带 tenant_id 的模型（继承 TenantMixin）SELECT 时默认注入
  `tenant_id == 当前租户`。过滤在 ORM 层默认开启——漏写查询也不会跨租户泄露（fail-closed）。
- `before_flush` 事件：新建对象自动盖 tenant_id（OwnedMixin 再盖 owner_id）。
- 无租户上下文（本地/桌面 Streamlit、脚本、登录 upsert）→ 不过滤，保持单机行为不变。

仅「会被直接查询」的业务表挂 mixin（候选人/岗位两个根 + 5 张直查子表）；深层子表
（education/work_experience/… ）只经 candidate 关系加载，其可见性由父候选人的过滤决定，
关系加载本就被本过滤跳过，给它们加 tenant_id 无隔离收益，故不挂。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import Integer, event
from sqlalchemy.orm import Mapped, Session, mapped_column, with_loader_criteria

_tenant_id: ContextVar[int | None] = ContextVar("tenant_id", default=None)
_user_id: ContextVar[int | None] = ContextVar("user_id", default=None)

# 执行选项：显式绕过租户过滤（如跨租户后台维护），谨慎使用。
SKIP_TENANT = "skip_tenant_filter"


class TenantMixin:
    """带租户归属的表：tenant_id → organization.id。"""
    tenant_id: Mapped[int | None] = mapped_column(
        Integer, index=True, nullable=True  # 存量行回填后语义上非空；留 nullable 便于迁移与 fail-closed
    )


class OwnedMixin(TenantMixin):
    """根业务表：再加 owner_id → app_user.id（用于 M2.3 recruiter/manager 可见范围）。"""
    owner_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)


def set_context(tenant_id: int | None, user_id: int | None):
    return _tenant_id.set(tenant_id), _user_id.set(user_id)


def reset_context(tokens) -> None:
    t_tok, u_tok = tokens
    _tenant_id.reset(t_tok)
    _user_id.reset(u_tok)


@contextmanager
def tenant_scope(tenant_id: int | None, user_id: int | None = None):
    tokens = set_context(tenant_id, user_id)
    try:
        yield
    finally:
        reset_context(tokens)


def current_tenant_id() -> int | None:
    return _tenant_id.get()


def current_user_id() -> int | None:
    return _user_id.get()


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_filter(state) -> None:
    # 只过滤顶层 SELECT；列加载/关系加载跳过（关系加载的行由其父实体的过滤保证）。
    if not state.is_select or state.is_column_load or state.is_relationship_load:
        return
    if state.execution_options.get(SKIP_TENANT):
        return
    tid = _tenant_id.get()
    if tid is None:  # 无租户上下文（本地/脚本/登录）→ 不过滤
        return
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantMixin, lambda cls: cls.tenant_id == tid, include_aliases=True
        )
    )


@event.listens_for(Session, "before_flush")
def _stamp_tenant(session: Session, _flush_ctx, _instances) -> None:
    tid = _tenant_id.get()
    if tid is None:
        return
    uid = _user_id.get()
    for obj in session.new:
        if isinstance(obj, TenantMixin) and getattr(obj, "tenant_id", None) is None:
            obj.tenant_id = tid
        if isinstance(obj, OwnedMixin) and uid is not None and getattr(obj, "owner_id", None) is None:
            obj.owner_id = uid
