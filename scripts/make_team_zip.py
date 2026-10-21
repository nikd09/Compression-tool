"""Build the zip that gets handed to the team.

Run from the tool folder:  python scripts\\make_team_zip.py
The zip lands next to the tool folder, named CompressLab_<date>.zip.

Only what is needed to run the tool goes in. Development files (version
control, tests, CI, notes, caches, local data, virtual environments) are
left out. Word guides placed in docs/team/ are added under Guides/.
"""

from __future__ import annotations

import argparse
import datetime as dt
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = "CompressLab"

INCLUDE_FILES = [
    "pyproject.toml",
    "requirements.txt",
    ".streamlit/config.toml",
    "scripts/run_webapp.bat",
]
INCLUDE_DIRS = ["compression_tool"]
SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}

# pyproject.toml names a README; pip refuses to install without it.
README_TEXT = (
    "CompressLab\n"
    "===========\n\n"
    "To start the tool, double-click scripts\\run_webapp.bat.\n"
    "See the guides in the Guides folder.\n"
)


def _files() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for rel in INCLUDE_FILES:
        out.append((ROOT / rel, rel))
    for d in INCLUDE_DIRS:
        for path in sorted((ROOT / d).rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.suffix in SKIP_SUFFIXES:
                continue
            out.append((path, path.relative_to(ROOT).as_posix()))
    guides = ROOT / "docs" / "team"
    if guides.is_dir():
        for path in sorted(guides.glob("*.docx")):
            if not path.name.startswith("~$"):  # Word's lock files
                out.append((path, f"Guides/{path.name}"))
    return out


def build(target: Path) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, rel in _files():
            data = src.read_bytes()
            if rel.endswith(".bat"):
                # cmd.exe wants Windows line endings.
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            zf.writestr(f"{TOP}/{rel}", data)
        zf.writestr(f"{TOP}/README.md", README_TEXT)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=None, help="where to write the zip")
    args = parser.parse_args()
    target = args.out or ROOT.parent / f"CompressLab_{dt.date.today():%Y-%m-%d}.zip"
    build(target)
    print(f"Team zip written: {target}")


if __name__ == "__main__":
    main()
