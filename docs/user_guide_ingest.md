# CompressLab User Guide — Ingest Page

This walks through every piece of text and every option on the **Ingest**
page, in the order it appears on screen: what it is, what it means in plain
language, and *why* it was built that way. Wherever a setting is a number,
there's a worked example using real numbers so it's concrete, not abstract.

The Ingest page has four numbered steps: **Upload → Thresholds → Preview →
Commit**. Nothing touches disk until step 4.

---

## Step 1 — Upload

### The file uploader
*"Compression test export(s)"*

Drop in one or more `.xlsx` exports from the testing machine. You can
select several files at once if they belong to the same test series.
Nothing is written to the workspace at this point — uploading just loads
the files into memory so the tool can read them. You could upload, look at
the preview, and close the tab without ever committing anything; nothing
would be saved.

### Material
This is a dropdown once at least one material already exists in the
workspace, and a plain text box the very first time (when there's nothing
to pick from yet).

**Why a dropdown instead of just typing a name every time?**

Material names are just text to the tool — it has no idea that "SteelMesh",
"Steel Mesh", and "steel-mesh" are meant to be the same thing. If you type
the name slightly differently each time you ingest a new batch, the tool
will silently create a *new, separate* material for each spelling. Nothing
errors, nothing warns you — you just end up with your data split across
three materials instead of one, and Results/Compare only ever show you a
third of what you actually have, because they don't know the other two
"materials" are actually the same thing.

Picking a name from the list instead of retyping it removes that risk
completely — you literally cannot mistype something you're selecting, not
typing.

**"+ Add new material…"**

This is the deliberate, on-purpose way to register a genuinely new
material. It's placed at the *bottom* of the list rather than being the
default, because most of the time when you ingest a new file, it's another
run of a material you already have (a second specimen, a repeat test) —
that's the common case, so it's the default behavior. Adding a brand-new
material is the exception, so it's one extra click, not the first thing you
see.

As a second line of defense, even if you *do* type a near-duplicate name
into the "new material" box (e.g. you meant to pick the existing "T050LR"
but typed "T050 LR"), the tool will try to match it to the existing entry
automatically when you commit, rather than silently creating a duplicate.
You'll see a message telling you it did that (see **"matched to the
existing material"** under Commit, below).

### "Test has a hold at peak"
Checked by default.

Most compression tests don't just load straight up and back down — they
hold (dwell) at peak stress for a while before unloading, to let the
material creep/relax. This checkbox tells the tool whether to look for that
dwell.

**Why would you ever uncheck it?** If your test cycles fast with *no*
programmed hold at all, the signal still spends a handful of samples
turning around at peak stress purely from machine geometry — that's not a
real hold, just the crosshead reversing direction. On a short enough cycle,
that turnaround can accidentally look long enough that the tool mistakes it
for a real dwell. Unchecking this skips hold-detection entirely, so every
cycle correctly reports "no hold, no creep" instead of a few false
positives scattered through an otherwise hold-free test.

### "Gauge length confirmed"
Unchecked by default.

This isn't about the test — it's about whether a *person* has verified that
the displacement sensor (extensometer) on this machine measures exactly the
specimen's own thickness (h₀), not something else in the load train (like
platen compliance or a longer travel distance that includes machine
give). If nobody has actually checked that, strain and modulus numbers stay
labeled "provisional" and carry a warning — not because the numbers are
necessarily wrong, but because nobody's confirmed the one assumption they
depend on. Checking this box is a manual sign-off, not something the tool
can verify itself from the data.

### The "looks like a file name, not a material" warning
This appears only if what you typed/picked as the Material looks like it
was actually copy-pasted from the export file's own name (e.g. "Mehrstufiger
Druckversuch Vergleichstest 2 T050LR1" instead of just "T050LR1"). The
tool guesses this from length — a real material code is short; a whole
file name is not.

It's just a nudge, not a blocker — you can commit anyway. The reasoning:
the full file name is *already* kept, in full, on every specimen record
regardless of what you name the material, so nothing is lost by using a
short code instead. A short code just reads far better as a card title in
Materials and as a legend entry in Compare. You can still rename it later
from the Materials tab if you ignore this now.

