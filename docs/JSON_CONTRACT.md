# JSON contract — v2 (frozen)

The record written to `processed_output/<run>/<specimen>.json` is the source of
truth for every consumer: the workbook, the HTML report, the CSV, the SQLite
index and the UI. This document is the contract they are built against.

**Frozen as of schema_version 2.** Within a version the shape is
**additive-only**: a new key may be added, an existing key may not be renamed,
retyped or removed. Anything else requires bumping `SCHEMA_VERSION`.

`tests/test_json_contract.py` pins the exact key set at every level, so a change
to the shape has to be a deliberate edit to that test.

---

## Top level

```jsonc
{
  "schema_version": 2,                  // int
  "created_utc": "2026-08-17T12:00:00+00:00",
  "specimen":  { ... },
  "analysis":  { ... },
  "config":    { ... },                 // every Config field, verbatim
  "cycles":    [ { ... } ]              // one object per cycle, in order
}
```

## `specimen`

| Key | Type | Notes |
|---|---|---|
| `specimen_id` | string | 16 hex chars. Stable across rebuilds and workspaces — derived from the source hash and label, so it is safe to use as a UI key. |
| `label` | string | From the export. |
| `material` | string | Given at ingest. |
| `source_filename` | string | Original filename, e.g. `Mehrstufiger_....xlsx`. **Use this for anything operator-facing** — "which file was this". Added after v2 was frozen; additive, so schema_version stayed at 2 — see the changelog at the bottom of this document. |
| `source_file` | string | Full path as supplied at ingest, **on whatever machine ran the ingest**. May be a temporary or session-specific path (a CI runner, a sandbox) and is not meaningful to a reader on a different machine. Kept for provenance only — do not display this as "the source file" in a UI; use `source_filename` or `raw_input_path`. |
| `source_format` | string | `series` or `single`. |
| `source_sha256` | string | Content hash of the original export. |
| `raw_input_path` | string \| null | Relative to the workspace root. |
| `displacement_channel` | string | Channel the signal came from. |
| `h0_mm`, `d0_mm`, `temperature_c` | number \| null | From the metadata sheet where present. |
| `n_points` | int | Valid samples after cleaning. |
| `notes` | string[] | Loader notes, e.g. which of several channels was used. |

## `analysis`

| Key | Type | Notes |
|---|---|---|
| `n_cycles` | int | |
| `global_peak_mpa` | number | Highest stress anywhere in the test. |
| `multi_stage` | bool | Peaks vary >5% of the global peak — cycles are stages, not repeats, and must not be averaged. |
| `ref_stress_mpa` | number | Common stress for cross-cycle displacement reads. |
| `residual_stress_mpa` | number | Low common stress for the residual read. |
| `h0_mm` | number \| null | |
| `has_strain` | bool | Whether the strain keys are present on cycles. |
| `notes` | string[] | |
| `strain_basis` | object | See below. |
| `warnings` | object[] | See below. |

### `analysis.strain_basis`

Records what strain was divided by and whether anyone checked it.

```jsonc
{
  "h0_mm": 0.471,
  "displacement_channel": "Sonder LÄA",
  "gauge_length_confirmed": false,      // asserted at ingest, never inferred
  "strain_valid": false                 // has_strain AND gauge_length_confirmed
}
```

**`strain_valid` is the gate.** When it is `false`, every strain and any derived
modulus is provisional and the UI must present it as such. Nothing in an export
proves what the extensometer was clamped across, so the tool never assumes it —
the default is `false` and it only becomes `true` when a human asserts it via
`ingest(..., gauge_length_confirmed=True)`.

Stress-based metrics — peak stress, stiffness in MPa/mm, energy, hysteresis
loss — are **unaffected** by this flag and remain valid either way.

### `analysis.warnings`

```jsonc
[ { "code": "gauge_length_unconfirmed", "severity": "critical", "message": "…" } ]
```

Ordered worst-first. `severity` is one of:

| Severity | Meaning for the reader |
|---|---|
| `critical` | A number may be wrong. Do not quote it until resolved. |
| `caution` | The number is right but is easily misread. |
| `info` | Worth knowing, no action. |

Codes a consumer may rely on (new codes may be added; existing ones keep their
meaning):

| Code | Severity | Fires when |
|---|---|---|
| `gauge_length_unconfirmed` | critical | `has_strain` but the gauge length has not been confirmed. |
| `no_gauge_length` | info | No h0, so strain keys are absent rather than estimated. |
| `first_cycle_near_discard_threshold` | caution / critical | The smallest cycle clears `major_cycle_frac × global peak` by a thin margin. Losing cycle 1 rebases every cumulative figure. Escalates to `critical` within 5% of the cliff. |
| `cycles_discarded_by_peak_filter` | caution | Long-enough runs were dropped for peaking too low. Message names their peaks — a low-stress run at the start is normally the machine finding contact. |
| `variable_dwell_length` | caution | Hold lengths differ by >10%, so hold displacement is not comparable as a raw total. |

