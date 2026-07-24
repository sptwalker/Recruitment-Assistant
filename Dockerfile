FROM python:3.11-slim-bookworm AS builder

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN python -m venv "${VIRTUAL_ENV}"

WORKDIR /build
COPY pyproject.toml ./
COPY recruitment_assistant ./recruitment_assistant
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir .

FROM python:3.11-slim-bookworm AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    HOME=/tmp \
    TZ=Asia/Shanghai

COPY --from=builder /opt/venv /opt/venv

RUN python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app /var/lib/recruitment-assistant /opt/playwright \
    && chown -R app:app /var/lib/recruitment-assistant \
    && ln -s /var/lib/recruitment-assistant/data /app/data \
    && ln -s /var/lib/recruitment-assistant/logs /app/logs \
    && ln -s /var/lib/recruitment-assistant/config/app.env /app/.env

WORKDIR /app

COPY --chown=app:app app ./app
COPY --chown=app:app recruitment_assistant ./recruitment_assistant
COPY --chown=app:app chrome_extension ./chrome_extension
COPY --chown=app:app icon ./icon
COPY --chown=app:app alembic.ini pyproject.toml ./
COPY --chown=app:app scripts/analyze_test_run.py ./scripts/analyze_test_run.py
COPY --chown=app:app --chmod=0755 scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN mkdir -p /opt/recruitment-assistant-defaults \
    && cp -a /app/app/styles/themes /opt/recruitment-assistant-defaults/themes \
    && rm -rf /app/app/styles/themes \
    && ln -s /var/lib/recruitment-assistant/data/themes /app/app/styles/themes

USER app:app

VOLUME ["/var/lib/recruitment-assistant"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/recruitment-assistant/_stcore/health', timeout=3).read()" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["streamlit", "run", "app/main.py", "--server.address=0.0.0.0", "--server.port=8080", "--server.baseUrlPath=recruitment-assistant", "--server.headless=true", "--server.enableXsrfProtection=true"]