### "Different materials in this batch?"
Only appears once you've attached **more than one file**. Collapsed by
default, and every file inside it defaults to "(same as above)" — so the
common case (several files, all one material) needs zero extra clicks.

**Why does this exist?** Before this existed, Ingest had exactly one
Material field for the *entire* upload. If you uploaded two files meant for
two *different* materials in the same batch, the tool had no way to know
that — it would silently combine both files' specimens into one material,
under one name. There was no warning and no way to split them apart
afterward short of deleting everything and re-ingesting separately.

Only change a file's entry *inside* this expander if that specific file
belongs to a different material than the one you picked above.

---

## Step 2 — Thresholds

*("Optional." — you almost never need to touch this section. It exists for
the rare export that gets segmented wrong.)*

### The big idea, first

Every number in here is a **relative fraction** of the test's own peak
stress — never an absolute MPa value. That matters because the same
setting has to work whether your test peaks at 3 MPa or 450 MPa; a fixed
absolute number could never do that.

It's also important to understand that these numbers are **not** what
directly finds the cycles. The tool finds cycle boundaries itself, using
adaptive peak-detection that looks at each candidate peak against its own
local surroundings. Two of the numbers below (**Unload sensitivity** and
**Minimum cycle size**) are just *safety floors* on top of that automatic
detection — think of them as "reject this candidate if it clearly fails
this sanity check," not "this is the rule that decides everything." Same
idea for the stiffness window: the tool automatically finds the best-fit
window on the data first; **Stiffness window start/end** only kicks in as
a fallback on the rare cycle where nothing else clears the bar.

**When would you actually touch this?** Only if you run Preview (Step 3)
and see a real problem — a stage from your test missing entirely, or two
stages getting merged into one, or a cycle count that's obviously wrong.
For the vast majority of exports, the defaults are correct and untouched.

Every field below also shows its exact internal name (e.g. `unload_frac`)
in a small caption underneath. That's the same name used by the command-line
tool's flags (`--unload-frac`) and what "Settings this run used" on the
Config page prints back — so if you ever need to look something up or
reproduce a setting from the command line, that's the name to search for.

---

### Unload sensitivity (`unload_frac`) — default **0.5** (50%)

**What it does:** For a candidate peak to count as its *own separate*
cycle (rather than just a bump on the way up to a taller neighboring
peak), the valley on either side of it has to drop back down by at least
this fraction of *that candidate's own* peak height.

**Worked example** — a 10-stage test peaking at 450 MPa, where cycle 2
peaks at 50 MPa:

- 50% of 50 MPa = 25 MPa.
- For cycle 2 to be recognized as a real, separate cycle from cycle 1, the
  valley between them has to fall to **25 MPa or below**.
- If the valley only relaxes down to, say, 40 MPa (only "giving back" 10 MPa
  out of the 50 MPa peak — a 20% ratio), it fails the 50% requirement, and
  the tool would read cycles 1 and 2 as one merged cycle instead of two.

**Why 0.5, and why per-candidate rather than one fixed number for the whole
test?** A real separation between stages, on real tested data, was
measured to give back 72–90%+ of the peak almost every time (specimens
tend to unload nearly all the way between stages), while a real
false-alarm — a transient ramp overshoot that was never a genuine
separate stage — only measured about 13.5%. 50% sits with a wide, safe
margin on either side of both.

**When to touch it:** If Preview shows two real stages of your test getting
merged into one cycle, *lowering* this number (e.g. to 0.3) makes the tool
more willing to accept a shallower valley as a real separation. If it's
splitting one real stage into two spurious "cycles" because of a wiggle in
the signal, *raising* it makes the tool more conservative.

---

### Minimum cycle size (`major_cycle_frac`) — default **0.01** (1%)

**What it does:** Unlike Unload sensitivity above (which compares a
candidate against *itself*), this one compares a candidate's peak against
the **whole test's global peak**. Any candidate whose own peak doesn't even
reach this tiny fraction of the global peak is thrown out immediately, no
matter what its neighbors look like.

**Worked example** — same 450 MPa test:

- 1% of 450 MPa = 4.5 MPa.
- Any little blip in the signal that never gets above 4.5 MPa — for
  example, the machine's crosshead finding contact with the specimen right
  at the very start of the recording, before the real test even begins —
  gets discarded outright, regardless of anything else about it.

