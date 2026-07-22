# 后端镜像（FastAPI + 复用 recruitment_assistant/services）。
# 构建上下文 = 仓库根（见 docker-compose）。中国网络：Docker Hub 不可达时给 docker daemon
# 配 registry-mirror（如 docker.m.daocloud.io），镜像名保持规范不写死镜像站。
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1

# 先装依赖（含 recruitment_assistant 包与其运行期依赖）。playwright 只装 Python 包、
# 不下浏览器二进制——采集已改为扩展/WS 上报，服务端不跑浏览器。
COPY pyproject.toml README.md ./
COPY recruitment_assistant ./recruitment_assistant
RUN pip install .

# 再叠加完整源码：backend/、alembic 迁移脚本随包已在，PYTHONPATH=/app 让源码优先，
# 保证 migrations/ 与 backend/ 一定可用（wheel 未必带上迁移脚本）。
COPY backend ./backend
COPY alembic.ini ./

EXPOSE 8000
# 启动时 lifespan 会 alembic upgrade head（PG 或 SQLite，由 DATABASE_URL 决定）。
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
