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
  Raw exports/                      immutable, content-addressed, read-only
    <sha12>_<original name>.xlsx      -- optional, see below
  Records/
    <material>_<YYYY-MM-DD>/
      run.json                      what was ingested, under which config
      <specimen>.json               the record — source of truth, ALWAYS written
      <specimen>.curve.json         curve cache for the dashboard, ALWAYS written
      <specimen>.csv                per-cycle table, flat            -- optional
      <specimen>.xlsx               per-cycle table + summary        -- optional
      <specimen>.html               standalone report                -- optional
      <material>_<date>.xlsx        all specimens of THIS RUN, when >1 -- optional
  reports/
    <material>.xlsx                 every specimen ever ingested for this
    <material>.html                 material, across every run -- see
                                     "Combined per-material export" below
    _Overview.html                  every material at a glance, links into
                                     each one's own report -- see "The
                                     all-materials overview" below
  materials.json                    the controlled material list -- see
                                     "The controlled material list" below
  audit/                             one small JSON file per ingest call --
    <timestamp>_<id>.json             see "Ingest audit trail" below
  knowledge_base.db                 SQLite index, rebuildable -- unless the
                                     workspace has an index_root (the web
                                     app always sets one), in which case
                                     this file lives THERE instead, not
                                     under <workspace>/ at all -- see
                                     "Sharing the app with colleagues" below
```

`Raw exports/` and `Records/` are named for someone browsing the shared drive
in Explorer, not for a codebase — plain English beats the folders someone
opening `Reports\` would otherwise never need to see. A workspace ingested
into before this rename does not have to move anything: `raw_input/` and
`processed_output/` still work exactly as before if that workspace already
has them on disk (see `Workspace.raw` / `.processed` in `persistence.py`) —
only a brand-new workspace gets the new names.

Three properties this layout is built to hold:

- **The original export can be archived, but does not have to be.** By
  default it is copied into `Raw exports/` before anything is analysed — an
  export that later turns out to crash the engine is still preserved — and
  the copy is marked read-only. `ingest(archive_originals=False)` (the
  Ingest form's "Archive a copy of the uploaded file" checkbox, or
  `--no-archive` on the CLI) skips the copy for someone who already keeps
  their own originals elsewhere; the file's SHA-256 is still recorded either
  way, so re-ingesting the same file stays a no-op rather than a silent
  duplicate regardless of this setting.
- **The JSON records are the source of truth, always written.** Each one
  carries the metadata, the exact config used, the source file's hash and
  every per-cycle metric. A record plus the archived raw file (if kept) is
  enough to reproduce the numbers. The per-specimen and per-run Excel/CSV/HTML
  next to it are a convenience, not the record, and are themselves optional:
  `ingest(write_reports=False)` (the "Write per-run Excel/CSV/HTML" checkbox,
  or `--no-reports`) skips them for someone who only ever opens the combined
  export in `reports/` and finds the per-run copies redundant.
- **The database is disposable.** `rebuild` throws it away and regenerates it
  from the records. Nothing is stored there that cannot be recovered, so a
  schema change is a rebuild rather than a migration. `reports/` is the same
  kind of disposable: `material_export.export_material()` rebuilds it
  entirely from the indexed specimens every time, so deleting it just means
  the next ingest (or a manual rebuild -- see Config, or `compression-tool
  export-material <name>`) recreates it.

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
- **Hold detection is automatic, with one deliberate exception.** A hold is a
  plateau at peak stress at least `hold_min_points` (20) samples long, within
  `hold_tol_frac` (0.5%) of the peak -- reliable for the validated exports,
  whose dwells run 900-3000+ samples. It breaks down on a fast-cycling test
  with NO programmed dwell: turning around at peak still takes a handful of
  samples (geometry, not a hold), and on a short enough cycle that turnaround
  can accidentally clear `hold_min_points` and get misread as a real one --
  observed on a ~2,100-sample-per-cycle steel mesh test, where 3 of 10 cycles
  were flagged with a hold that was not there. `Config.detect_holds` (default
  `True`) is the escape hatch: set it `False` -- the Ingest form's "Test has a
  hold at peak" checkbox, or `--no-detect-holds` on the CLI -- for a test
  known to have no dwell, and every cycle reports no hold and no creep instead
  of a few false ones scattered through an otherwise hold-free test.
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
| Ingest | Upload exports, adjust thresholds (including whether the test has a hold at all -- see "Hold detection" below), `preview()` before committing, then `ingest()`. |
| Results | Pick a material and its specimens (1-8); renders the grouped-bar dashboard against their real records and curve caches. |
| Compare | Build named groups of specimens -- any specimens, from any materials, in any combination -- and overlay one metric across the groups' means (`knowledge_base.cycles_for_specimens()`). A group is not required to be a whole material. |
| Config | What settings a run was actually ingested with -- read-only, traced back per run rather than showing the form's current defaults. |

### Sharing the app with colleagues -- and the shared workspace

`scripts/run_webapp.bat` starts the app bound to this PC's network address
instead of only `localhost`, so colleagues on the same corporate network/VPN
can open `http://<this-PC's-name>:8501` in a browser -- no install, no VS
Code, no Python on their end. Whichever PC runs it has to stay on and
connected while people are using it; closing that window takes the app down
for everyone. **There is no login yet** (see "Still to build"), so this is a
trusted-network stopgap, not the final answer -- and never expose it via a
public tunnel (ngrok or similar): this is proprietary test data, and a
tunnel would put it on the open internet with zero authentication in front
of it.

