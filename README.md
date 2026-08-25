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

## Web UI

```bash
pip install -e ".[webapp]"
streamlit run compression_tool/webapp/app.py
```

Four views, matching HANDOFF.md's build order, behind an icon sidebar (`st.logo`
+ styled `st.button` rows -- see "Why not `st.navigation`" below for why it is
not Streamlit's own multipage widget):

| View | What it does |
|---|---|
| Ingest | Upload exports, adjust thresholds, `preview()` before committing, then `ingest()`. |
| Results | Pick a material and its specimens (1-8); renders the grouped-bar dashboard against their real records and curve caches. |
| Compare | Build named groups of specimens -- any specimens, from any materials, in any combination -- and overlay one metric across the groups' means (`knowledge_base.cycles_for_specimens()`). A group is not required to be a whole material. |
| Config | What settings a run was actually ingested with -- read-only, traced back per run rather than showing the form's current defaults. |

Every view is a thin layer over the public API -- `preview`, `ingest`,
`knowledge_base`, and `dashboard_data.build_dashboard_data()`, which maps a
stored record plus its curve cache onto the exact shape the dashboard
template (`webapp/templates/results_dashboard.html`) renders from. Nothing
web-specific reimplements a metric.

The dashboard's chart set (`PANELS` in the template) is a single array, so
narrowing it to whatever subset a reviewer settles on -- fewer panels, a
different order -- is a one-line edit, not a rebuild. Each chart carries its
own legend, drawn into the SVG rather than beside it, so a copied or
downloaded PNG is self-explanatory without its surrounding page. Copy and
Download both render the same PNG; when the browser's clipboard or download
permissions are actually withheld (a sandboxed embed) the PNG opens on
screen instead, right-click-able, rather than failing silently -- detected
via `window.origin`, which reads the literal string `"null"` in a sandboxed
iframe lacking `allow-same-origin` and the real origin otherwise. (A `srcdoc`
document's `location.origin` reads `"null"` regardless of sandboxing, which
is why the check does not use it.)

### Specimens per test

Any count from one to eight. `dashboard_data.MAX_SPECIMENS` is the palette's
slot count, not an arbitrary cap: the eight categorical slots are assigned by
specimen index and never cycled, so a ninth specimen would have to reuse a
colour, and two specimens sharing one is worse than being told to select
fewer. Charts read most comfortably to about six
(`COMFORTABLE_SPECIMENS`).

Layout adapts rather than squeezing: the grouped-bar panels widen as series
are added, and because the same number drives the grid's column width the
grid drops to fewer columns instead of thinning the bars past legibility.
Direct value labels stay selective -- past three series a number on every bar
is forty-odd labels fighting for one strip of space, so beyond that the hover
tooltip and the values table carry the figures.

Inside the dashboard every specimen is a toggle, and the mean recomputes over
whichever are shown. Colour follows the specimen, never its position in the
visible list, so hiding one never repaints the others.

### Colour

The categorical palette is the validated default from the data-viz method,
verified with its own checker on the adjacent pairlist (grouped bars sit side
by side) against the exact surfaces the dashboard renders on -- light: worst
adjacent CVD dE 9.1, normal-vision 19.6; dark: 8.4 and 19.3. Dark is a
selected set of steps for the dark surface, not an automatic flip of the
light one. Three light-mode slots sit under 3:1 on the light surface, which
obliges the relief rule: every chart carries a legend, the expanded view
direct-labels its bars, and the full values table is one tab away.

The **mean is deliberately not a categorical slot** -- it is an aggregate, not
another specimen, and giving it a specimen hue would say it is one. It takes a
neutral ink instead.

`--brand` drives only the interactive accent (active chips, focus rings, the
tab underline) and never a data mark, so rebranding cannot break a validated
data palette. The same palette is handed to Streamlit's chrome in
`.streamlit/config.toml`, so the Compare view's charts use the same hues in
the same order.

### Comparing across specimens, not just across materials

