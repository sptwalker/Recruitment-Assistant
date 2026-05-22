"""
配置嵌入式 Python 环境：解压标准库、启用 site-packages、安装 pip 和依赖。
"""
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PYTHON_VERSION = "3.11.9"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"


def setup_embedded_python(target_dir: Path, requirements_file: Path) -> None:
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    embed_zip = target_dir.parent / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    if not embed_zip.exists():
        print(f"Downloading Python {PYTHON_VERSION} embeddable package...")
        urllib.request.urlretrieve(PYTHON_EMBED_URL, embed_zip)
        print("Download complete.")

    if not (target_dir / "python.exe").exists():
        print("Extracting Python embeddable package...")
        with zipfile.ZipFile(embed_zip, "r") as zf:
            zf.extractall(target_dir)

    pth_file = target_dir / "python311._pth"
    if pth_file.exists():
        print("Configuring python311._pth...")
        lines = pth_file.read_text(encoding="utf-8").splitlines()
        new_lines = []
        for line in lines:
            if line.strip() == "#import site":
                new_lines.append("import site")
            else:
                new_lines.append(line)
        if "Lib" not in "\n".join(new_lines):
            new_lines.insert(0, "Lib")
            new_lines.insert(1, "Lib/site-packages")
        pth_file.write_text("\n".join(new_lines), encoding="utf-8")

    stdlib_zip = target_dir / "python311.zip"
    lib_dir = target_dir / "Lib"
    if stdlib_zip.exists() and not lib_dir.exists():
        print("Extracting standard library...")
        lib_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(stdlib_zip, "r") as zf:
            zf.extractall(lib_dir)
        stdlib_zip.unlink()

    site_packages = target_dir / "Lib" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)

    python_exe = target_dir / "python.exe"
    pip_exe = target_dir / "Scripts" / "pip.exe"
    if not pip_exe.exists():
        get_pip = target_dir.parent / "get-pip.py"
        if not get_pip.exists():
            print("Downloading get-pip.py...")
            urllib.request.urlretrieve(GET_PIP_URL, get_pip)
        print("Installing pip...")
        subprocess.run(
            [str(python_exe), str(get_pip), "--no-warn-script-location"],
            check=True,
        )

    print(f"Installing dependencies from {requirements_file.name}...")
    subprocess.run(
        [
            str(python_exe), "-m", "pip", "install",
            "-r", str(requirements_file),
            "--no-warn-script-location",
            "--disable-pip-version-check",
        ],
        check=True,
    )
    print("Embedded Python environment ready.")


if __name__ == "__main__":
    build_dir = Path(__file__).resolve().parent
    dist_dir = build_dir.parent / "dist" / "简历智采助手"
    requirements = build_dir / "requirements-deploy.txt"
    setup_embedded_python(dist_dir / "python", requirements)
