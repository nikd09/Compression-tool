"""Excel download for the Compare page: every headline property per group
and stress step, with the same mean and SD the Compare chart draws."""

from __future__ import annotations

import io
from typing import Sequence

import pandas as pd
import xlsxwriter

from .excel_export import _col_label, excel_cycle_columns
from .naming import short_names

_T = {
    "groups_sheet": {"en": "Groups", "de": "Gruppen"},
    "title": {"en": "Comparison", "de": "Vergleich"},
    "note": {
        "en": "Each property sheet gives, per stress step, the mean, the standard "
              "deviation (SD) and the number of values (n) over all specimens in "
              "the group: the same numbers as the Compare chart.",
        "de": "Jedes Kennwertblatt zeigt je Laststufe den Mittelwert, die "
              "Standardabweichung (SD) und die Anzahl der Werte (n) über alle "
              "Proben der Gruppe: dieselben Zahlen wie im Vergleichsdiagramm."},
    "group": {"en": "Group", "de": "Gruppe"},
    "material": {"en": "Material", "de": "Material"},
    "specimen": {"en": "Specimen", "de": "Probe"},
    "full_name": {"en": "Full name", "de": "Vollständiger Name"},
    "stress": {"en": "Stress step (MPa)", "de": "Laststufe (MPa)"},
    "mean": {"en": "mean", "de": "Mittelwert"},
}

# Excel allows 31 characters in a sheet name, so the long property names get
# a short sheet name; the full name and unit stand in the sheet's first row.
_SHEET = {
    "Stiffness_common_MPa_per_mm": ("Stiffness common", "Steifigkeit gemeinsam"),
    "Stiffness_relative_MPa_per_mm": ("Stiffness relative", "Steifigkeit eigen"),
    "MaxDisp_mm": ("Max displacement", "Max Weg"),
    "PermDef_cumulative_mm": ("Total deformation mm", "Gesamtverformung mm"),
    "PermDef_cumulative_pct": ("Total deformation %", "Gesamtverformung %"),
    "Creep_during_hold_mm": ("Creep", "Kriechweg"),
    "Energy_dissipated_MPa_mm": ("Energy dissipated", "Dissipierte Energie"),
}


def _stats(df: pd.DataFrame, ids: Sequence[str], key: str) -> pd.DataFrame:
    sub = df[df["specimen_id"].isin(ids)]
    rows = []
    for level, part in sub.groupby("StressLevel_MPa"):
        vals = pd.to_numeric(part[key], errors="coerce").dropna()
        if vals.empty:
            continue
        rows.append({"stress": float(level), "mean": float(vals.mean()),
                     "sd": float(vals.std(ddof=0)) if len(vals) > 1 else 0.0,
                     "n": int(len(vals))})
    return pd.DataFrame(rows, columns=["stress", "mean", "sd", "n"])


def compare_workbook(
    df: pd.DataFrame, groups: Sequence[dict], label_by_id: dict, material_by_id: dict,
    lang: str = "en",
) -> bytes:
    """`df`: the cycle rows of every specimen in the groups, with a
    StressLevel_MPa column. `groups`: [{"name": ..., "ids": [...]}, ...]."""
    t = lambda k: _T[k][lang]  # noqa: E731
    has_strain = "PermDef_cumulative_pct" in df.columns and df["PermDef_cumulative_pct"].notna().any()
    cols = [c for c in excel_cycle_columns(bool(has_strain))
            if c.key in _SHEET and c.key in df.columns]

    buf = io.BytesIO()
    book = xlsxwriter.Workbook(buf, {"in_memory": True, "nan_inf_to_errors": True})
    title = book.add_format({"bold": True, "font_size": 14})
    head = book.add_format({"bold": True, "text_wrap": True, "valign": "bottom",
                            "bg_color": "#1F3864", "font_color": "#FFFFFF", "border": 1})
    wrap = book.add_format({"text_wrap": True, "valign": "top"})
    step_fmt = book.add_format({"num_format": "0"})
    n_fmt = book.add_format({"num_format": "0"})

    # --- Groups --------------------------------------------------------------
    sheet = book.add_worksheet(t("groups_sheet"))
    sheet.write(0, 0, t("title"), title)
    sheet.merge_range(1, 0, 1, 3, t("note"), wrap)
    sheet.set_row(1, 48)
    for i, h in enumerate((t("group"), t("material"), t("specimen"), t("full_name"))):
        sheet.write(3, i, h, head)
    all_ids = list(dict.fromkeys(sid for g in groups for sid in g["ids"]))
    short = dict(zip(all_ids, short_names(label_by_id.get(s, s) for s in all_ids)))
    r = 4
    for g in groups:
        for sid in g["ids"]:
            sheet.write(r, 0, g["name"])
            sheet.write(r, 1, str(material_by_id.get(sid, "")))
            sheet.write(r, 2, short[sid])
            sheet.write(r, 3, str(label_by_id.get(sid, sid)))
            r += 1
    sheet.set_column(0, 1, 22)
    sheet.set_column(2, 2, 26)
    sheet.set_column(3, 3, 48)

    # --- one sheet per property ---------------------------------------------
    for col in cols:
        name = _SHEET[col.key][1 if lang == "de" else 0]
        sheet = book.add_worksheet(name)
        label = _col_label(col, lang) + (f" ({col.unit})" if col.unit else "")
        sheet.write(0, 0, label, title)
        stats = {g["name"]: _stats(df, g["ids"], col.key) for g in groups}
        levels = sorted({s for st in stats.values() for s in st["stress"]})
        sheet.write(2, 0, t("stress"), head)
        c = 1
        for g in groups:
            for h in (f"{g['name']} {t('mean')}", f"{g['name']} SD", f"{g['name']} n"):
                sheet.write(2, c, h, head)
                c += 1
        num = book.add_format({"num_format": col.fmt})
        for j, level in enumerate(levels):
            row = 3 + j
            sheet.write_number(row, 0, level, step_fmt)
            c = 1
            for g in groups:
                st = stats[g["name"]]
                hit = st[st["stress"] == level]
                if hit.empty:
                    c += 3
                    continue
                sheet.write_number(row, c, hit["mean"].iloc[0], num)
                sheet.write_number(row, c + 1, hit["sd"].iloc[0], num)
                sheet.write_number(row, c + 2, hit["n"].iloc[0], n_fmt)
                c += 3
        sheet.set_row(2, 32)
        sheet.set_column(0, 0, 16)
        sheet.set_column(1, max(1, c - 1), 14)
        sheet.freeze_panes(3, 1)

    book.close()
    return buf.getvalue()