The workspace path is read from the `COMPRESSION_TOOL_WORKSPACE` environment
variable if it is set (see the comment at the top of `run_webapp.bat` for
how to set it), falling back to `./data` for local development. It is never
hardcoded in source -- moving the host to a different PC later is just
setting the same variable there, no code change.

**The SQLite index is deliberately kept off whatever `COMPRESSION_TOOL_WORKSPACE`
points at.** That path is expected to be a synced or shared folder (OneDrive,
SharePoint, a network drive), and syncing a SQLite file while it is being
written is a well-known way to corrupt it -- the sync client and SQLite's own
locking are not coordinated. `Workspace.index_root` (default:
`%LOCALAPPDATA%\CompressionTool` on Windows) moves *only* `knowledge_base.db`
to a plain local folder on the host PC; `Raw exports/`, `Records/` and
`reports/` still live under the shared path exactly as before. The index is
disposable and rebuildable from the JSON records regardless of where it
lives, so this costs nothing -- and the very first time a shared workspace is
opened from a PC that has no local index yet, the app rebuilds one
automatically rather than showing an empty tool in front of non-empty data.

This split is webapp-only (`webapp/common.py`'s `workspace_picker()`); the
CLI still uses one root for everything, which is fine for a local or
non-shared workspace. **Do not run `compression-tool ingest` directly
against a shared `COMPRESSION_TOOL_WORKSPACE` path** -- that writes
`knowledge_base.db` straight into the synced folder, exactly the risk the
split exists to avoid. Ingest through the web app's Ingest tab instead.

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

Each group's **Name** field only labels that group -- the chart legend and the
"Group membership" listing below the chart -- it has no effect on which
specimens are in it. Two groups sharing a name is not just a display
oddity: the chart groups rows BY that name, so two identically-named groups
would otherwise merge into one series instead of drawing two. Compare
disambiguates automatically (appending " (2)", " (3)", ...) and shows which
groups it renamed, rather than let that merge happen silently.

### Combined per-material export: one Excel workbook, one real dashboard, across every run

`material_export.export_material(ws, material)` writes
`<workspace>/reports/<material>.xlsx` and `.html`, covering every specimen
ever ingested for that material -- not just whichever run last triggered the
write. It runs automatically at the end of every `ingest()` call (Config
shows the resulting paths, with a manual "Rebuild now" for a material ingested
before this existed, or after `rebuild`), and is exposed on the CLI as
`compression-tool export-material <name>`. It runs regardless of the
`write_reports` setting below -- turning off the per-run copies never turns
off the one export most people actually keep using.

The `.html` half is a real, standalone copy of the interactive dashboard --
the same template and the same `dashboard_data.build_dashboard_data()`
Results renders from, with the JSON data baked directly into the file. It
opens from disk with no server running and no network access, and every
chart still exports its own PNG. This is a materially different thing from
the plain-tables report `html_report.py` writes per run (see "The workbook"
above): that one deliberately carries no charts; this one is the charted
dashboard itself, saved.

Two adjustments only this combined export needs, since it can span specimens
that were never ingested together:

- **The colour palette's `MAX_SPECIMENS` (8) ceiling still applies.** A
  material that has outgrown it keeps every specimen in the Excel workbook
  (a table has no such limit) but the dashboard shows only the `MAX_SPECIMENS`
  most recently ingested, with an HTML comment at the top of the file
  recording how many were left out and why.
- **The page title and PNG-download filename**, which `build_dashboard_data`
  otherwise takes from the first specimen's own source file (correct when
  every specimen came from one ingest run, misleading once they come from
  several), are overridden to name the material instead.

