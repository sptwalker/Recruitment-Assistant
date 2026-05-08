import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "app/main.py"],
        cwd=root,
        check=True,
    )


if __name__ == "__main__":
    main()
