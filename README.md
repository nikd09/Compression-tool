# CompressLab

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

Five views behind an icon sidebar (`st.logo` + styled `st.button` rows -- see
"Why not `st.navigation`" below for why it is not Streamlit's own multipage
widget):

| View | What it does |
|---|---|
| Ingest | Upload exports, adjust thresholds (including whether the test has a hold at all -- see "Hold detection" below), `preview()` before committing, then `ingest()`. |
| Results | Pick a material and its specimens (1-8); renders the grouped-bar dashboard against their real records and curve caches. |
| Compare | Build named groups of specimens -- any specimens, from any materials, in any combination -- and overlay one metric across the groups' means (`knowledge_base.cycles_for_specimens()`). A group is not required to be a whole material. |
| Materials | One card per material, searchable -- specimens, runs, mean peak stress, mean thickness (h0), date added -- click one to open its full combined dashboard in place. See "The Materials library" below. |
| Config | What settings a run was actually ingested with -- read-only, traced back per run rather than showing the form's current defaults. |

### Sharing the app with colleagues -- and the shared workspace

`scripts/run_webapp.bat` starts the app bound to this PC's network address
instead of only `localhost`, so colleagues on the same corporate network/VPN
can open `http://<this-PC's-name>:8501` in a browser -- no install, no VS
Code, no Python on their end. Whichever PC runs it has to stay on and
connected while people are using it; closing that window takes the app down
for everyone. **There is still no real login** -- `webapp/auth.py` can put a
single shared password in front of the whole app (see below), but that is a
stopgap for the gap between "this is hosted" and "IT has SSO in front of
it," not a substitute for it -- and never expose this via a public tunnel
(ngrok or similar) regardless: this is proprietary test data, and a tunnel
puts it on the open internet in front of, at best, one shared secret.

The workspace path is read from the `COMPRESSION_TOOL_WORKSPACE` environment
variable if it is set (see the comment at the top of `run_webapp.bat` for
how to set it), falling back to `./data` for local development. It is never
hardcoded in source -- moving the host to a different PC later is just
setting the same variable there, no code change.

