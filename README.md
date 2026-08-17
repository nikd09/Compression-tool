# Compression Analysis Tool

Ingestion, metrics and persistence for load-controlled cyclic / multi-stage
compression tests exported from a Zwick Z100.

The calculation engine (`compression_tool/core.py`) is the validated reference
implementation described in [HANDOFF.md](HANDOFF.md) and is used unmodified.
This repository adds the persistence layer and the Excel export around it —
steps 1 and 2 of the build order in that brief.

## Install

```bash
pip install -e ".[dev]"
```

`numpy >= 2.0` is required: the energy integration uses `np.trapezoid`.

## Use

```bash
# Look before committing: format, cycles, holds, channel, h0 — writes nothing.
compression-tool preview Mehrstufiger.xlsx

# Archive, analyse, persist and index.
compression-tool -w ./data ingest Mehrstufiger.xlsx --material PEEK-GF30

# What is on record.
compression-tool -w ./data list
compression-tool -w ./data materials

# Regenerate the database from the records on disk.
compression-tool -w ./data rebuild
```

Every `Config` knob is available as a flag (`--unload-frac`, `--residual-stress-frac`, …),
so nothing has to be hand-edited in source to try a different threshold.

From Python:

```python
from compression_tool import Config, ingest, preview

preview(["Mehrstufiger.xlsx"])
result = ingest(["Mehrstufiger.xlsx"], "./data", material="PEEK-GF30")
print(result.summary())
```

## Layout

```
<workspace>/
  raw_input/                        immutable, content-addressed, read-only
    <sha12>_<original name>.xlsx
  processed_output/
    <material>_<YYYY-MM-DD>/
      run.json                      what was ingested, under which config
      <specimen>.json               the record — source of truth
      <specimen>.csv                per-cycle table, flat
      <specimen>.xlsx               per-cycle table + summary + dictionary
      <specimen>.html               standalone report
      <material>_<date>.xlsx        all specimens of the run, when >1
  knowledge_base.db                 SQLite index, rebuildable
```

Three properties this layout is built to hold:

- **The original export is never touched.** It is copied into `raw_input/`
  before anything is analysed — an export that later turns out to crash the
  engine is still preserved — and the copy is marked read-only. Re-ingesting
  the same file is a no-op rather than a silent replacement.
- **The JSON records are the source of truth.** Each one carries the metadata,
  the exact config used, the source file's hash and every per-cycle metric.
  A record plus the archived raw file is enough to reproduce the numbers.
- **The database is disposable.** `rebuild` throws it away and regenerates it
  from the records. Nothing is stored there that cannot be recovered, so a
  schema change is a rebuild rather than a migration.

Re-running the same sources with the same config on the same day overwrites in
place. Changing the config gives the run its own folder, so a changed result
never displaces the one it should be compared against.

## The workbook

Four sheets:

| Sheet | Contents |
|---|---|
| Summary | Identity, provenance and whole-test aggregates. Fields down the page, specimens across it, so a two-specimen series reads side by side. |
| Cycles | The flat per-cycle table. Real headers with units, frozen panes, autofilter. |
| Data dictionary | What every column means, generated from the schema. |
| Config | The settings behind the numbers, plus the derived reference levels. |

The data dictionary is not decoration. The per-cycle table carries two
stiffness columns that look interchangeable and are not, and a permanent
deformation column that is **not** compression set in the ASTM D395 / ISO 815
sense. Anyone reading the workbook without the surrounding conversation needs
those distinctions in the file itself.

### Reading the numbers

- **Stiffness (common band)** — fitted over an identical stress window in every
  cycle (25–75% of the smallest cycle peak). This is the one that may be
  compared across stages, specimens and materials.
- **Stiffness (relative band)** — fitted over 25–75% of each cycle's own peak.
  Describes the cycle faithfully but rises as the stages climb even when the
  material has not stiffened. Not comparable across cycles.
- **Stiffness quality** — `ok`, `few points`, `nonlinear` or `none`, derived
  from the fit's n and R². A slope from a handful of points on a fast machine
  ramp is not trustworthy; the flag says so instead of letting it be plotted as
  solid.
- **Hysteresis loss** — dissipated ÷ input. The cross-test comparable form;
  absolute loss scales with stress amplitude.
- **Permanent deformation** — residual displacement read on the *loading*
  branch at a low common stress, not at zero, because the specimen loses
  contact at zero and the signal falls back to a few-micrometre baseline.
- **Hold length** — in **samples**, not seconds. Neither export carries a time
  channel, so creep *rate* cannot be computed, only total creep across the
  dwell. Enabling a time channel in the export settings would lift that.

## Tests

```bash
pytest
```

83 tests run against synthetic exports built to reproduce the three behaviours
the real sample data revealed: rising stage peaks, a dwell during which
displacement keeps climbing after stress has levelled off, and a collapse to a
few-micrometre baseline at near-zero stress. Those are what the engine's less
obvious choices exist for, so a synthetic signal without them would test
nothing that matters. The permanent-set pin is checked against a closed-form
value rather than a recorded output.

A further 10 in `tests/test_regression_real_files.py` run against the real
`Mehrstufiger` export and pin what it actually produced: 2 specimens × 9
cycles, stages landing on 50–450 MPa in 50 MPa steps, h0 = 0.471 mm, d0 = 16 mm,
a detected dwell of 888–3079 samples in every cycle, no unphysical values, and
cumulative permanent deformation of 13.494% / 14.071%.

Instrument exports are gitignored rather than committed, so those tests skip on
a fresh clone until the files are placed in `tests/data/` — see the README
there for the expected names.

## Still to build

Steps 3–5 of the handoff: the Streamlit UI (Ingest / Results / Compare /
Config), the dashboard rework, and plots. The pieces they need are in place —
`preview()` returns exactly what an ingest screen should show before committing,
and `knowledge_base.cycles_for_materials()` returns the shape a compare view
needs.

Two open questions carried over from the handoff and not decided here:

- **h0 for exports without a metadata sheet.** Currently a `Config` fallback
  (`--h0-mm`); strain columns are suppressed rather than faked when it is
  unset. Whether the UI should prompt or a material lookup table should hold it
  is still open.
- **No time channel in either export**, so creep rate is unavailable. This is a
  change to the export settings on the machine, not something the tool can
  recover.
