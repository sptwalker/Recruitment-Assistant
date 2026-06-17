"""
简历智采助手 - 主构建脚本
运行此脚本生成 dist/简历智采助手/ 目录，包含完整的可运行应用。
之后可用 Inno Setup 编译 installer.iss 生成安装包。

用法: python build/build_installer.py
"""
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

BUILD_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BUILD_DIR.parent
DIST_DIR = PROJECT_ROOT / "dist" / "简历智采助手"

PG_VERSION = "17.2-1"
PG_ZIP_URL = f"https://get.enterprisedb.com/postgresql/postgresql-{PG_VERSION}-windows-x64-binaries.zip"
PG_ZIP_NAME = f"postgresql-{PG_VERSION}-windows-x64-binaries.zip"

APP_DIRS = ["app", "recruitment_assistant", "icon", "chrome_extension"]
APP_FILES = ["pyproject.toml"]
DATA_DIRS = ["data/exports", "data/attachments/zhilian", "data/attachments/boss", "data/attachments/51job"]


def step(msg: str) -> None:
    print(f"\n{'='*60}\n  {msg}\n{'='*60}")


def download_postgres() -> Path:
    cache = BUILD_DIR / PG_ZIP_NAME
    if cache.exists():
        print(f"  Using cached {PG_ZIP_NAME}")
        return cache
    print(f"  Downloading PostgreSQL {PG_VERSION}...")
    urllib.request.urlretrieve(PG_ZIP_URL, cache)
    return cache


def extract_postgres(zip_path: Path) -> None:
    pg_dest = DIST_DIR / "pgsql"
    if pg_dest.exists():
        print("  PostgreSQL already extracted.")
        return
    print("  Extracting PostgreSQL (bin + lib + share only)...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if member.startswith("pgsql/bin/") or member.startswith("pgsql/lib/") or member.startswith("pgsql/share/"):
                zf.extract(member, DIST_DIR)


def setup_python_env() -> None:
    from setup_embedded_python import setup_embedded_python
    requirements = BUILD_DIR / "requirements-deploy.txt"
    setup_embedded_python(DIST_DIR / "python", requirements)


def copy_app_code() -> None:
    for dir_name in APP_DIRS:
        src = PROJECT_ROOT / dir_name
        dst = DIST_DIR / dir_name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".ruff_cache"))
    for file_name in APP_FILES:
        shutil.copy2(PROJECT_ROOT / file_name, DIST_DIR / file_name)


def create_data_dirs() -> None:
    for d in DATA_DIRS:
        (DIST_DIR / d).mkdir(parents=True, exist_ok=True)
    (DIST_DIR / "logs").mkdir(parents=True, exist_ok=True)


def create_env_file() -> None:
    env_content = """\
APP_ENV=local
DATABASE_URL=postgresql+psycopg://postgres:932092@localhost:5432/recruitment_assistant
CRAWLER_MIN_INTERVAL_SECONDS=8
CRAWLER_MAX_INTERVAL_SECONDS=30
CRAWLER_MAX_RESUMES_PER_TASK=50
EXPORT_DIR=data/exports
ATTACHMENT_DIR=data/attachments
BROWSER_STATE_DIR=data/browser_state
SNAPSHOT_DIR=data/snapshots
LOG_LEVEL=INFO
AI_API_KEY=
AI_BASE_URL=https://api.deepseek.com/v1
AI_MODEL=deepseek-chat
"""
    (DIST_DIR / ".env").write_text(env_content, encoding="utf-8")


def copy_launcher() -> None:
    shutil.copy2(BUILD_DIR / "launcher.pyw", DIST_DIR / "launcher.pyw")
    shutil.copy2(BUILD_DIR / "stop.bat", DIST_DIR / "stop.bat")


def main() -> None:
    step("1/6 Preparing dist directory")
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    step("2/6 Setting up embedded Python + dependencies")
    sys.path.insert(0, str(BUILD_DIR))
    setup_python_env()

    step("3/6 Downloading and extracting PostgreSQL")
    pg_zip = download_postgres()
    extract_postgres(pg_zip)

    step("4/6 Copying application code")
    copy_app_code()

    step("5/6 Creating data directories and config")
    create_data_dirs()
    create_env_file()
    copy_launcher()

    step("6/6 Build complete!")
    print(f"\n  Output: {DIST_DIR}")
    print(f"  To test: double-click {DIST_DIR / 'launcher.pyw'}")
    print(f"  To create installer: compile build/installer.iss with Inno Setup 6")


if __name__ == "__main__":
    main()