### The all-materials overview: one page, no app required, to see and compare everything

`reports_overview.build_overview(ws)` writes `<workspace>/reports/_Overview.html`
-- every material in the workspace, its specimen/run counts, mean peak stress,
and last-ingested date, plus a headline bar chart ranking materials by mean
peak stress. Like the per-material export above, it is fully self-contained
(no server, no network), rebuilt automatically on every `ingest()`, and
exposed on the CLI as `compression-tool build-overview` and in Config as a
manual "Rebuild now".

This exists for the majority of people who only ever read results, not
ingest them: they never need the live app running at all. Point them at
`reports/_Overview.html` on the shared drive and they can browse every
material, see which ones are worth a closer look, and click through to a
material's own full dashboard -- entirely from a folder, in a browser, on a
machine with no Python installed.

Underscore-prefixed deliberately: it sorts before every material name in
Explorer/Finder, so it is the first thing anyone sees when they open the
`reports/` folder, not something to hunt for among however many materials
have accumulated.

### The controlled material list: one canonical spelling per material

`material_registry.py` maintains `<workspace>/materials.json` -- every
material name that has ever been ingested, so "SteelMesh" typed on Monday
and "Steel Mesh" typed on Friday do not become two materials that never
compare against each other in Results or Compare, silently. Two mechanisms,
one belt-and-suspenders:

- **Ingest offers a picker**, not a free-text box, once at least one material
  exists: pick from the list, or an explicit "+ Add new material" reveals a
  text box for a genuinely new one. The very first material in an empty
  workspace still gets a plain text box -- there is nothing to pick from yet.
- **`ingest()` itself normalizes what it is given**, regardless of entry
  point (webapp, CLI, or direct API use): `add_material()` casefolds and
  strips separators to compare names, so "steel-mesh" resolves to the
  already-registered "SteelMesh" rather than creating a near-duplicate, even
  if someone bypasses the picker entirely. Ingest shows an info notice when
  this happens, naming both what was typed and what it matched to -- a
  silent substitution would be more confusing than telling a user their
  input was recognized as something already on file.

A missing or corrupt `materials.json` never blocks ingest: `load_materials()`
falls back to deriving the list from the index itself, so the workspace's
real data is always the floor, and only the curated ordering/dedup a saved
file provides is what is temporarily lost.

Deliberately NOT built (a possible follow-up, not needed yet): renaming or
merging materials that are *already* fragmented in existing data. That would
mean rewriting `material` on already-persisted specimen JSONs and, because
`specimen_id` is partly derived from material, changing IDs that Compare's
session state and the SQLite index already reference -- real, but higher-risk
work, worth doing only once an actual case of existing fragmentation shows up.

### Concurrent ingest: the filesystem decides who owns a run folder

Two people ingesting into the same shared workspace around the same moment
used to be a real race. `resolve_run_dir()` picks a run folder by name
(`<material>_<date>`, then `-002`, `-003`, ... on a collision) and, before
this hardening, only checked whether that name already existed before
`mkdir`ing it -- a window in which two callers could both see the name free,
both proceed, and both write specimens into the same folder. `run.json` is
written once, at the end of each ingest, so the loser of that race would
have its specimens on disk but silently missing from the manifest.

`resolve_run_dir()` now claims the folder itself with an exclusive-create
`mkdir` (no `exist_ok`): the filesystem, not a check-then-act sequence in
Python, decides who gets a given name. Losing the race for one name just
means trying the next suffix, exactly as if the folder had already existed
from an earlier run -- so two concurrent ingests of different sources for
the same material on the same day now reliably end up in two different
folders instead of silently sharing one. A legitimate re-run (identical
sources, identical config, same day) still lands back in the same folder as
before, once its `run.json` proves the match.

The other half: every file this codebase (re)writes on every ingest --
`reports/<material>.xlsx`, `reports/<material>.html`, the per-run workbook
and report, and the per-specimen curve cache -- is now written atomically
(a `.partial` file, then `os.replace`), the same pattern the JSON records
and `reports/_Overview.html` already used. Two ingesters landing on the same
material around the same time can no longer produce an interleaved, corrupt
workbook or dashboard between them, and nobody can open a half-written file
mid-write regardless of concurrency.

A lock file was deliberately not added: at "a few ingesters", exclusive-
create plus atomic writes closes every case that actually mattered, without
the stale-lock failure mode a lock file brings (a crashed process leaving a
lock nobody ever clears).

### Stiffness-quality flagging in the common-band stiffness chart