Two more environment variables matter once this is hosted rather than run
for yourself, both no-ops (today's exact behaviour) when unset:

- **`COMPRESSION_TOOL_ALLOWED_ROOTS`** -- `persistence.check_workspace_allowed()`,
  called from the Advanced "change workspace" box every time it is used.
  Unset, that box accepts any path the way it always has -- fine on a laptop
  running the app for yourself, since typing a path there grants no
  capability a local Python process did not already have. Hosted, an
  unvalidated free-text path is arbitrary read *and* write on the *server's*
  filesystem for anyone who can reach the URL, not just this tool's own
  data. Set this to one or more permitted roots (joined by `os.pathsep` --
  `;` on Windows, `:` elsewhere) before hosting, and the box refuses
  anything outside them with a clear message instead.
- **`COMPRESSION_TOOL_PASSWORD`** -- `webapp/auth.py`'s `require_password()`,
  the first thing `app.py`'s `main()` does. Unset, nobody sees a login
  screen, exactly like today. Set, the whole app -- sidebar and all -- is
  replaced by a single password prompt until the right one is entered for
  that browser session. One shared secret, not per-person identity, and
  nothing about who entered it is recorded anywhere -- swap it for the
  reverse proxy / SSO the moment IT provides one.

**The SQLite index is deliberately kept off whatever `COMPRESSION_TOOL_WORKSPACE`
points at.** That path is expected to be a synced or shared folder (OneDrive,
SharePoint, a network drive), and syncing a SQLite file while it is being
written is a well-known way to corrupt it -- the sync client and SQLite's own
locking are not coordinated. `Workspace.index_root`, set by the webapp to
`workspace_index_root(root)` (default base: `%LOCALAPPDATA%\CompressionTool`
on Windows), moves *only* `knowledge_base.db` to a plain local folder on the
host PC; `Raw exports/`, `Records/` and `reports/` still live under the
shared path exactly as before. The index is disposable and rebuildable from
the JSON records regardless of where it lives, so this costs nothing -- and
the very first time a shared workspace is opened from a PC that has no local
index yet, the app rebuilds one automatically rather than showing an empty
tool in front of non-empty data.

`workspace_index_root()` scopes that local folder to a hash of the resolved
workspace root, not just the machine -- `default_index_root()` alone is one
fixed path regardless of which workspace points at it, so opening a SECOND,
different workspace on a machine that already had a first one indexed would
otherwise find that first index already sitting at the same path and use it
as-is: every view would silently show the first workspace's materials and
specimens under the second workspace's name, with nothing in the UI to
suggest anything was wrong. In practice this only bites someone who points
the Workspace field at more than one real folder from the same machine, but
it is silent and serious when it does, so the per-workspace subfolder is not
optional the way it might look from the two-line diff that added it.

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

### The full dashboard, before Commit

Ingest's Step 3 ("Preview") already showed a summary card per specimen --
cycle count, peak stress, h0, format, warnings -- without writing anything.
"Show full interactive dashboard" goes further: the exact same charted
dashboard Results renders for an already-committed material, built from the
uploaded files directly, still without writing anything.

`pipeline.preview_dashboard_data()` is what makes this possible without
duplicating the dashboard: `persistence.build_payload()` and
`curve_cache.build_curve_cache()` are both pure assembly functions already,
called by `ingest()` before anything is written to disk, so a preview can
call the exact same two functions and simply never take the archive/write
steps that come after them in `ingest()`. The material name is used only to
label the charts -- it is never passed to `add_material()`, so looking at a
file never registers a material either. The only disk I/O is reading the
file's own bytes to hash them, the same cost `ingest()` already pays before
archiving.

This exists for re-checking an old export, or looking at a throwaway trial,
without either becoming a permanent Materials entry just because someone
wanted to see it. `preview()`'s summary cards remain the fast path for "does
this look right"; the full dashboard is for "let me actually look at the
curves" before deciding whether Commit is worth it at all.

### One Material field, or several: per-file overrides for a mixed batch

Ingest used to have exactly ONE Material field for the whole upload. Attach
two exports meant for two different materials and both landed under
whichever single name was typed -- no error, no warning, just every specimen
from both files silently combined into one material. Splitting them apart
again meant deleting and re-ingesting separately.

Attaching more than one file now reveals a "Different materials in this
batch?" expander (`ingest_view._per_file_materials()`) listing every file
with its own picker, defaulted to `"(same as above)"` -- the common case
(several files, one material) still needs zero extra clicks. Only a file
someone explicitly overrides gets a different material; the rest fall
through to the Material field above. `_resolve_material_groups()` then
groups the uploaded paths by their resolved material and Commit calls
`ingest()` once PER GROUP rather than once for the whole batch -- each group
lands in its own run folder under its own material, exactly as if it had
been uploaded separately. With no overrides this is one group and one call,
identical to before. Preview's full dashboard (previous section) honours the
same split via `preview_dashboard_data(material_by_path=...)`, so what
Preview shows before Commit already reflects which specimens will land under
which material, not a single combined guess.

### Comparing across specimens, not just across materials

Compare builds groups from individual specimens, not whole materials. Each
group starts pre-filled with one material's specimens as a shortcut, but
membership is free-form from there: drop a bad trial run out of "Material A"
without losing the rest of its mean, or fold "Material A"'s S2+S3 and
"Material B"'s S4+S5 into one group each, spanning materials, to compare
exactly the runs that matter. The same freedom exists one tab over: Results'
specimen toggles (§ Specimens per test) already let a bad run be excluded from
that view's own mean without leaving the page.

Each group also carries a **Material filter** above its specimen picker,
defaulted to that group's own starting material. Once a workspace holds many
materials, the specimen dropdown listing every specimen in the whole
workspace flat becomes the thing standing between someone and the two or
three specimens they actually want; the filter narrows it to one material at
a time instead. Picking "All materials" restores the full flat list for
building a group that deliberately spans materials -- and starts that
group's picker EMPTY rather than pre-selecting the whole workspace, since a
filter meant to cut down a long list should never itself be the thing that
dumps everything into a group. Switching the filter clears whatever was
picked under the previous one, rather than silently keeping a specimen from
a material just filtered away.

Each group's **Name** field only labels that group -- the chart legend and the
"Group membership" listing below the chart -- it has no effect on which
specimens are in it. Two groups sharing a name is not just a display
oddity: the chart groups rows BY that name, so two identically-named groups
would otherwise merge into one series instead of drawing two. Compare
disambiguates automatically (appending " (2)", " (3)", ...) and shows which
groups it renamed, rather than let that merge happen silently.

Bars carry a direct value label at 3 groups or fewer -- the same threshold
and reasoning as the Results dashboard's own bar charts (`PANELS` in
`results_dashboard.html`): past that, a number on every bar is dozens of
labels fighting for the same strip of space. This is also what Vega-Lite's
own fullscreen and PNG-download actions (the toolbar icons above the chart)
render, since it is the same chart spec at a different size -- there is no
separate "expanded" variant to add labels to, unlike Results' custom SVG
charts, which draw the small grid cell and the expanded dialog from the
same function.

