"""Short specimen names for screens and workbooks.

Specimen labels are '<export file name>_S<n>'. File names from the testing
machine tend to be long and to repeat the same words ('compression test',
'Druckversuch', ...), so a chart legend or a table column full of them is
hard to read. The short name keeps what tells specimens apart: the rest of
the file name and the specimen number, e.g. 'Demo-A · S1'. The full label
stays in the records and is shown on hover and on the Traceability sheet.
"""

from __future__ import annotations

import re
from typing import Iterable

MAX_FILE_PART = 28

_SAMPLE = re.compile(r"[_\s-]+(S\d+)$", re.IGNORECASE)
_ARCHIVE_PREFIX = re.compile(r"^[0-9a-f]{12}_")
_FILLER = re.compile(
    r"\b(?:compression[\s-]*tests?|druck[\s_-]*versuch(?:e|s)?|druck[\s_-]*prüfung(?:en)?|"
    r"mehrstufen(?:versuch(?:e|s)?|prüfung(?:en)?)?|mehrstufige[rns]?|mehrstufig|"
    r"multi[\s_-]*stage|series|serie)\b",
    re.IGNORECASE,
)


def _without_material(stem: str, material: str | None) -> str:
    """Drop the material name from the file part: it is already shown in
    the heading (Results, Excel) or next to the name (Compare)."""
    if not material:
        return stem
    words = [re.escape(w) for w in re.split(r"[\s_-]+", str(material).strip()) if w]
    if not words:
        return stem
    pattern = r"(?<![0-9A-Za-zÄÖÜäöüß])" + r"[\s_-]*".join(words) + r"(?![0-9A-Za-zÄÖÜäöüß])"
    return re.sub(pattern, " ", stem, flags=re.IGNORECASE)


def short_name(label: str, material: str | None = None) -> str:
    """'Demo-A compression test_S1' -> 'Demo-A · S1'; with the material
    given, its name is left out too ('Druckversuch Mehrstufen_TALC050AN1_S1'
    with material 'TALC050AN1' -> 'S1')."""
    label = str(label or "").strip()
    m = _SAMPLE.search(label)
    sample = m.group(1).upper() if m else ""
    stem = label[: m.start()] if m else label
    stem = _without_material(_ARCHIVE_PREFIX.sub("", stem), material).replace("_", " ")
    stem = _FILLER.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem)
    stem = re.sub(r"\s*-\s*$|^\s*-\s*", "", stem).strip(" -_.")
    if len(stem) > MAX_FILE_PART:
        stem = stem[: MAX_FILE_PART - 1].rstrip() + "…"
    if stem and sample:
        return f"{stem} · {sample}"
    return stem or sample or label


def short_names(labels: Iterable[str], materials: Iterable[str | None] | None = None) -> list[str]:
    """short_name for each label, made unique: a short name shared by two
    different labels gets ' (2)', ' (3)' ... so no two entries look alike."""
    labels = list(labels)
    mats = list(materials) if materials is not None else [None] * len(labels)
    out = [short_name(lbl, m) for lbl, m in zip(labels, mats)]
    seen: dict[str, int] = {}
    first_label: dict[str, str] = {}
    for i, (name, lbl) in enumerate(zip(out, labels)):
        if name not in first_label:
            first_label[name] = lbl
            seen[name] = 1
        elif first_label[name] != lbl:
            seen[name] += 1
            out[i] = f"{name} ({seen[name]})"
    return out
