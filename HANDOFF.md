# Compression Analysis Tool v2 — Handoff Brief

Reference implementation of the calculation engine: `compression_core.py`.
Validated against both real export formats. Continue the build from here.

---

## 1. What the sample data actually revealed

These findings overturned parts of the original plan. Read before changing anything.

| Finding | Consequence |
|---|---|
| Both tests are **multi-stage**: peak stress rises every cycle (50→300 MPa, 50→450 MPa) | Cycles are not repeats of each other. Any metric tied to "this cycle's own peak" is **not comparable across cycles**. |
| Old tool's default `major_cycle_threshold = 5 MPa` | Would have discarded **every cycle** of a 2 MPa test, and is meaningless against a 450 MPa test. All thresholds must be relative. |
| Displacement collapses to a ~2–4 µm baseline at near-zero stress (contact loss) | A zero-referenced permanent set is **unreliable**. The original tool's choice to read residual displacement at a low non-zero stress was **correct** — keep it. |
| Displacement keeps rising during the dwell, so it peaks **after** stress does | Splitting the hysteresis loop at max *stress* produced negative dissipated energy. Split at max *displacement*. |
| Every cycle in both files has a long dwell at peak (~900–3000 points) and a second dwell near zero | Hold detection from data works reliably; never ask the user. |
| Displacement units differ per file (mm vs µm), and one file has two displacement channels | Units must be parsed from the header row; extensometer channel preferred over crosshead (crosshead includes machine compliance). |
| `Mehrstufiger` file carries h0 = 0.471 mm, d0 = 16 mm in its metadata sheets; the `TALCO50` file carries none | Strain normalisation is automatic for one format, needs a user-supplied h0 for the other. Suppress strain columns rather than faking them. |

## 2. Engine status — done and validated

- Format auto-detection (`single` vs `series` workbook layouts)
- Unit parsing and conversion from the header rows
- Metadata extraction (h0, d0, temperature) per specimen
- Relative-threshold cycle segmentation with valley expansion
- Automatic hold/dwell detection
- Metrics: peak, residual displacement, permanent deformation (cumulative + incremental), stiffness (common band + relative band, each with n-points and R²), energy in/dissipated/relative hysteresis loss, creep during hold
- Strain-normalised variants when h0 is known

Validation result: 6 cycles found in `TALCO50`, 9 in each `Mehrstufiger` specimen; zero unphysical values; the two specimens of the same series agree closely (good repeatability signal).

## 3. Metric definitions and why

- **Permanent deformation** — residual displacement read on the *loading* branch at a low common reference stress (default 2% of global peak), then referenced to cycle 1 (cumulative) and to the previous cycle (incremental). Not zero-referenced, because of contact loss. Note this differs from ASTM D395 / ISO 815 compression set, which is a long-duration static test — do not label it "compression set" in reports.
- **Stiffness** — reported twice, and both are needed: `Stiffness_common` uses an identical stress window in every cycle (25–75% of the *smallest* cycle peak), so it is valid to compare across stages and materials; `Stiffness_relative` uses 25–75% of each cycle's own peak, which describes that cycle faithfully but rises artificially as stages climb. Always show which is which.
- **Energy** — `HysteresisLoss_rel` (dissipated ÷ input) is the cross-test comparable form. Absolute loss scales with stress amplitude, so a 50 MPa stage and a 450 MPa stage cannot be compared on the absolute number.
- **Creep** — displacement gained across the detected dwell. Omitted entirely (not zero, not guessed) when no dwell exists.

## 4. Build order from here

1. **Persistence layer** — `raw_input/` (immutable) → `processed_output/{material}_{date}/` (JSON + CSV + XLSX + HTML) → `knowledge_base.db` (SQLite index, rebuildable from the JSONs, never the source of truth). One JSON per specimen: metadata, config used, per-cycle metrics, source file path.
2. **Excel export** — flat per-cycle table with real headers and units, plus a summary sheet. This was an explicit request.
3. **Streamlit UI** — pages: Ingest (drag-drop N files, show detected format/cycles/holds for visual confirmation *before* committing), Results (dashboard + plain table), Compare (multi-select materials from the knowledge base, overlay), Config (the former hand-edited knobs as form fields).
4. **Dashboard rework** — plot against the common-band metrics by default; flag low-`n`/low-`R²` stiffness points instead of plotting them as solid.
5. **Regression tests** — pin the validated numbers above so future changes cannot silently shift results.

## 5. Open items needing a decision

- **h0 for exports without a metadata sheet** — prompt in the UI, or maintain a material/specimen lookup table?
- **No time channel in either export.** Hold length is currently measured in sample points, so creep *rate* cannot be computed, only total creep across the dwell. If creep rate matters, the export settings need a time channel enabled (see the export dialog screenshot).
- Whether to add stretched-exponential relaxation fitting during the dwell (gives relaxed fraction + time constant, per the cyclic-compression literature) — worthwhile later, not needed for v1.