**Why so low?** This exists purely to catch near-zero noise/contact
artifacts at the very start of a recording — it is deliberately **not**
meant to be the thing that decides whether a real stage counts. A real
stage's validity is judged by Unload sensitivity and local signal noise,
not by how tall it is compared to some other stage. Keeping this low
matters especially for a test with many stages: in a 10-stage test, the
smallest real stage naturally sits around 1/10th of the global peak by
construction — if this floor were set too high (an older default used to
be 10%), it would be a coin-flip whether that legitimate first stage
survives at all.

---

### Stiffness window start / end (fallback) (`stiff_lo_frac` / `stiff_hi_frac`) — default **0.25 / 0.75** (25%–75%)

**What it does:** When the tool calculates a cycle's stiffness (how steep
the loading curve is), it needs to pick a window on that curve to fit a
straight line through. Normally the tool finds the *best* window
automatically for each cycle, from the data itself. These two numbers are
only used as a **fallback**, on the rare cycle where the automatic search
can't find a window that's wide enough or has enough data points to trust.

**Worked example** — cycle 10, peaking at 450 MPa:

- Fallback window = 25% to 75% of 450 MPa = **112.5 MPa to 337.5 MPa**.
- The tool would fit its stiffness line only through data points that fall
  in that stress range on the loading branch — but again, only if the
  automatic best-fit search already failed for that cycle.

**When to touch it:** Essentially never in normal use. If you do ever need
to force stiffness to be measured over a specific manually-chosen range
(overriding auto-detection's usual behavior on every cycle that needs the
fallback), this is where that fixed range comes from.

---

### Reference stress (`residual_stress_frac`) — default **0.02** (2%)

**What it does:** This is one single, low stress value, calculated as a
fraction of the *global* peak, that the tool reads on both the loading and
unloading portions of *every* cycle. It's used for two different things:

1. **Permanent deformation** — how much the specimen failed to spring back
   within one cycle (comparing the displacement at this reference stress on
   the way up vs. the way down).
2. **Cross-cycle comparison** — reading the same reference stress on every
   cycle lets you compare cycles against each other on equal footing.

**Worked example** — same 450 MPa test:

- 2% of 450 MPa = **9 MPa**.
- Even cycle 1, whose own peak is only 10 MPa, can still reach a 9 MPa
  reference point near its own peak — the reference stays low enough to be
  reachable on the very smallest stage of the test, not just the big ones.

**Why so low, specifically?** Two competing needs: it has to be low enough
to be reachable even on a small/single-stage test (a bigger reference, like
50% of the global peak, would be completely unreachable on a stage that
only ever gets to 10 MPa), but it also has to stay clear of the noisy,
unreliable region right around zero stress, where the specimen is losing
contact with the platen and the signal isn't meaningful. 2% of the global
peak was chosen and validated as comfortably inside both of those bounds
on real tested data.

**Watch for the warning:** If a *specific* cycle's own peak is small enough
that this reference stress sits too close to that noisy near-zero region
for that cycle specifically, the tool flags that cycle's permanent-set
number as less certain — not wrong, just worth a second look.

---

### Hold detection tolerance (`hold_tol_frac`) — default **0.005** (0.5%)

**What it does:** While a cycle is dwelling (holding) at peak stress, the
signal isn't perfectly flat — there's always a little wobble. This sets how
much the signal is allowed to drift, as a fraction of that cycle's own
peak, and still count as "the specimen is being held," rather than reading
it as the unload already starting.

**Worked example** — cycle 10, peak 450 MPa:

- 0.5% of 450 MPa = **2.25 MPa**.
- The signal can wander up or down by up to 2.25 MPa around the peak and
  the tool still treats it as "still holding," not as the unload beginning
  early.

**When to touch it:** If a genuinely long dwell is getting cut short in the
data (the tool thinks the hold ended earlier than it really did) because
your machine's signal is noisier than usual, raising this slightly gives
it more room. Lowering it makes hold detection stricter.

---

### Specimen thickness override (h0) (`h0_mm`)
Blank by default.

**What it does:** The specimen's thickness (h₀) is normally read
automatically from the metadata sheet inside the export file — it's what
strain and other thickness-normalized numbers (like Maximum strain, or
Permanent deformation as a %) are calculated from. This field lets you
type in a thickness manually instead, overriding whatever the file's own
metadata says (or filling the gap if the file has none at all).