`schema.py`'s `stiffness_quality()` -- 'few points' below 10 samples in the
fit, 'nonlinear' below an R² of 0.95, 'ok' otherwise -- already coloured the
Excel cycles sheet and sat as raw `n`/`R²` columns in the dashboard's values
table. The chart itself did not: a common-band stiffness slope from four
points on a fast machine ramp was drawn identically to one from forty,
which is exactly backwards for the one panel most likely to be read at a
glance and quoted.

The common-band stiffness bar chart now dims and diagonally hatches any bar
whose fit is thin or curved, in both the grid view and the expanded dialog
(one shared drawing function, so the fix applies to both automatically), and
adds a "Fit quality" line to that bar's tooltip. The raw `n` and `R²` stay in
the values table as before -- the chart change is additive, not a
replacement for the numbers underneath it.

### Ingest audit trail: who ingested what, and when

`audit.py` writes one small JSON file per ingest call, under
`<workspace>/audit/` -- user (OS login name), host, UTC timestamp, material,
run folder, the source files and their hashes, every specimen written, and
anything skipped and why. Written automatically by `ingest()` itself, so it
applies uniformly regardless of entry point, same as the material registry
and the overview page. Read it back with `compression_tool audit` on the
CLI, `list_audit_entries(ws)` from Python, or the "Recent activity" table on
the Config tab (the 15 most recent, across the whole workspace).

One file per ingest, not one growing log that every ingester would have to
append to -- the same reasoning as everywhere else in this codebase that a
shared drive rules out append-in-place: two ingesters appending around the
same moment can interleave into a corrupt line or lose one entirely,
depending on how the filesystem buffers a write that is not a whole new
file. A brand-new file per ingest, atomically written, is never shared
between writers.

Best-effort and disposable by design, like the SQLite index: nothing
downstream reads an audit record to reconstruct state, so a write that
fails (a read-only share, a full disk) is swallowed rather than allowed to
fail an ingest that already succeeded and was already written to disk in
full.

### One design system across every view, not just Results

Results was the first view rebuilt with a real visual language; Ingest,
Compare and Config used to be default Streamlit widgets stacked in a column.
All four now share the same layer (`webapp/common.py`'s `polish()`, applied
once from `app.py`): card surfaces for every `st.container(border=True)`,
consistent button/expander weight, and a numbered step flow (`_step()` in
`ingest_view.py`) walking Ingest through Upload → Thresholds → Preview →
Commit instead of a flat list of widgets. Config's "Sources" and "Specimens"
sections moved from `st.text()` loops to `st.dataframe()` tables so long runs
scroll and sort instead of scrolling the whole page.

Fixed alongside the restructuring: Ingest's preview cards used to vanish the
moment Commit was clicked, because their `if st.button("Preview"): ...` block
only rendered its contents on the run where that specific click happened —
and Streamlit reruns the whole script on every button press, including
Commit's. Preview results now live in
`st.session_state["ingest_preview_rows"]` and are re-rendered on every run,
so they stay on screen through the Commit click that follows them.

**A Streamlit CSS gap, found while wiring the step badges' colour:**
`var(--primary-color)` and `var(--secondary-background-color)` — both
documented Streamlit theme variables — resolve to nothing in this Streamlit
build (1.62). Confirmed by walking the DOM in a real browser
(`getComputedStyle(el).getPropertyValue('--primary-color')` returns `''`
everywhere, and no stylesheet defines a matching `:root` rule); the app's own
pre-existing nav-highlight CSS only worked because it already carried a
literal fallback (`var(--primary-color, #2a78d6)`) as a coincidence, not a
deliberate guard. Every `var(--...)` this app relies on now carries an
explicit fallback pulled from `.streamlit/config.toml`'s own theme literals,
with a `@media (prefers-color-scheme: dark)` block supplying the dark-theme
literal where the difference is visible (the step badge colour, the file
uploader's drop-zone tint) — confirmed Streamlit itself follows
`prefers-color-scheme` here regardless of `[theme] base`, by rendering with a
`color-scheme: dark` browser context and comparing.

`common.py` also gained two small helpers used everywhere a specimen or group
is named: `short_tag(label, i)` pulls the trailing `_S<n>` off a specimen
label ("S1", "S2", …), and `dot(i)` is an inline colour swatch for
categorical slot `i`. Both exist for the same reason: a dropdown or chip
showing full labels breaks once specimens share a long common filename
prefix — the truncation cuts off exactly the suffix that told them apart, and
two entries end up reading as identical. Putting the short tag first survives
that truncation; the Results and Compare specimen pickers, and Config's
specimen table, all use it.

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
