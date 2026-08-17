"""
cli.py
======
Command line front end. The Streamlit UI will call the same functions.

    python -m compression_tool preview  Mehrstufiger.xlsx
    python -m compression_tool ingest   Mehrstufiger.xlsx --material PEEK-GF30
    python -m compression_tool list     --material PEEK-GF30
    python -m compression_tool rebuild
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import fields
from pathlib import Path
from typing import Optional, Sequence

from . import knowledge_base
from .core import Config
from .persistence import Workspace
from .pipeline import ingest, preview, rebuild_index

DEFAULT_WORKSPACE = "./data"


def _add_config_args(parser: argparse.ArgumentParser) -> None:
    """Expose every Config knob as --flag, so nothing has to be hand-edited in
    source to try a different threshold."""
    group = parser.add_argument_group(
        "analysis settings", "All defaults are relative to the test's own peak stress."
    )
    for f in fields(Config):
        flag = "--" + f.name.replace("_", "-")
        if f.type in ("int", int):
            group.add_argument(flag, type=int, default=None, metavar="N")
        elif f.type in ("Optional[float]",):
            group.add_argument(flag, type=float, default=None, metavar="X")
        else:
            group.add_argument(flag, type=float, default=None, metavar="X")


def _config_from_args(args: argparse.Namespace) -> Config:
    cfg = Config()
    for f in fields(Config):
        value = getattr(args, f.name, None)
        if value is not None:
            setattr(cfg, f.name, value)
    return cfg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compression_tool",
        description="Analyse and archive cyclic compression tests.",
    )
    parser.add_argument("-w", "--workspace", default=DEFAULT_WORKSPACE,
                        help=f"data root (default: {DEFAULT_WORKSPACE})")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prev = sub.add_parser("preview", help="analyse without writing anything")
    p_prev.add_argument("files", nargs="+")
    _add_config_args(p_prev)

    p_ing = sub.add_parser("ingest", help="archive, analyse, persist and index")
    p_ing.add_argument("files", nargs="+")
    p_ing.add_argument("-m", "--material", default=None,
                       help="material name; defaults to the file stem")
    p_ing.add_argument("--no-index", action="store_true",
                       help="write the records but leave the database untouched")
    _add_config_args(p_ing)

    p_list = sub.add_parser("list", help="list indexed specimens")
    p_list.add_argument("-m", "--material", default=None)

    sub.add_parser("rebuild", help="regenerate the database from the records on disk")
    sub.add_parser("materials", help="list known materials")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    ws = Workspace.at(args.workspace)

    if args.command == "preview":
        rows = preview(args.files, _config_from_args(args))
        for row in rows:
            if "error" in row:
                print(f"{Path(row['source_file']).name}: FAILED - {row['error']}")
                continue
            stage = "multi-stage" if row["multi_stage"] else "constant-amplitude"
            print(
                f"{row['label']}: {row['source_format']} format, {row['n_points']} samples, "
                f"{row['n_cycles']} cycles ({row['n_holds']} with a hold), "
                f"peak {row['global_peak_mpa']:.1f} MPa, {stage}, "
                f"channel '{row['displacement_channel']}', "
                f"h0 {'%.3f mm' % row['h0_mm'] if row['h0_mm'] else 'unknown'}"
            )
            for note in row["notes"]:
                print(f"    note: {note}")
        return 0 if any("error" not in r for r in rows) else 1

    if args.command == "ingest":
        result = ingest(
            args.files, ws,
            material=args.material,
            cfg=_config_from_args(args),
            update_index=not args.no_index,
        )
        print(result.summary())
        return 0 if result.specimens else 1

    if args.command == "rebuild":
        count = rebuild_index(ws)
        print(f"Rebuilt {ws.db_path} from {count} record(s).")
        return 0

    conn = knowledge_base.connect(ws.db_path)
    try:
        if args.command == "materials":
            for name in knowledge_base.materials(conn):
                print(name)
            return 0

        df = knowledge_base.list_specimens(conn, args.material)
        if df.empty:
            print("No specimens indexed. Run 'ingest', or 'rebuild' if the "
                  "database was deleted.")
            return 1
        view = df[["label", "material", "n_cycles", "global_peak_mpa",
                   "multi_stage", "h0_mm", "created_utc"]]
        print(view.to_string(index=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
