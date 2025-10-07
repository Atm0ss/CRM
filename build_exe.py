"""Utility helpers to bundle the CRM backend as a standalone executable.

This script wraps PyInstaller to produce a Windows-friendly .exe from the
project's ``main.py`` entrypoint.  PyInstaller must be installed in the active
Python environment (``pip install -r requirements-build.txt``).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

try:
    import PyInstaller.__main__
except ModuleNotFoundError as exc:  # pragma: no cover - guidance for developers
    raise SystemExit(
        "PyInstaller is required. Install it with 'pip install -r "
        "requirements-build.txt' before running this script."
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parent
ENTRYPOINT = PROJECT_ROOT / "main.py"
DEFAULT_NAME = "crm"


def clean_previous_build_artifacts() -> None:
    """Remove PyInstaller build directories if they exist."""
    for folder in (PROJECT_ROOT / "build", PROJECT_ROOT / "dist"):
        if folder.exists():
            shutil.rmtree(folder)


def run_pyinstaller(name: str, extra_args: list[str] | None = None) -> None:
    """Invoke PyInstaller with the required arguments."""
    if extra_args is None:
        extra_args = []

    args = [
        str(ENTRYPOINT),
        f"--name={name}",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--collect-submodules=app",
    ] + extra_args

    PyInstaller.__main__.run(args)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bundle the CRM backend into a single-file executable using PyInstaller."
        )
    )
    parser.add_argument(
        "--name",
        default=DEFAULT_NAME,
        help="Name of the generated executable (default: %(default)s).",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Skip deleting previous build artifacts before running PyInstaller.",
    )
    parser.add_argument(
        "pyinstaller-args",
        nargs=argparse.REMAINDER,
        help="Additional options forwarded directly to PyInstaller.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(list(argv) if argv is not None else sys.argv[1:])

    if not ENTRYPOINT.exists():
        raise SystemExit("main.py was not found. Ensure you are in the project root.")

    if not args.no_clean:
        clean_previous_build_artifacts()

    extra_args = args.__dict__.get("pyinstaller-args") or []
    run_pyinstaller(args.name, extra_args)


if __name__ == "__main__":
    main()
