"""FastAPI 应用工厂（M2 多用户 ATS 后端）。

M2.0：骨架 + 健康检查。后续里程碑挂载 auth / 业务路由。
用 `uvicorn backend.app.main:app --reload` 起服；数据库由 DATABASE_URL 决定
（未设则本地 SQLite，见 recruitment_assistant.storage.resume_db.resolve_db_url）。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.storage.db import create_session, init_database
from recruitment_assistant.storage.resume_db import resolve_db_url


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_database()  # alembic upgrade head（PG 或 SQLite）
    # 扩展上报事件 → 入库（BOSS 持久化路径）。crawl_ws 已在 tenant_scope 内调用它。
    from backend.app.crawl_hub import hub
    from backend.app.crawl_ingest import handle_boss_event
    hub.on_event = handle_boss_event
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="简历智采助手 API", version="M2", lifespan=_lifespan)

    # 前端 SPA 跨域（携带 cookie）。
    origins = [settings.frontend_origin]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from backend.app.auth.feishu import router as auth_router
    app.include_router(auth_router)

    from backend.app.routers import candidates, jobs, logs, crawl
    app.include_router(candidates.router)
    app.include_router(jobs.router)
    app.include_router(logs.router)
    app.include_router(crawl.router)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/healthz/db")
    def healthz_db() -> dict:
        with create_session() as s:
            s.execute(text("SELECT 1"))
        dialect = "postgresql" if resolve_db_url().startswith("postgresql") else "sqlite"
        return {"status": "ok", "dialect": dialect}

    return app


app = create_app()