## `cycles[]`

Always present:

`Cycle`, `PeakStress_MPa`, `PeakDisp_mm`, `MaxDisp_mm`,
`StressAtMaxDisp_MPa`, `ResidualDisp_mm`,
`PermDef_cumulative_mm`, `PermDef_incremental_mm`,
`Stiffness_common_MPa_per_mm`, `Stiffness_common_n`, `Stiffness_common_r2`,
`Stiffness_relative_MPa_per_mm`, `Stiffness_relative_n`, `Stiffness_relative_r2`,
`DispAtRef_load_mm`, `DispAtRef_unload_mm`,
`Energy_in_MPa_mm`, `Energy_dissipated_MPa_mm`, `HysteresisLoss_rel`,
`HoldDetected`, `HoldPoints`, `Creep_during_hold_mm`, `_start`, `_end`

Present only when `has_strain`:

`PeakStrain_pct`, `MaxStrain_pct`, `PermDef_cumulative_pct`,
`PermDef_incremental_pct`, `Creep_pct`

All values are numbers or `null`, except `HoldDetected` which is a bool.
Keys prefixed `_` are sample indices into the raw signal, for re-plotting.

---

## Reading rules the UI must honour

These are the distinctions the numbers do not carry on their own.

**Displacement has two meanings.** `PeakDisp_mm` is displacement at maximum
*stress*; `MaxDisp_mm` is the largest displacement in the cycle and is the point
the energy integrals split at. On T050E1 `MaxDisp_mm` exceeds `PeakDisp_mm` by
37% in cycle 8. Quote `MaxDisp_mm` for how far the specimen moved; the pair for
how much of that arrived after the stress peak. Where that maximum falls is
itself a reading — see `StressAtMaxDisp_MPa` below.

**Two stiffnesses, one comparable.** `Stiffness_common_*` uses an identical
stress window in every cycle and may be compared across stages, specimens and
materials. `Stiffness_relative_*` uses each cycle's own peak and rises as the
stages climb even when the material has not stiffened — never plot it across
stages.

**Energy is per unit area.** `MPa·mm` = work ÷ cross-sectional area. Divide by
`h0_mm` for work per unit volume (MPa) — and that conversion inherits the
`strain_valid` gate. `HysteresisLoss_rel` is a ratio and is immune.

**Hold length is mandatory context.** Never display `Creep_during_hold_mm`
without `HoldPoints` beside it: it is a total, not a rate, so a longer dwell
accumulates more at identical material behaviour. `schema.INSEPARABLE_PAIRS`
lists the pairs that must not be split.

**Per-sample is not a rate.** `HoldDisp_per_1000_samples_mm` (derived, not
stored) normalises away unequal dwell lengths so cycles can be *ranked*. It is
not a creep rate and must never be labelled or plotted as one — converting
samples to time needs a constant sampling interval, which the export does not
record. A rate in mm/s requires a time channel enabled at export.

**Maximum displacement is not always at the end of the dwell.** On an intact
specimen it is, and `StressAtMaxDisp_MPa` equals the peak. When it falls below
the peak the specimen went on compacting *while the load was being removed* —
still yielding on the unloading ramp. On T050E1 this reads ~0.9996 of peak for
cycles 1–5 and then steps to 0.88 / 0.66 / 0.71 / 0.49 (S1, from cycle 6). Dwell
ripple keeps an intact cycle a shade under 1.000, so read ≥ 0.997 as intact.
It is a step rather than a drift, which dates damage onset more sharply than the
stiffness rollover does.

**Fit quality is not measurement accuracy.** `Stiffness_common_quality`
(`ok` / `few points` / `nonlinear` / `none`) describes only how well a line
fitted the points. It says nothing about calibration, compliance, alignment or
dimensional tolerance. Label it *fit quality* in the UI, never *confidence* or
*accuracy* — a value can read `ok` and still be physically wrong.

**Peak stress is not strength.** The top stage is the programmed maximum, not a
UCS or failure stress. Never label it "strength" on an axis.

**Permanent deformation is not compression set.** It is a residual displacement
read on the loading branch at a low common stress. ASTM D395 / ISO 815
compression set is a different, long-duration static test.