Compare builds groups from individual specimens, not whole materials. Each
group starts pre-filled with one material's specimens as a shortcut, but
membership is free-form from there: drop a bad trial run out of "Material A"
without losing the rest of its mean, or fold "Material A"'s S2+S3 and
"Material B"'s S4+S5 into one group each, spanning materials, to compare
exactly the runs that matter. The same freedom exists one tab over: Results'
specimen toggles (§ Specimens per test) already let a bad run be excluded from
that view's own mean without leaving the page.

### The expanded-chart dialog: a fixed, scrolling frame, not a content-fit one

`components.html` embeds the dashboard in an iframe with a height Streamlit
fixes once, in Python, before the browser ever measures anything. An earlier
version sized that height to the dashboard's own total content (2000px+) so
nothing inside would need a scrollbar. That broke the "expand chart" dialog:
the dialog sizes itself off `vh`, and inside that iframe `vh` meant the
content height, not the screen -- so a 92vh dialog came out taller than any
real monitor, and because the frame's Python-side width estimate could not
track the sidebar being opened or closed live in the browser, it drifted from
the real layout besides.

The fix is to stop estimating: the iframe is a fixed, screen-realistic height
(`820px`) that scrolls INSIDE itself (`scrolling=True` on a real, non-content-fit
box), so `vh` means the real viewport, the dialog centers in what is actually
on screen, and it already re-measures itself on window resize -- which is what
makes it correctly follow the sidebar being collapsed or expanded, with no
Python-side awareness of that state needed at all. Verified with the dialog
opened after scrolling deep into the iframe's own internal scrollbar (the
scenario that broke before): the panel is capped at `min(78vh, 680px)`,
deliberately well under the iframe's own 820px, so it stays fully on screen
even though this page cannot see exactly where the outer Streamlit page has
scrolled it to.

### Why not `st.navigation`

The sidebar is a manual `st.button` loop with icons, not `st.navigation` +
`st.Page` -- Streamlit's own multipage widget. Its URL-path routing cleared
`st.session_state` on every page switch in this environment, confirmed with an
isolated repro (session_state came back `{}` on the destination page, not just
one widget). A single script with the current view held in `session_state` and
dispatched by hand has no page navigation to lose state across.

That surfaced two narrower, real Streamlit gotchas worth keeping in mind
anywhere else in this codebase a similar pattern is tempting:

- **A keyed widget has exactly one call site.** The Workspace input used to be
  re-declared inside every view (`workspace_picker()` called from each of
  Ingest/Results/Compare/Config). Even with `st.navigation` removed, it still
  reset on every switch, because Streamlit ties a keyed widget's carried-over
  value to where in the script it is declared, not just its key -- the same
  key at a different call site is a different widget as far as that carry-over
  is concerned. Fixed by rendering it once, in `app.py`, and passing the
  resolved `Workspace` into every view's `render(ws)` as a plain argument.
- **`st.rerun()` mid-loop can silently drop state declared after it.** The nav
  buttons originally called `st.rerun()` immediately inside their own loop,
  before the Workspace input a few lines below had run. That halts the script
  right there -- and Streamlit garbage-collects `session_state` for any keyed
  widget that was not instantiated on the run that just completed, so the
  workspace value was being cleared on the very run that changed the page. A
  button click already triggers a rerun on its own; the explicit call was not
  only unnecessary, it was cutting the script short before later widgets ever
  ran. The one intentional `st.rerun()` left in `app.py` is the last statement
  in `main()`, after every widget (including the newly-selected page's own)
  has already rendered once -- it exists purely to make the sidebar's own
  active-item highlight catch up within the same click instead of lagging one
  behind, and nothing after it can be starved because nothing is after it.

## Still to build

Steps 3–5 of the handoff are now built: Ingest / Results / Compare / Config
above. What is not built yet:

- **No re-analysis from the UI.** Changing a threshold on Ingest only affects
  new ingests; there is no "re-run this specimen with different settings"
  button yet, though `Config` exposes every knob needed to build one.
- **Brand colours are placeholders.** `--brand` in the dashboard template and
  `primaryColor` in `.streamlit/config.toml` carry the palette's own blue.
  Swapping in EQYO's accent is those two values; nothing else depends on them.

`ingest()` writes a `<specimen>.curve.json` sidecar beside every record -- the
per-cycle stress-displacement points a chart needs, reduced with
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
