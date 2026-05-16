import socket
import subprocess
import sys
from pathlib import Path


STREAMLIT_PORT = 8501
BOSS_WS_PORT = 8765


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    streamlit_running = is_port_open(STREAMLIT_PORT)
    boss_ws_running = is_port_open(BOSS_WS_PORT)
    if streamlit_running or boss_ws_running:
        print(
            "检测到服务已在运行，已阻止重复启动："
            f"Streamlit 8501={'已占用' if streamlit_running else '未占用'}，"
            f"BOSS WebSocket 8765={'已占用' if boss_ws_running else '未占用'}。"
        )
        print("请访问 http://127.0.0.1:8501；如需重启，请先停止旧进程或使用 .codebuddy\\restart_streamlit.bat。")
        return
    subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app/main.py",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(STREAMLIT_PORT),
        ],
        cwd=root,
        check=True,
    )


if __name__ == "__main__":
    main()
