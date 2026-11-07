"""Cross-platform build automation for MatrixTrack."""

import os
import subprocess
import sys
from pathlib import Path


def ensure_pyinstaller():
    """Install PyInstaller if it is not already available."""
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=True,
        )


def main():
    ensure_pyinstaller()

    project_root = Path(__file__).resolve().parent
    entry_point = project_root / "matrixtrack" / "__main__.py"

    if not entry_point.is_file():
        raise FileNotFoundError(f"Entry point not found: {entry_point}")

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconsole",
        "--name=MatrixTrack",
        "--clean",
        str(entry_point),
    ]

    subprocess.run(command, check=True, cwd=project_root)
    print("Build completed successfully. Output is in the 'dist/' directory.")


if __name__ == "__main__":
    main()
