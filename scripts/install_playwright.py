import subprocess
import sys


def main() -> None:
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)


if __name__ == "__main__":
    main()
