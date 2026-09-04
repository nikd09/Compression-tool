# CompressLab User Guide — Results Page

This walks through everything on the **Results** page: the plain Streamlit
controls around the dashboard, and every piece of the dashboard itself
(the chart grid, the top info bar, the warning banner, and the Method
tab). Same format as the Ingest guide — what it is, what it means, why it
exists.

Results shows **already-committed** data — specimens that went through
Ingest and were saved to the workspace. If you just want to eyeball a file
before committing it, that's what Ingest's own Preview does instead (see
the Ingest guide); Results is for looking at what's actually saved.

---

## The picker, above the dashboard

### "Material"
A dropdown of every material that has at least one specimen committed in
this workspace. Picking one here is the same as opening that material's
card from Materials — in fact, if you came here *from* clicking a card on
Materials, this dropdown is already set to it; you don't have to re-pick.

### "Specimens (1-8)"
A multiselect listing every specimen committed under the chosen material.
Whichever ones you tick get their own colour (S1, S2, S3…) inside the
dashboard, plus an automatic "Avg" (average) series across whichever of
them are currently visible.

By default, the first handful are pre-ticked for you (enough to see
something immediately without having to select anything), but you can
change that selection freely.

**Why is there a cap (1–8)?** The dashboard's colour palette only has 8
visually distinct colours, and it will not reuse one — past that point,
two different specimens would either look identical in the charts or the
tool would have to invent a 9th colour that isn't reliably distinguishable
from the others. Practically, the charts also read most comfortably with
around 3–4 specimens at once; much more than that and the grouped bars per
cycle start to feel cramped. You can still select up to 8, the panels just
widen and the grid drops to fewer columns per row to keep the bars legible.

### The error/warning messages under the picker

- **"The index still lists X but its record no longer exists on disk"** —
  the workspace's search index still has an entry for a specimen, but the
  actual file backing it is gone. This normally means someone deleted a
  file by hand outside the app (e.g. straight from Windows Explorer or a
  shared drive), which only ever touches the file, never the index that
  still points at it. The fix is on the Config page: **"Reindex from
  disk"** rebuilds the index to match what's actually there.
- **"No curve cache found for: X"** — every specimen's full stress/
  displacement signal is cached separately (for speed) from its summary
  numbers. If that cache is missing for one specimen, every *summary* chart
  (Peak stress, stiffness, permanent deformation, etc.) still works fine
  for it, but the "Stress-displacement curves" panel — the one that needs
  the actual raw signal — will be empty for that specimen specifically.
  Re-ingesting that file regenerates the cache.

---

## The dashboard — top bar

### Title
The uploaded file's own name (with `.xlsx` dropped), or a generic
"Compression Results" title if no filename is available (e.g. when
several differently-named files were combined).

### The metadata chips
A row of small pills right under the title:

| Chip | Meaning |
|---|---|
| **Specimens** | How many specimens are currently loaded into this view. |
| **Cycles** | How many cycles (load stages) the test had. |
| **Peak** | The single highest stress reached across the whole test — this is the *global* peak every per-cycle-relative number is measured against. |
| **h₀** | The specimen's measured starting thickness, in mm — what strain and % figures are normalized against. |
| **d₀** | The specimen's starting diameter, in mm (metadata only; not used in any calculation shown here). |
| **Channel** | Which displacement channel/extensometer the machine's export used to measure movement. |
| **T** | The recorded test temperature, in °C. |

### Colour theme / EN-DE toggle
Small icon controls in the top-right of the dashboard's own header — these
are *independent* of the app's own sidebar EN/DE and light/dark toggle,
because this whole dashboard is designed to also work as a **standalone
file**: if you download it or someone opens it outside the app entirely
(mailed as an attachment, opened straight from a folder), there's no
Streamlit sidebar around it to control it from, so the dashboard carries
its own copy of these controls. Inside the app, opening the dashboard
already starts it matching whatever language/theme you'd already picked in
the sidebar — these are just there so it keeps working the same way once
it's on its own.

### The warning chips — Critical / Caution / Info
Appears only if the analysis actually produced warnings for this specific
data. Collapsed by default as small pill counters (e.g. "Caution 3") —
click one to expand the full messages underneath.

These are the tool being honest about assumptions it had to make or
situations worth a second look — for example, a reference stress landing
close to the noisy near-zero region for one specific cycle, or a stage with
no detected hold in an otherwise-held test. They don't necessarily mean
something is *wrong*; they mean "here's something you should know before
trusting this number blindly."

### Results / Method tabs
Two tabs at the top of the dashboard:

- **Results** — the chart grid (below) — the numbers themselves.
- **Method** — the settings that actually produced those numbers, a
  glossary of every column, and an explicit list of things this test
  *cannot* tell you. Covered in its own section further down.

---

## The specimen toggle row