The chart also carries a title -- the picked metric, with its unit as a
subtitle -- matching every panel on the Results tab already having one; this
was the one chart in the app without it. Axis, legend and value-label font
sizes are set explicitly rather than left at Vega-Lite's defaults.

**The chart's width is fixed (`width=820`), not `use_container_width=True`.**
Vega-Lite text is set in absolute pixels and does not scale with the chart's
size the way Results' hand-built SVG charts do -- every dimension in that
template, bar widths down to font sizes, is a function of one base width, so
scaling the SVG up for the expanded dialog or a PNG export scales everything
together and the on-screen proportions never change. Compare has no such
scale-invariance: letting the chart stretch to the full page width (a
Vega-Lite spec copied out of a wide browser window read `"width": 1340`)
grows the plot area while the text stays the same absolute size, so it reads
smaller the wider the window is -- and "Download PNG" downloads exactly what
is on screen, so a screen-filling chart became a huge image with
comparatively tiny text once pasted into a Word or PowerPoint page at normal
size. A fixed, moderate width keeps text a healthy fraction of the image at
every size this spec is ever rendered or downloaded at, confirmed by
downloading and viewing the PNG directly rather than only checking the
in-app view. Streamlit's own fullscreen toolbar action still stretches to
the viewport regardless of the spec's configured width -- that one render
path is outside what the Python API controls -- which is what the explicit
font-size bump above is for.

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

### The Materials library: one card per material, in the app and out of it

`reports_overview.material_rows(ws)` computes one row per material --
specimens, runs, mean peak stress, mean thickness (h0), and the date this
material was first added to the workspace (the earliest `created_utc` among
its specimens, not its most recent activity) -- and is the single
computation behind two presentations that always agree with each other:

- **The "Materials" tab** (`materials_view.py`), in the app's left nav after
  Compare: a searchable grid of cards, deliberately styled to match the
  static page below rather than look like a third, different design --
  the properties above, the date in small type at the top right. Clicking a
  material's name switches the tab to that material's full combined
  dashboard (`reports/<material>.html`, embedded the same way Results embeds
  its own -- `streamlit.components.v1.html`), with a "← Back to Materials"
  button to return to the grid. Deliberately no chart of its own on the grid
  itself -- Results and Compare already own the deep dive, per material and
  across materials respectively; this tab is the index and the door into
  each material's real charts, not a second place to chart from.