**When to use it:** Only when you know the export's own metadata is
missing or wrong for this specific specimen, and you have a trustworthy
measured value to use instead. Leave it blank otherwise — it defaults to
reading the real value from the file, not to faking a number.

---

## Step 3 — Preview

*"Check the files parse, then open the full interactive dashboard in a new
tab, before anything is written."*

### "Run preview"

Runs the exact same analysis the tool would use if you committed — same
engine, same thresholds — but **writes nothing to disk**. It's a
look-before-you-commit step. If a file fails to parse outright, you'll see
an error naming that specific file. If everything parses, an "Open
dashboard in a new tab" button appears so you can look at the full,
interactive charts before deciding to commit.

### The "stale" caption
*"Upload or setting changed since this preview — run it again to see the
current file(s)."*

This appears if you change something — swap in a different file, edit a
threshold, flip the material split — **after** running Preview but without
running it again. Rather than silently keep showing you the *old* preview
(which used to genuinely happen and looked exactly like it was still
current), the tool now notices the mismatch and tells you plainly that
what's on screen is out of date.

### "Open dashboard in a new tab"

Opens the exact same interactive dashboard that Results shows for
committed data — full charts, all cycles, hover tooltips, copy/download —
except this data was never written anywhere. It's genuinely disposable:
close the tab, and nothing about it persists unless you separately click
Commit.

### The "showing N most recent of M specimens" caption

Only appears if your batch has more specimens than the dashboard's colour
palette can display at once (there's a hard limit on how many series one
chart legend can distinctly colour). It's telling you the chart is showing
a subset, not silently hiding data without saying so.

---

## Step 4 — Commit

*"Archives the raw file and writes the record - re-running the same file
is a no-op."*

This is the step that actually writes to the workspace. Everything before
this point was just analysis in memory.

### "Archive a copy of the uploaded file"
Checked by default.

Copies your uploaded export file into the workspace's `Raw exports/`
folder before analysis, so every result can always be traced back to the
*exact bytes* that produced it later. If you already keep your own copies
of these files elsewhere and don't want a second copy taking up disk
space here, uncheck it. Either way — checked or not — the file's SHA-256
hash (a unique fingerprint) is always recorded, and that hash is what the
tool uses to notice "you already ingested this exact file" if you
accidentally upload it again.

### "Write per-run Excel/CSV/HTML"
Checked by default.

Writes an extra Excel workbook, CSV, and HTML report for *this specific
run*, alongside the permanent JSON record. If you only ever look at the
combined report for a whole material (under `reports/<material>`, see the
Config page) and find these individual per-run copies redundant, uncheck
this. Either way, the JSON record and the underlying curve data — which
the combined report and every chart get rebuilt from — are **always**
written; this checkbox only controls the extra convenience copies.

### "Commit to workspace"

The actual save button. If you didn't pick or type a material name, this
will show an error asking you to do that first rather than silently
guessing.

### "matched to the existing material" message

If what you typed for a new material was close enough to an existing
material's name that the tool judged it was almost certainly meant to be
the same one, it automatically uses the existing name instead of creating
a near-duplicate — and tells you it did that, so it's never a silent
surprise.

### The success message

Tells you how many specimens were ingested, under which material name,
and exactly which folder the run was written to — plus a summary block
and any warnings the analysis produced for this specific run (the same
kind of caution/critical notices you'd see on the dashboard).

---

## Quick reference: every threshold, at a glance

| Setting | Internal name | Default | What it's a fraction of |
|---|---|---|---|
| Unload sensitivity | `unload_frac` | 0.5 (50%) | the *candidate cycle's own* peak |
| Minimum cycle size | `major_cycle_frac` | 0.01 (1%) | the *whole test's* global peak |
| Stiffness window start | `stiff_lo_frac` | 0.25 (25%) | that cycle's own peak (fallback only) |
| Stiffness window end | `stiff_hi_frac` | 0.75 (75%) | that cycle's own peak (fallback only) |
| Reference stress | `residual_stress_frac` | 0.02 (2%) | the *whole test's* global peak |
| Hold detection tolerance | `hold_tol_frac` | 0.005 (0.5%) | that cycle's own peak |
