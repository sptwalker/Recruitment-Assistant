"""
简历智采助手 - 启动器
双击运行：启动 PostgreSQL → 初始化数据库 → 启动 Streamlit → 打开浏览器
"""
import ctypes
import ctypes.wintypes
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
PYTHON_DIR = APP_ROOT / "python"
PYTHON_EXE = PYTHON_DIR / "python.exe"

STREAMLIT_PORT = 8501
PG_PORT = 5432
DB_NAME = "recruitment_assistant"
DB_USER = "postgres"
DB_PASSWORD = "932092"

LOG_FILE = APP_ROOT / "logs" / "launcher.log"

_SAFE_BASE = Path(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", "C:\\Temp"))) / "ResumeAssistantPG"


def _is_ascii(p: Path) -> bool:
    try:
        str(p).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _create_junction(link: Path, target: Path) -> bool:
    """用 Windows API 创建 NTFS 目录联接，绕过 cmd.exe 编码问题。"""
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FSCTL_SET_REPARSE_POINT = 0x000900A4
    IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    link.mkdir(parents=True, exist_ok=True)

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    h = kernel32.CreateFileW(
        str(link), GENERIC_WRITE, 0, None, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    if h == INVALID_HANDLE_VALUE:
        return False
    try:
        sub_name = f"\\??\\{target}".encode("utf-16-le")
        print_name = str(target).encode("utf-16-le")
        path_buf = sub_name + b"\x00\x00" + print_name + b"\x00\x00"
        header = struct.pack(
            "<LHHHHHH",
            IO_REPARSE_TAG_MOUNT_POINT,
            8 + len(path_buf),
            0,
            0, len(sub_name),
            len(sub_name) + 2, len(print_name),
        )
        buf = header + path_buf
        out = ctypes.c_ulong(0)
        ok = kernel32.DeviceIoControl(
            ctypes.c_void_p(h), FSCTL_SET_REPARSE_POINT,
            buf, len(buf), None, 0, ctypes.byref(out), None,
        )
        return bool(ok)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(h))


def _safe_pgsql() -> Path:
    """返回一个纯 ASCII 的 pgsql 目录路径。

    PostgreSQL 二进制文件在启动时解析自身路径来定位 share/ 等目录。
    非 ASCII 路径会导致 UTF8 编码错误。解决：用 Windows NTFS junction
    在 %LOCALAPPDATA% 下创建纯 ASCII 的入口指向实际 pgsql 目录。
    """
    local_path = APP_ROOT / "pgsql"
    if _is_ascii(local_path):
        return local_path
    safe = _SAFE_BASE / "pgsql"
    target_exe = safe / "bin" / "initdb.exe"
    if target_exe.exists():
        return safe
    if safe.exists():
        shutil.rmtree(safe, ignore_errors=True)
    safe.parent.mkdir(parents=True, exist_ok=True)
    _create_junction(safe, local_path)
    if not target_exe.exists():
        raise RuntimeError(
            f"无法创建 pgsql 目录联接: {safe} -> {local_path}\n"
            "请尝试将软件安装到纯英文路径（如 C:\\ResumeAssistant）"
        )
    return safe


def _safe_pgdata() -> Path:
    """返回一个纯 ASCII 的 pgdata 路径。"""
    local_path = APP_ROOT / "pgdata"
    if _is_ascii(local_path):
        return local_path
    safe = _SAFE_BASE / "pgdata"
    safe.mkdir(parents=True, exist_ok=True)
    return safe


# PG 路径延迟解析：M1 后正常启动不再用 PG；仅老库一次性迁移时按需解析（避免全新安装因缺
# pgsql 二进制在模块加载期就崩）。
PGSQL_DIR = None
PG_CTL = None
INITDB = None
PSQL = None
PGDATA_DIR = None


def _resolve_pg_paths() -> None:
    """仅迁移分支调用：解析老版 pgsql/pgdata 路径。"""
    global PGSQL_DIR, PG_CTL, INITDB, PSQL, PGDATA_DIR
    if PGSQL_DIR is not None:
        return
    PGSQL_DIR = _safe_pgsql()
    PG_CTL = PGSQL_DIR / "bin" / "pg_ctl.exe"
    INITDB = PGSQL_DIR / "bin" / "initdb.exe"
    PSQL = PGSQL_DIR / "bin" / "psql.exe"
    PGDATA_DIR = _safe_pgdata()


def log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _safe_pg_log() -> str:
    """返回 PostgreSQL 日志文件路径（纯 ASCII）。"""
    local_log = APP_ROOT / "logs" / "postgresql.log"
    try:
        str(local_log).encode("ascii")
        return str(local_log)
    except UnicodeEncodeError:
        safe = Path(os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", "C:\\Temp")))
        safe = safe / "ResumeAssistantPG"
        safe.mkdir(parents=True, exist_ok=True)
        return str(safe / "postgresql.log")


def get_env() -> dict:
    env = os.environ.copy()
    parts = [str(PYTHON_DIR), str(PYTHON_DIR / "Scripts")]
    if PGSQL_DIR is not None:
        parts.insert(0, str(PGSQL_DIR / "bin"))
        env["PGDATA"] = str(PGDATA_DIR)
        env["PGPORT"] = str(PG_PORT)
        env["PGUSER"] = DB_USER
        env["PGPASSWORD"] = DB_PASSWORD
    env["PATH"] = ";".join(parts) + ";" + env.get("PATH", "")
    env["PYTHONPATH"] = str(APP_ROOT)
    return env


def init_postgres() -> None:
    if not (PGDATA_DIR / "PG_VERSION").exists():
        log("Initializing PostgreSQL data directory...")
        log(f"  PGDATA: {PGDATA_DIR}")
        PGDATA_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [str(INITDB), "-D", str(PGDATA_DIR), "-U", DB_USER, "-E", "UTF8", "--locale=C"],
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
    pg_log = _safe_pg_log()
    # 不用 -w 和 capture_output：pg_ctl 在 Windows 上 spawn postgres.exe 后，
    # 子进程会继承管道句柄，导致 subprocess.run 永远阻塞等待管道关闭。
    subprocess.run(
        [str(PG_CTL), "start", "-D", str(PGDATA_DIR), "-l", pg_log],
        env=get_env(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=15,
    )
    for _ in range(30):
        if is_port_open(PG_PORT):
            log("PostgreSQL started.")
            return
        time.sleep(0.5)
    log(f"PostgreSQL failed to start. Check log: {pg_log}")
    raise RuntimeError(f"PostgreSQL 启动超时，详见日志: {pg_log}")


def ensure_database() -> None:
    env = get_env()
    result = subprocess.run(
        [str(PSQL), "-U", DB_USER, "-p", str(PG_PORT), "-lqt"],
        capture_output=True, text=True, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if DB_NAME not in result.stdout:
        log(f"Creating database '{DB_NAME}'...")
        subprocess.run(
            [str(PSQL), "-U", DB_USER, "-p", str(PG_PORT), "-c", f"CREATE DATABASE {DB_NAME};"],
            env=env, creationflags=subprocess.CREATE_NO_WINDOW, check=True,
        )


def stop_postgres() -> None:
    try:
        subprocess.run(
            [str(PG_CTL), "stop", "-D", str(PGDATA_DIR), "-m", "fast"],
            env=get_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=15,
        )
        log("PostgreSQL stopped (migration done).")
    except Exception as exc:
        log(f"stop_postgres error (ignored): {exc}")


def ensure_sqlite_schema() -> None:
    """建/补齐统一 SQLite 库的全部表（子进程内 import 应用）。"""
    log("Ensuring SQLite schema...")
    r = subprocess.run(
        [str(PYTHON_EXE), "-c",
         "from recruitment_assistant.storage.db import init_database; init_database()"],
        cwd=str(APP_ROOT), env=get_env(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if r.returncode != 0:
        log(f"  schema stdout: {r.stdout}")
        log(f"  schema stderr: {r.stderr}")


def run_one_time_pg_migration() -> None:
    """老库升级：若存在 pgdata 且未迁移，起一次老 PG 把 5 张表搬进 SQLite，然后停 PG。

    全新安装（无 pgdata）或已迁移：仅调用迁移模块写 marker/跳过，不碰 PG。
    绝不阻塞启动。
    """
    marker = APP_ROOT / "data" / ".pg_migrated"
    pgdata = APP_ROOT / "pgdata"
    need_pg = (not marker.exists()) and pgdata.exists()

    if need_pg:
        try:
            log("Detected legacy pgdata + no migration marker → one-time PG→SQLite migration.")
            _resolve_pg_paths()
            start_postgres()
            ensure_database()
        except Exception as exc:
            log(f"PG start for migration failed (will still attempt/skip): {exc}")

    # 迁移模块自身幂等：老库拷数据 / 全新装写 marker 跳过
    r = subprocess.run(
        [str(PYTHON_EXE), "-c",
         "from recruitment_assistant.storage.migrate_pg_to_sqlite import migrate_if_needed;"
         " print(migrate_if_needed())"],
        cwd=str(APP_ROOT), env=get_env(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    log(f"  migration result: {(r.stdout or '').strip()} {(r.stderr or '').strip()}")

    if need_pg:
        stop_postgres()


def main() -> None:
    try:
        log("=" * 50)
        log("简历智采助手启动中...")
        log(f"  APP_ROOT:  {APP_ROOT}")
        run_one_time_pg_migration()   # 老库一次性迁移；全新装直接跳过
        ensure_sqlite_schema()        # 建/补齐统一 SQLite 表
        proc = start_streamlit()
        wait_and_open_browser()
        log("All services started. Waiting for Streamlit process...")
        if proc is not None:
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


def start_streamlit() -> subprocess.Popen | None:
    if is_port_open(STREAMLIT_PORT):
        # 端口已被上次残留的 Streamlit 占用：复用它，不再 spawn 第二个（会 10048 崩溃）
        log(f"Streamlit already running on {STREAMLIT_PORT}, reusing.")
        return None
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


if __name__ == "__main__":
    main()