- **`reports_overview.build_overview(ws)`**, which writes the same rows into
  `<workspace>/reports/_Overview.html` -- fully self-contained (no server, no
  network), rebuilt automatically on every `ingest()`, and exposed on the CLI
  as `compression-tool build-overview` and in Config as a manual
  "Rebuild now". This exists for the majority of people who only ever read
  results, not ingest them: they never need the live app running at all.
  Point them at `reports/_Overview.html` on the shared drive and they can
  browse every material and click through to its own full dashboard --
  entirely from a folder, in a browser, on a machine with no Python
  installed. Underscore-prefixed deliberately: it sorts before every
  material name in Explorer/Finder, so it is the first thing anyone sees
  when they open the `reports/` folder.

Each card also carries a **Download dashboard** button -- a local copy of
that material's combined `.html` dashboard, needing no admin access (a read,
not a write, the same as clicking through to view it). Only shown once that
file already exists on disk; a material whose dashboard has not been built
yet shows a caption pointing at opening it once instead, rather than paying
the cost of building every card's dashboard on every grid render just so the
button can offer a fresher copy nobody asked for.

The grid itself is `auto-fill` with a 230px-per-card minimum, not a fixed
column count -- as many equal-width cards fit per row as the window actually
allows (four at a normal laptop width, more on a wider monitor, fewer on a
narrower one), all the same size and font regardless of row length.

An earlier version of this page led with a "mean peak stress by material"
bar chart ranking every material against each other. Dropped: a single
number ranking materials against each other by one metric is a comparison
Compare already does properly, across whichever metric and whichever
specimens actually matter for that comparison -- a fixed bar chart on the
index page was never that, just a chart that happened to be easy to build
from data already on hand.

**The Materials cards hover** (a shadow lift and an accent-coloured border)
-- and, while adding that, `webapp/common.py`'s app-wide equivalent for
every `st.container(border=True)` card turned out to have been a silent
no-op the whole time: it targets `[data-testid="stVerticalBlockBorderWrapper"]`,
a testid that does not exist anywhere in the installed Streamlit version's
rendered DOM (confirmed live -- zero matches, on every tab). Streamlit
now applies the border directly to the `stVerticalBlock` element itself, via
an auto-generated class name that is identical across every bordered
container on the page (and not something to hardcode -- it is a build
implementation detail, not a stable selector). Materials' cards work around
that by giving each card its own `st.container(..., key=...)`, which
Streamlit *does* still expose as a stable `st-key-<key>` class on that same
element regardless of Streamlit-internal DOM changes.

That fix is now app-wide, not just Materials: every other `st.container(
border=True)` in the app (Config's info panels, each Compare group, each
Ingest preview card) has a `key="card_..."` too, and `common.py`'s
`_POLISH` targets `[class*="st-key-card_"]` instead of the dead testid --
one hover rule (a shadow lift, a blue-tinted border), shared by every one of
them. Materials keeps its own richer card CSS layered on top (grid layout,
a bigger clickable title) rather than merging into the shared rule, since it
is deliberately a different kind of card -- keyed `mat_card_<slug>`, outside
the `card_` convention, so the two never collide. The one thing this buys
back is scoped: a bordered container added later still needs its own
`key="card_..."` to opt in -- there is no way, in this Streamlit version, to
catch every `border=True` container automatically without one.