**A mean across multi-stage cycles is not a single physical value.**
`HysteresisLoss_rel` climbed from 0.55 to 0.93 across the nine T050E1 stages —
it is not flat across a stress range. `summary_pairs()` labels the aggregate
"Mean hysteresis loss **across cycles**" only when `analysis.multi_stage` is
true, precisely so it cannot be read as one number the way it would be for a
constant-amplitude test. Do the same in the UI: never show a mean across a
multi-stage test's cycles without naming what it's averaged over, and prefer
the per-cycle table.

**`source_file` is not for display.** It is the full path on whatever machine
ran the ingest — a sandbox, a CI runner, someone's laptop — and is provenance,
not identity. Show `source_filename` (or `raw_input_path`, which is relative to
the workspace) wherever a user needs to know "which file was this".

**Warnings are file-level, not per-column.** When rendering more than one
specimen side by side, dedupe warnings with `diagnostics.distinct()` before
displaying them — do not iterate each specimen's own `analysis.warnings` and
show every one, or two specimens ingested under identical settings will show
the same paragraph twice.

**Confirming the gauge length is not the same question as picking the right h0
value.** A modulus-plausibility check (does dividing by this h0 give a sane
modulus?) can rule out a *wrong* candidate — for T050E1 it rules out the 20 mm
crosshead reference length, which implies an impossible 96–192 GPa. It cannot
confirm that the extensometer *physically spans only the specimen*: a channel
that bridges extra material would still produce a plausible-looking modulus,
just the wrong one. `gauge_length_confirmed` is a human assertion about
fixturing, not something a plausibility check can set on your behalf.

---

## Derived values

Two columns are computed on read rather than stored, because they are pure
functions of stored keys. Both appear in the workbook, CSV, HTML and the SQLite
index; neither is in the JSON.

| Column | From | Function |
|---|---|---|
| `Stiffness_common_quality` | `Stiffness_common_n`, `Stiffness_common_r2` | `schema.stiffness_quality()` |
| `HoldDisp_per_1000_samples_mm` | `Creep_during_hold_mm`, `HoldPoints` | `schema.hold_disp_per_1000_samples()` |
| `UnloadYield_frac` | `StressAtMaxDisp_MPa`, `PeakStress_MPa` | `schema.unload_yield_frac()` |

Use those functions rather than reimplementing them, so the UI cannot drift from
the workbook.

A third function is a cross-cutting aggregate rather than a per-cycle value:
`excel_export.cross_specimen_stats(payloads)` returns mean / std / coefficient
of variation per cycle across two or more specimens — the same shape as the
source export's own `Statistik` sheet, extended to every cycle rather than a
single `Fmax` reading. Returns `[]` for a single specimen (nothing to compare)
so the caller can skip the section entirely, the same way the combined
workbook itself only appears for a multi-specimen ingest.

## Building against it

```python
from compression_tool.persistence import read_json
from compression_tool.schema import user_facing_cycle_columns
from compression_tool.excel_export import row_values, summary_pairs

payload = read_json(path)
columns = user_facing_cycle_columns(payload["analysis"]["has_strain"])
rows = [row_values(c, columns) for c in payload["cycles"]]
```

`user_facing_cycle_columns()` returns the display order with derived columns
already inserted next to what they qualify, and each `Column` carries its
`label`, `unit` and `description`. Rendering from it is what keeps the UI,
workbook and report showing the same thing.

---

## Changelog since the v2 freeze (all additive; schema_version stayed at 2)

- `specimen.source_filename` added — the original filename, for display.
  `specimen.source_file` (the full ingest-machine path) is unchanged but its
  label was clarified: it is provenance, not something to show a user.
- `excel_export.cross_specimen_stats()` added — mean/std/CoV per cycle across
  specimens, surfaced as a "Statistics" workbook sheet and an HTML section
  when a run has more than one specimen.
- `diagnostics.distinct()` added and became the one place every consumer
  (workbook, report, CLI) dedupes warnings across specimens — previously each
  kept its own copy of the same logic, and the workbook's Summary sheet did
  not dedupe at all, repeating each warning's full paragraph once per
  specimen column.
- `summary_pairs()`'s hysteresis-loss row is now labelled "Mean hysteresis
  loss **across cycles**" when `analysis.multi_stage` is true, rather than an
  unscoped "Mean hysteresis loss" that could be read as a single physical
  value across stress levels that are not comparable.
- `cycles[].StressAtMaxDisp_MPa` added, with derived `UnloadYield_frac` — the
  stress at which the specimen was most compressed, and that as a fraction of
  the cycle peak. Surfaces continued yielding on the unloading ramp, which no
  other column carried.
- `MaxDisp_mm`'s description corrected: it claimed the maximum always falls at
  the end of the dwell, which holds only while the specimen is intact.
- The combined run report's `<title>` no longer repeats the material name
  (was `"T050E1 - T050E1_2026-08-17"`; now `"T050E1 - 2026-08-17"`).