Just above the chart grid: one chip per specimen (colour swatch + short
tag, e.g. "S1"), plus an "Avg" chip if more than one specimen is loaded.

- **Clicking a specimen's chip** hides or shows it in every chart at once —
  it doesn't reload anything, it's instant, and it's purely a *view*
  toggle: it doesn't change what's selected up in the Specimens
  multiselect above, or affect any other tab.
- **"Avg"** toggles a computed average series across whichever specimens
  are *currently visible* (not necessarily everything you originally
  selected — if you've hidden one with its own chip, Avg recalculates
  without it). The small note that appears next to it — *"Avg is the mean
  of the N specimens shown, per cycle"* — is telling you exactly that: it's
  a live average of what's on screen right now, not a fixed calculation
  from when the page loaded.
- **"Show all" / "Hide all"** (only appears past 2 specimens) is a shortcut
  so you don't have to click every chip individually when comparing just
  one or two specimens out of a larger set.

---

## The chart panels

Every chart panel here is a **grouped bar chart, one group per cycle**
(except the last one, which is different — see below). Each bar in a group
is one visible specimen, plus Avg if it's toggled on.

### Peak stress — MPa
The highest stress reached in each cycle. In a multi-stage test this rises
every stage **by design** — it's the programmed test schedule, not
something the material is doing on its own. It matters here because it's
the reference every other per-cycle-relative number (stiffness windows,
reference stress) is measured against.

### Common-band stiffness — MPa/mm
The slope of the loading curve, fitted over the **same absolute stress
window on every cycle**. That window is found automatically once, on the
smallest-peak (reference) cycle's own most-linear region, then reused
unchanged on every other cycle.

**Why fix the window instead of finding the best one every time?** Because
this is the number you use to compare cycles, specimens, or even different
materials against each other — and that only makes sense if everyone's
being measured over the same stress range. A material genuinely softening
partway through the test shows up here as the bars rolling over partway
through the cycles.

