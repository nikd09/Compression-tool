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

from . import audit, diagnostics, knowledge_base
from .core import Config
from .material_export import export_material
from .persistence import Workspace
from .pipeline import ingest, preview, rebuild_index
from .reports_overview import build_overview

DEFAULT_WORKSPACE = "./data"


def _add_config_args(parser: argparse.ArgumentParser) -> None:
    """Expose every Config knob as --flag, so nothing has to be hand-edited in
    source to try a different threshold."""
    group = parser.add_argument_group(
        "analysis settings", "All defaults are relative to the test's own peak stress."
    )
    for f in fields(Config):
        flag = "--" + f.name.replace("_", "-")
        if f.type in ("bool",):
            # --flag / --no-flag, defaulting to None (Config's own default
            # applies) rather than to False -- a plain store_true could not
            # tell "not passed" from "explicitly disabled".
            group.add_argument(flag, action=argparse.BooleanOptionalAction, default=None)
        elif f.type in ("int", int):
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
    p_prev.add_argument("--gauge-length-confirmed", action="store_true",
                        help="assert that the displacement channel spans only h0")
    _add_config_args(p_prev)

    p_ing = sub.add_parser("ingest", help="archive, analyse, persist and index")
    p_ing.add_argument("files", nargs="+")
    p_ing.add_argument("-m", "--material", required=True,
                       help="material name, e.g. PEEK-GF30 -- required; a "
                            "short material code, not the export's file name")
    p_ing.add_argument("--no-index", action="store_true",
                       help="write the records but leave the database untouched")
    p_ing.add_argument("--gauge-length-confirmed", action="store_true",
                       help="assert that the displacement channel spans only h0; "
                            "without it, strain and modulus are marked provisional")
    p_ing.add_argument("--no-archive", action="store_true",
                       help="do not copy the export into Raw exports/ -- only its "
                            "SHA-256 is recorded")
    p_ing.add_argument("--no-reports", action="store_true",
                       help="skip per-specimen/per-run Excel, CSV and HTML -- only "
                            "the JSON record, curve cache and the combined "
                            "reports/<material> export are written")
    _add_config_args(p_ing)

    p_list = sub.add_parser("list", help="list indexed specimens")
    p_list.add_argument("-m", "--material", default=None)

    sub.add_parser("rebuild", help="regenerate the database from the records on disk")
    sub.add_parser("materials", help="list known materials")

    p_exp = sub.add_parser(
        "export-material",
        help="(re)write the combined workbook + dashboard for one material",
    )
    p_exp.add_argument("material")

    sub.add_parser(
        "build-overview",
        help="(re)write the all-materials overview page (reports/_Overview.html)",
    )

    p_audit = sub.add_parser(
        "audit", help="who ingested what, and when -- most recent first"
    )
    p_audit.add_argument("-n", "--limit", type=int, default=20,
                         help="show at most this many records (default: 20)")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    ws = Workspace.at(args.workspace)

    if args.command == "preview":
        rows = preview(args.files, _config_from_args(args),
                       gauge_length_confirmed=args.gauge_length_confirmed)
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
            for w in row.get("warnings", []):
                print(f"    [{w['severity'].upper()}] {w['message']}")
        return 0 if any("error" not in r for r in rows) else 1

    if args.command == "ingest":
        result = ingest(
            args.files, ws,
            material=args.material,
            cfg=_config_from_args(args),
            update_index=not args.no_index,
            gauge_length_confirmed=args.gauge_length_confirmed,
            archive_originals=not args.no_archive,
            write_reports=not args.no_reports,
        )
        print(result.summary())
        for w in diagnostics.distinct(result.payloads):
            print(f"  [{w['severity'].upper()}] {w['message']}")
        return 0 if result.specimens else 1

    if args.command == "rebuild":
        count = rebuild_index(ws)
        print(f"Rebuilt {ws.db_path} from {count} record(s).")
        return 0

    if args.command == "export-material":
        exported = export_material(ws, args.material)
        if not exported["xlsx"]:
            print(f"No indexed specimens for material {args.material!r}.")
            return 1
        print(f"Workbook  : {exported['xlsx']}")
        print(f"Dashboard : {exported['html']}")
        return 0

    if args.command == "build-overview":
        path = build_overview(ws)
        if not path:
            print("No indexed specimens in this workspace.")
            return 1
        print(f"Overview : {path}")
        return 0

    if args.command == "audit":
        entries = audit.list_entries(ws, limit=args.limit)
        if not entries:
            print("No audit records in this workspace.")
            return 1
        for e in entries:
            skipped = f", {len(e.get('skipped', []))} skipped" if e.get("skipped") else ""
            print(
                f"{e.get('timestamp_utc', '—')}  {e.get('user', '—')}@{e.get('host', '—')}  "
                f"{e.get('material', '—')}: {len(e.get('specimens', []))} specimen(s){skipped}"
                f"  -> {e.get('run_dir', '—')}"
            )
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
