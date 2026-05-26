"""
简历智采助手 - 启动器
双击运行：启动 PostgreSQL → 初始化数据库 → 启动 Streamlit → 打开浏览器
"""
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
PYTHON_DIR = APP_ROOT / "python"
PGSQL_DIR = APP_ROOT / "pgsql"
PGDATA_DIR = APP_ROOT / "pgdata"
PYTHON_EXE = PYTHON_DIR / "python.exe"
PG_CTL = PGSQL_DIR / "bin" / "pg_ctl.exe"
INITDB = PGSQL_DIR / "bin" / "initdb.exe"
PSQL = PGSQL_DIR / "bin" / "psql.exe"

STREAMLIT_PORT = 8501
PG_PORT = 5432
DB_NAME = "recruitment_assistant"
DB_USER = "postgres"
DB_PASSWORD = "932092"

LOG_FILE = APP_ROOT / "logs" / "launcher.log"


def log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _short_path(p: Path, ensure_exists: bool = False) -> str:
    """将长路径转为 Windows 8.3 短路径，避免非 ASCII 字符导致 PostgreSQL 失败。

    GetShortPathNameW 只对已存在的路径有效。如果路径不存在且 ensure_exists=True，
    先创建目录再取短路径。对于祖先目录含中文的情况，逐级向上查找已存在的祖先取短路径。
    """
    import ctypes

    if ensure_exists and not p.exists():
        p.mkdir(parents=True, exist_ok=True)

    buf = ctypes.create_unicode_buffer(512)
    try:
        rv = ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, 512)
        if rv and rv < 512 and buf.value:
            return buf.value
    except Exception:
        pass
    return str(p)


def get_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = f"{PGSQL_DIR / 'bin'};{PYTHON_DIR};{PYTHON_DIR / 'Scripts'};{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(APP_ROOT)
    env["PGDATA"] = _short_path(PGDATA_DIR, ensure_exists=True)
    env["PGPORT"] = str(PG_PORT)
    env["PGUSER"] = DB_USER
    env["PGPASSWORD"] = DB_PASSWORD
    return env


def init_postgres() -> None:
    if not (PGDATA_DIR / "PG_VERSION").exists():
        log("Initializing PostgreSQL data directory...")
        pgdata_path = _short_path(PGDATA_DIR, ensure_exists=True)
        log(f"  PGDATA path: {pgdata_path}")
        result = subprocess.run(
            [_short_path(INITDB), "-D", pgdata_path, "-U", DB_USER, "-E", "UTF8", "--locale=C"],
            env=get_env(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            log(f"  initdb stdout: {result.stdout}")
            log(f"  initdb stderr: {result.stderr}")
            result.check_returncode()
        pg_hba = PGDATA_DIR / "pg_hba.conf"
        content = pg_hba.read_text(encoding="utf-8")
        content = content.replace("scram-sha-256", "trust").replace("md5", "trust")
        pg_hba.write_text(content, encoding="utf-8")
        log("PostgreSQL data directory initialized.")


def start_postgres() -> None:
    if is_port_open(PG_PORT):
        log("PostgreSQL already running.")
        return
    init_postgres()
    log("Starting PostgreSQL...")
    pgdata_path = _short_path(PGDATA_DIR)
    result = subprocess.run(
        [_short_path(PG_CTL), "start", "-D", pgdata_path, "-l", _short_path(APP_ROOT / "logs" / "postgresql.log"), "-w"],
        env=get_env(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        log(f"  pg_ctl stdout: {result.stdout}")
        log(f"  pg_ctl stderr: {result.stderr}")
        result.check_returncode()
    for _ in range(30):
        if is_port_open(PG_PORT):
            log("PostgreSQL started.")
            return
        time.sleep(0.5)
    raise RuntimeError("PostgreSQL failed to start within 15 seconds.")


def ensure_database() -> None:
    env = get_env()
    result = subprocess.run(
        [_short_path(PSQL), "-U", DB_USER, "-p", str(PG_PORT), "-lqt"],
        capture_output=True, text=True, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if DB_NAME not in result.stdout:
        log(f"Creating database '{DB_NAME}'...")
        subprocess.run(
            [_short_path(PSQL), "-U", DB_USER, "-p", str(PG_PORT), "-c", f"CREATE DATABASE {DB_NAME};"],
            env=env, creationflags=subprocess.CREATE_NO_WINDOW, check=True,
        )
    log("Running database migrations...")
    subprocess.run(
        [str(PYTHON_EXE), "-c", "from recruitment_assistant.storage.db import init_database; init_database()"],
        cwd=str(APP_ROOT), env=env,
        creationflags=subprocess.CREATE_NO_WINDOW, check=True,
    )


def start_streamlit() -> subprocess.Popen:
    log("Starting Streamlit...")
    env = get_env()
    proc = subprocess.Popen(
        [
            str(PYTHON_EXE), "-m", "streamlit", "run", "app/main.py",
            "--server.address", "127.0.0.1",
            "--server.port", str(STREAMLIT_PORT),
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ],
        cwd=str(APP_ROOT),
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return proc


def wait_and_open_browser() -> None:
    for _ in range(60):
        if is_port_open(STREAMLIT_PORT):
            log("Streamlit ready, opening browser.")
            webbrowser.open(f"http://127.0.0.1:{STREAMLIT_PORT}")
            return
        time.sleep(0.5)
    log("WARNING: Streamlit did not become ready within 30 seconds.")


def main() -> None:
    try:
        log("=" * 50)
        log("简历智采助手启动中...")
        start_postgres()
        ensure_database()
        proc = start_streamlit()
        wait_and_open_browser()
        log("All services started. Waiting for Streamlit process...")
        proc.wait()
    except Exception as e:
        log(f"ERROR: {e}")
        import traceback
        log(traceback.format_exc())
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, f"启动失败：{e}\n\n详情请查看 logs/launcher.log", "简历智采助手", 0x10
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