A hatched, dimmed bar means the fit behind that specific cycle was thin
(too few data points) or curved (didn't fit a straight line well) — treat
that one bar as indicative only, not a number to quote confidently.

### Relative-band stiffness — MPa/mm
The same idea as above, except the fitting window is re-found **on every
cycle's own loading curve** individually, instead of being fixed once.
This gives a tighter, more faithful fit to each specific cycle — but for
exactly that reason, it is **not** valid to compare across cycles, since
the window itself moves each time. Use Common-band stiffness for
comparisons; use this one to describe a single cycle as accurately as
possible on its own.

### Permanent deformation, this cycle — % of h₀
How much of the specimen's thickness failed to spring back, counted
**within that one cycle alone**: the gap between the residual position
measured on the way up and the way back down, both at the same low
reference stress. Nothing about any other cycle is involved in this
number.

**Why does this matter more than the running total below?** Because this
is the chart that shows a sudden jump. If cycle 7 shows a step up that's
noticeably bigger than cycles 1–6, that's the first visible sign that
something changed — new damage — starting specifically in cycle 7. On the
running-total chart, the same event only shows up as a *slightly* steeper
step among several already-rising bars, which is much easier to miss.

### Permanent deformation, running total — % of h₀
The per-cycle amounts above, added up cycle by cycle: the total fraction
of the specimen's thickness that has not sprung back, cumulatively, by
this point in the test. This number only ever rises or stays flat — it
never goes down — so it's the one to quote for "how much permanent set has
this specimen taken overall." Check the per-cycle chart next to it if you
need to know *which* cycle actually caused a jump, since a single large
cycle is easy to miss here (it just makes one step slightly steeper among
several already-rising ones).

### Maximum strain — % of h₀
The largest displacement reached in the cycle, divided by the specimen's
own measured starting thickness (h₀). This is only meaningful to the
extent that h₀ genuinely matches the length the displacement sensor
actually measures — see the Ingest guide's note on "Gauge length
confirmed" for what that assumption depends on.

### Hysteresis loss — dissipated ÷ input (no unit, a ratio)
Energy dissipated in the cycle, divided by the energy put into it. This is
the form that's comparable **across different stress levels** — the raw
dissipated-energy number by itself scales with how hard the specimen was
loaded, so a 50 MPa stage and a 450 MPa stage can never be compared
directly on that raw number, but they *can* be compared on this ratio.

### Hold displacement — mm (a total, not a rate)
How much the specimen kept moving (creeping) while held at peak stress,
added up over the whole dwell — a **total**, not a rate per second.
Important: dwell length varies between cycles in a real test, so a cycle
held for longer will naturally accumulate more displacement even if the
material is behaving identically to a shorter-held cycle. Always read this
next to the hold length (visible in the Method tab's values table) rather
than on its own.

### Maximum displacement — mm
The single largest displacement reached in the cycle — this is also the
exact point where the tool splits the loop into "loading energy" and
"unloading energy" for the energy calculations. It's typically a bit more
than the displacement at peak *stress*, by however much the specimen
crept further while the load was being held.

### Energy dissipated — MPa·mm
The area enclosed by the stress-displacement loop for that cycle: work
that went into the specimen and never came back out. The unit is work per
unit **cross-sectional area** (MPa·mm), not per unit volume — divide by h₀
if you need work per unit volume in plain MPa.

### Stress-displacement curves (the last, full-width panel)
This one isn't a bar chart — it's every specimen's actual raw signal, all
cycles overlaid. Each closed loop is one cycle: up the loading branch,
across the dwell at peak, down the unloading branch. This is the shape
every other number on this page is calculated *from*.

- The **area inside a loop** is that cycle's dissipated energy (same
  number as the Energy dissipated bar chart).
- The **rightward drift** between one loop and the next, along the
  displacement axis, is the permanent deformation accumulating — you're
  literally watching the specimen fail to fully return to where it started.

---

## Each panel's toolbar

Hovering a panel (or opening its expanded view) shows three small icon
buttons:

- **Copy** — copies that chart as a PNG image straight to your clipboard,
  ready to paste into a document or email.
- **Download PNG** — saves the same image as a file.
- **Expand** — opens the chart full-size in a dialog, with the "why" text
  underneath it (the same explanation as above, always visible there) and
  labeled data points where there's room to show them.

The Y-axis unit is always shown, at every size — small panel, expanded, or
downloaded — so an exported image is never missing the information you'd
need to actually read it later.

---

## The Method tab

This is where the page shows its work — not just the numbers, but exactly
what produced them.

### "Settings behind the numbers"
The same threshold knobs from Ingest's "Advanced: segmentation and
reference thresholds" section — except here they're shown **applied to
this actual test's real numbers**, not as abstract percentages. For
example, "Reference stress: 2%" on Ingest becomes, here, something like
"2% of global peak = 9 MPa" — the literal value that was used for *this*
specimen. This is the place to check exactly what settings a committed run
actually used, especially useful if you're comparing two runs that might
have been ingested with different thresholds.

### "What each column means"
A full glossary of every column in the underlying data — including a few
that don't get their own chart panel:

- **Stress at maximum displacement (MPa)** — the stress recorded at the
  instant the specimen was most compressed. On an intact specimen this
  equals the peak stress exactly. If it's noticeably *lower* than the
  peak, that means the specimen kept compacting even while the load was
  already being removed — a distinct damage signature that no other single
  column shows on its own.
- **Hold length (samples)** — how long the detected dwell lasted, measured
  in *data samples*, not seconds (see "no creep rate" below for why). On
  its own this number means little; it's meant to be read alongside Hold
  displacement.
- **Hold displacement / 1000 samples (µm)** — the same hold displacement,
  normalized so cycles with different dwell lengths can be fairly ranked
  against each other. This is a *normalization*, not a real creep rate —
  it still isn't measured in time.

### "What this test cannot tell you"
Three explicit, deliberate limits — things people commonly assume a
compression test tells you, that this particular kind of test does not:

1. **The peak stress is not a strength.** It's the top step of the
   programmed test schedule, and the specimen unloaded normally afterward
   — nothing broke. An actual strength figure (like UCS — ultimate
   compressive strength) needs the specimen to be loaded to fracture,
   which this kind of cyclic test deliberately does not do.
2. **There is no creep rate.** The machine's export doesn't record a time
   channel — only cycle counts and dwell length in *samples*. Getting a
   real rate in mm/s would need the machine's time channel switched on at
   export time (a setting on the testing machine itself, not something the
   software can add after the fact).
3. **Fit quality is not measurement accuracy.** The "n" and "R²" figures
   attached to stiffness numbers describe how well a straight line fit the
   data points that went into that specific slope — they say nothing about
   whether the machine itself was calibrated correctly, how much give
   there was in the machine's own load frame, or whether the specimen was
   aligned properly.

---

## Footer note

*"Rendered from the frozen JSON record (schema v2). Curves are reduced for
drawing only; every number is computed on the full signal."*

Two separate things being said here:

- **"Frozen JSON record"** — once a specimen is committed, its analysis
  results are saved as a permanent JSON file. Everything on this page is
  read from that saved file, not recalculated live every time you open
  Results. If you want updated numbers after changing a threshold, you use
  Config's "Re-analyse this run" to regenerate that record — see the
  Config guide.
- **"Curves are reduced for drawing only"** — the raw signal a real test
  can contain tens of thousands of data points, far more than a chart
  needs to look smooth on screen. The *displayed* curve is a simplified
  version for rendering speed, but this only affects what's drawn — every
  number on this page (stiffness, energy, permanent deformation, all of
  it) is calculated from the complete, un-reduced signal, not the
  simplified drawing.
