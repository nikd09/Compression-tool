# Compression Analysis Tool

Ingestion, metrics and persistence for load-controlled cyclic / multi-stage
compression tests exported from a Zwick Z100.

The calculation engine (`compression_tool/core.py`) is the validated reference
implementation described in [HANDOFF.md](HANDOFF.md). This repository adds the
persistence layer and the Excel export around it — steps 1 and 2 of the build
order in that brief.

Three deliberate changes have been made to the engine since the handoff, each
after review against the real T050E1 export:

- `MaxDisp_mm` (and `MaxStrain_pct`) added — the largest displacement in the
  cycle, distinct from `PeakDisp_mm`, which is displacement at maximum *stress*.
- The energy docstring corrected: `MPa·mm` is work per unit cross-sectional
  area, not per unit volume. No computed value changed.
- `StressAtMaxDisp_MPa` added — the stress at which the specimen was most
  compressed. Plotting the loops revealed that from cycle 6 the maximum falls on
  the *unloading* ramp, not at the dwell's end: the specimen kept compacting as
  the load came off. The description of `MaxDisp_mm` was corrected with it.

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

Five sheets when a run has more than one specimen (four for a single specimen
— Statistics is skipped when there is nothing to compare):

| Sheet | Contents |
|---|---|
| Summary | Identity, provenance and whole-test aggregates. Fields down the page, specimens across it, so a two-specimen series reads side by side. Warnings appear **once**, below every column, not once per specimen. |
| Cycles | The flat per-cycle table. Real headers with units, frozen panes, autofilter. |
| Statistics | Mean / std / coefficient of variation per cycle, across specimens — the same shape as the source export's own `Statistik` sheet, extended to every cycle. Only present with >1 specimen. |
| Data dictionary | What every column means, generated from the schema. |
| Config | The settings behind the numbers, plus the derived reference levels. |

The data dictionary is not decoration. The per-cycle table carries two
stiffness columns that look interchangeable and are not, and a permanent
deformation column that is **not** compression set in the ASTM D395 / ISO 815
sense. Anyone reading the workbook without the surrounding conversation needs
those distinctions in the file itself.

**Source file vs source path.** The Summary shows `Source file` (the original
filename) separately from `Source path (ingest machine)` (the full path on
whatever machine ran the ingest, which may be a sandbox or CI path with no
meaning to anyone else). Use the filename for anything operator-facing.

The JSON contract is frozen at schema_version 2 — see
[docs/JSON_CONTRACT.md](docs/JSON_CONTRACT.md). Build the UI against that.

### Reading the numbers

- **Displacement, two meanings** — *displacement at peak stress* is taken at
  maximum stress; *maximum displacement* is the largest in the cycle, and is
  where the energy integrals split. The second exceeds the first by however much
  the specimen crept after the stress peak — 37% in cycle 8 of the T050E1
  export. Quote maximum displacement for how far the specimen moved. Where that
  maximum falls is its own reading — see the next entry.
- **Stress at maximum displacement** — on an intact specimen this equals the
  peak: the specimen stops compacting the moment the load stops being held.
  Below the peak it kept compacting *while the load was being removed*. On
  T050E1 it holds at ~1.00 for cycles 1–5 then steps to 0.49 by cycle 9, and
  the same step appears in both specimens — a sharper onset marker than the
  stiffness rollover.
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
- **Energy** — `MPa·mm` is work per unit **cross-sectional area**, not per unit
  volume; divide by h0 for per-volume in MPa. **Hysteresis loss** — dissipated ÷
  input — is a ratio, immune to that conversion, and is the cross-test
  comparable form; absolute loss scales with stress amplitude. Its **mean**
  across a multi-stage test is a different matter: loss climbed 0.55 → 0.93
  across T050E1's nine stages, so the Summary labels that aggregate "across
  cycles" rather than let it read as one physical value — same reasoning as
  the two stiffness bands below.
- **Permanent deformation** — residual displacement read on the *loading*
  branch at a low common stress, not at zero, because the specimen loses
  contact at zero and the signal falls back to a few-micrometre baseline.
- **Hold displacement and hold length** — always read together. Hold
  displacement is a **total, not a rate**, so a longer dwell accumulates more at
  identical material behaviour; the T050E1 dwell varies 3.5× across its cycles.
  The per-1000-samples column removes that distortion so cycles can be *ranked*,
  but it is **not a creep rate** and must never be plotted as one: converting
  samples to seconds needs a constant sampling interval, which the export does
  not record. A real rate requires a time channel enabled at export.
- **Strain and modulus are conditional.** Both depend on h0 being the gauge
  length the displacement channel actually spans, which no export proves. Every
  record carries a `strain_basis` block with `gauge_length_confirmed` (default
  **false**) and `strain_valid`; until someone asserts it via
  `--gauge-length-confirmed`, strain is marked provisional and a `critical`
  warning travels with the result. Stress-based metrics are unaffected.

  This is not the same question as *which* h0 to divide by. A
  modulus-plausibility check can rule out a wrong candidate — for T050E1 the
  20 mm crosshead reference length implies 96–192 GPa, which is impossible for
  something that compacts 13%, so h0 = 0.471 mm is the right value — but it
  cannot confirm the extensometer's physical span: a channel that bridges
  extra material would still produce a plausible-looking modulus, just a wrong
  one. `--gauge-length-confirmed` is already available on `preview` and
  `ingest`; use it once the fixturing itself has been checked, not on the
  strength of a plausibility argument alone.

## Tests

```bash
pytest
```

138 tests run against synthetic exports built to reproduce the three behaviours
the real sample data revealed: rising stage peaks, a dwell during which
displacement keeps climbing after stress has levelled off, and a collapse to a
few-micrometre baseline at near-zero stress. Those are what the engine's less
obvious choices exist for, so a synthetic signal without them would test
nothing that matters. The permanent-set pin is checked against a closed-form
value rather than a recorded output.

`tests/test_json_contract.py` pins the frozen record shape key by key, and
`tests/test_diagnostics.py` checks each warning both fires when it should and
stays quiet when it should not.

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
`knowledge_base.cycles_for_materials()` returns the shape a compare view
needs, and `ingest()` now writes a `<specimen>.curve.json` sidecar beside every
record — the per-cycle stress-displacement points a chart needs, reduced with
Ramer-Douglas-Peucker (`compression_tool/curve_cache.py`) so a UI is not
loading 85k raw samples per specimen to draw a loop. Deliberately outside the
frozen contract: rebuildable from the archived raw file, so a change to the
reduction is not a schema bump. On the real T050E1 export it reduces
85,844 / 86,017 raw samples to 704 / 1,380 points per specimen (28–55 KB) at
under 0.3% enclosed-area error per cycle.

Open items:

- **Confirm the `Sonder LÄA` gauge length.** Until someone verifies that the
  extensometer spans only the 0.471 mm specimen, strain and modulus stay
  provisional. This is a fixturing question the tool cannot answer.
- **h0 for exports without a metadata sheet.** Currently a `Config` fallback
  (`--h0-mm`); strain columns are suppressed rather than faked when it is
  unset. Whether the UI should prompt or a material lookup table should hold it
  is still open.
- **No time channel in either export**, so creep rate is unavailable. This is a
  change to the export settings on the machine, not something the tool can
  recover.
