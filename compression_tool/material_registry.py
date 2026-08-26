"""
material_registry.py
=====================
The controlled list of material names, shared by everyone who opens a
workspace: `<workspace>/materials.json`.

Exists because material is free text at the point it is most likely to be
mistyped -- Ingest, once per new test -- and a name is not just a label:
`specimen_id` (persistence.py) and every comparison in Results/Compare are
keyed by it. "SteelMesh", "Steel Mesh" and "steel-mesh" typed on three
different days become three materials that never compare against each
other, with no error and no warning that anything went wrong.

`ingest()` (pipeline.py) registers the material it is called with here
automatically, on every ingest, from every entry point (webapp, CLI,
scripts) -- so this list can never drift from what has actually been
ingested, and no caller has to remember to maintain it separately.
"""

from __future__ import annotations

from typing import Iterable

from . import knowledge_base
from .persistence import Workspace, read_json, write_json

_FILENAME = "materials.json"


def _normalize(name: str) -> str:
    """Casefolded, separator-stripped -- the key two names are compared BY,
    never shown. "Steel Mesh", "steel-mesh" and "SteelMesh" all normalize to
    the same key so the second and third are recognized as the first,
    rather than added as new, near-duplicate materials."""
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def load_materials(ws: Workspace) -> list[str]:
    """Every known material, alphabetically. Never raises and never blocks
    Ingest: a missing or unreadable materials.json falls back to whatever
    the index already knows about (real data, just without the curated
    ordering/aliasing a saved list carries), and only an index-free,
    file-free workspace returns empty."""
    path = ws.root / _FILENAME
    try:
        data = read_json(path)
        names = data.get("materials", [])
        if isinstance(names, list) and all(isinstance(n, str) for n in names):
            return sorted(names, key=str.casefold)
    except (OSError, ValueError):
        pass

    if ws.db_path.exists():
        conn = knowledge_base.connect(ws.db_path)
        try:
            return knowledge_base.materials(conn)
        finally:
            conn.close()
    return []


def _save(ws: Workspace, names: Iterable[str]) -> None:
    write_json({"materials": sorted(set(names), key=str.casefold)}, ws.root / _FILENAME)


def add_material(ws: Workspace, name: str) -> str:
    """Register `name`, or resolve it to an already-registered near-duplicate.

    Returns the CANONICAL name to actually use -- the one already on file,
    if `name` normalizes to match an existing entry, otherwise `name`
    itself (now newly on file). Callers that display what they used should
    compare their input to this return value: a difference means this
    silently matched an existing material rather than typing what was
    asked for a near-duplicate, and that is worth telling a user about
    rather than leaving them to notice their own text was not what stuck.
    """
    name = name.strip()
    if not name:
        raise ValueError("material name cannot be empty")

    existing = load_materials(ws)
    key = _normalize(name)
    for candidate in existing:
        if _normalize(candidate) == key:
            return candidate

    _save(ws, existing + [name])
    return name


def remove_material(ws: Workspace, name: str) -> None:
    """Drop `name` from the registry -- matched the same normalized way
    add_material() resolves a near-duplicate, so removing "PEEK-GF30"
    also removes an entry saved as "peek gf30". A no-op if it was not
    registered."""
    key = _normalize(name)
    remaining = [n for n in load_materials(ws) if _normalize(n) != key]
    _save(ws, remaining)
