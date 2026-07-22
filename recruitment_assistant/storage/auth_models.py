"""身份与租户模型（M2）——挂在统一 ResumeBase metadata 上，随 alembic 迁移建表。

- Organization：租户（一个飞书企业 = 一个 org，键 feishu_tenant_key）。
- User：应用用户（飞书身份 open_id/union_id + 角色）。表名 app_user（避开 PG 保留字 user）。
- Role：角色枚举，以字符串存库（避开 PG enum 类型迁移麻烦）。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from recruitment_assistant.storage.resume_db import ResumeBase


class Role(str, enum.Enum):
    admin = "admin"        # 管理员：全租户可见 + 用户管理
    manager = "manager"    # 用人经理：团队可见
    recruiter = "recruiter"  # 招聘专员：仅本人归属


class Organization(ResumeBase):
    __tablename__ = "organization"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feishu_tenant_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class User(ResumeBase):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organization.id"), index=True, nullable=False
    )
    feishu_open_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    feishu_union_id: Mapped[str | None] = mapped_column(String(64), index=True)
    feishu_user_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(128))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    email: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(20), default=Role.recruiter.value, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