The left nav picked up the same kind of polish along the way: each item now
gets a thin accent bar that grows in on hover and sits solid on whichever
page is active, plus a slight rightward shift and icon nudge on hover
(`app.py`'s `_NAV_CSS`) -- and lost a second dead rule in the process, one
that targeted the same nonexistent `stVerticalBlockBorderWrapper` testid for
no visible effect either.

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

**A material name is required to ingest, on every entry point.** `ingest()`
used to fall back to the file stem (`_infer_material()`) when no material was
given -- harmless for `preview()`, where it only ever labels a chart that is
never written to disk, but the CLI exposed the same fallback on `ingest()`
itself, silently. A `.xlsx` export's file name is rarely a material code
("Mehrstufiger Druckversuch Vergleichstest 2 T050LR1" was a real one, ingested
from the CLI without `-m`), and it is now permanent the moment it lands: the
Materials card, the Compare legend, the `reports/<material>` file names, all
of it. `ingest()` now raises `ValueError` rather than infer anything if
`material` is empty; the CLI's `-m/--material` is `required=True` to match.
The webapp form already blocked an empty Commit before this -- the CLI was the
actual gap. Ingest also warns (non-blocking) when a typed material name looks
like it was pasted straight from the file name rather than written as a code.

**Renaming and deleting a material** (`material_admin.py`), available from
each card on the Materials tab. Both operations edit the JSON records --
the source of truth -- and then call `knowledge_base.rebuild()`, the same
full drop-and-reindex-from-disk the app already uses to recover from any
index/disk disagreement, rather than trying to hand-patch the database:

- **Rename** rewrites `material` on every specimen JSON in every run folder
  that material owns, recomputes `specimen_id` for each (it is partly derived
  from material, so it changes with a rename), updates `run.json`, moves the
  run folder to match the new name (best-effort -- a folder that cannot be
  moved, e.g. a file open elsewhere, still gets everything inside it
  correctly re-indexed under the new name; only its folder name on disk keeps
  reading old), regenerates `reports/<material>.{xlsx,html}` under the new
  slug and removes the stale pair under the old one, and updates
  `materials.json`.
- **Delete** removes every run folder that material owns (JSON records, curve
  caches, per-run exports), its `reports/<material>.{xlsx,html}`, and its
  `materials.json` entry. Off by default: `Raw exports/` is content-addressed
  (the same file re-ingested under a second material name reuses the archived
  copy), so a raw export is only ever removed if `delete_raw=True` is passed
  AND no other remaining specimen still references it.

Both are gated in the webapp by `permissions.is_admin(ws)` -- see "Who may
rename or delete a material" below -- and Delete requires typing the exact
material name to confirm before the button enables.

**A stale index self-heals, but the app no longer crashes on it either way.**
The SQLite index can still list a specimen whose JSON record was removed
straight from disk (Explorer, the shared drive) rather than through the app --
nothing but a reindex touches it otherwise. Results and `material_export.py`
now skip a specimen whose record is missing (with a visible error naming it)
instead of raising `FileNotFoundError` out of the whole tab, and Config gained
a plain "Reindex from disk" button (`knowledge_base.rebuild()`) next to the
existing per-material and overview rebuilds, for exactly this case.

### Who may rename or delete a material

`permissions.py` gates Rename and Delete behind `<workspace>/admins.json` --
a plain list of OS usernames, matched against `getpass.getuser()`
case-insensitively, the same identity `audit.py` already attributes every
ingest to. The same pattern as `materials.json`: a small, shared,
hand-editable file at the workspace root, not a login system.

Unrestricted -- every action allowed for everyone -- until `admins.json`
exists. The first person to open Config's "Admin access" panel and click
"Claim admin access for myself" seeds it with just their username; every
visitor after that is restricted to whoever is listed, manageable from the
same panel by an existing admin (or by hand-editing `admins.json` on the
share). This still is not authentication (see the deployment notes above on
`COMPRESSION_TOOL_PASSWORD`, and "Still to build") -- nothing here proves
who is actually at the keyboard, only what the OS happens to report. It
exists to keep an accidental click from a casual visitor from renaming or
deleting a shared material -- not to stop someone deliberately editing the
file or running as another account. Real per-person enforcement needs the
app hosted behind corporate SSO.

**Rename and Delete stay visible to everyone, restricted or not** --
`permissions.is_admin(ws)` is checked at CLICK time in `materials_view.py`,
not used to hide the buttons. A non-admin sees both buttons exactly like an
admin does; clicking either shows an error explaining who can do this and
where to ask, instead of opening the rename/delete dialog. Hiding the
buttons entirely used to be the behaviour here -- changed on the reasoning
that a control nobody but an admin even knows exists is worse than one that
is visible and explains itself when it refuses: a non-admin colleague can
now discover the admin workflow exists at all, and knows who to ask, rather
than a capability quietly missing with no trace of why.

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

A lock file was deliberately not added for run-folder allocation or for any
of these per-run/per-material files: at "a few ingesters", exclusive-create
plus atomic writes closes every case that actually mattered there, without
the stale-lock failure mode a lock file brings (a crashed process leaving a
lock nobody ever clears). See below for the one place that reasoning did
not hold and a lock was added anyway.

### materials.json and admins.json: a small, self-healing lock for a shared list

`materials.json` and `admins.json` are a different shape of problem from
everything above: not an append-mostly set of per-run files, but ONE shared,
mutable list that "add a material" or "add an admin" reads in full,
modifies, and writes back in full. Exclusive-create and atomic writes do not
help here -- they make each individual WRITE safe, but not the
read-modify-write around it. Two people adding different materials (or
admins) around the same moment could each read the SAME list before either
had written, each compute their own "existing + my one addition", and
whichever wrote second would silently overwrite the first's addition
instead of both surviving.

`persistence.locked_update()` closes that gap with a small lock FILE
(`<path>.lock`, next to the JSON file it protects), acquired by exclusive
create -- the same underlying primitive `resolve_run_dir()` already relies
on, applied to a shared file instead of a new directory each time.
`material_registry.add_material()`/`remove_material()` and
`permissions.claim_admin()`/`add_admin()`/`remove_admin()` all wrap their
read-modify-write in it now. Unlike the "no lock file" reasoning above, this
one lock is self-healing against the exact failure mode that reasoning was
avoiding: a lock older than 30 seconds is assumed abandoned by a crashed
holder and is stolen rather than left to wedge every future writer forever
-- a network share has no process table to check a PID against, so age is
the only signal available. A caller waiting on a live holder gives up after
5 seconds and proceeds unlocked rather than hanging indefinitely; this buys
correctness for the ordinary case of two people clicking around the same
moment, not a hard guarantee under sustained contention, which is
consistent with this codebase's existing best-effort stance on shared,
hand-editable files (audit.py's own writes are the same trade-off).

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

### Re-analysing a run with different thresholds, without re-uploading

The Config tab's "Re-analyse this run" card, under the selected run's
summary, re-runs that run's already-archived source file(s) through the
engine with a changed threshold, hold-detection flag, h0 override or gauge
length confirmation -- the same form Ingest uses, reused as-is so the two
never drift into offering a different set of knobs for the same `Config`.

It only needs the archived copy under `Raw exports/` that `ingest()` already
wrote; nobody has to find and re-upload the original export. A run ingested
with "Archive a copy of the uploaded file" unchecked has nothing to reuse and
the card says so instead of offering a button that cannot work; a source
whose archived copy was later deleted by hand is reported and skipped, the
rest of the run's sources still re-analysed.

Feeding the resolved sources back through `ingest()` with a changed `Config`
gets its own new run folder (`resolve_run_dir`'s existing per-fingerprint
rule) -- the run being compared against is never silently overwritten by a
"what if" re-run. Re-analysing with the *same* settings on the same day is
the existing idempotent case: it overwrites the run in place rather than
piling up identical folders. `archive_raw()` is what makes feeding it an
already-archived path safe either way -- it recognises a path already inside
`Raw exports/` and returns it unchanged instead of copying it a second time
under a doubled, prefix-on-prefix name.

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

### A quieter top-right menu

`.streamlit/config.toml` sets `[client] toolbarMode = "viewer"`, which drops
Deploy, Rerun, Clear cache, Settings and About from Streamlit's built-in
top-right menu. None of them belong in front of someone using this as a
finished tool: Deploy is a Streamlit Community Cloud action, meaningless (and
possibly confusing) on a machine this app was just launched on; Rerun and
Clear cache are developer affordances -- Clear cache in particular has
nothing to clear, since nothing in this codebase uses
`@st.cache_data`/`@st.cache_resource`; Settings and About are about
Streamlit itself, not this tool. Print and Record screen stay: Streamlit
classes those as ordinary, not developer-only, so `toolbarMode` cannot drop
them individually, and they are harmless enough not to be worth a more
fragile CSS-based removal.

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
