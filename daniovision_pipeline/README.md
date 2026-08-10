# DanioVision analysis pipeline

Assigns each well of a 24-well DanioVision plate to an experimental group and
extracts summary locomotor metrics per well and per group (distance moved,
velocity, mobile/immobile "freezing" bouts, thigmotaxis, and activity binned
over time e.g. for light/dark comparisons) -- in the spirit of
[sthyme/ZebrafishBehavior](https://github.com/sthyme/ZebrafishBehavior)'s
well->group->metrics->stats workflow, adapted for Noldus
EthoVision/DanioVision output.

## Reading raw `.trk` files directly

`daniovision_pipeline/ethovision_trk.py` reads DanioVision/EthoVision's
internal per-arena `.trk` files (named like
`Track_filet0000a0004o0000_0001.trk`) directly, without needing an
EthoVision export. It reverse engineers a fixed 58-byte-per-frame record
(X/Y/Z Coordinate, Area, ChangedArea, Elongation, MergeState, Enter/Hidden
Zone Id) after a header block that names those fields in UTF-16 text -- see
the module docstring for the full byte layout.

This has been validated against the 5 sample arenas in this project two
independent ways: its own built-in structural sanity checks (MergeState only
takes valid states, Area is an integral pixel count, Elongation falls in
`[0, 1]`) pass on all 5 files, and the recovered per-arena X-pixel ranges
are cleanly non-overlapping and evenly spaced (arena 0: 24-184px, arena 1:
232-393px, arena 2: 440-597px, arena 3: 647-810px, arena 4: 855-1017px, all
sharing the same 81-245px Y range) -- exactly what 5 wells side by side in
one row of a plate should look like. That structure would not appear from a
misaligned parse.

```bash
python daniovision_pipeline/ethovision_trk.py info FILE.trk
python daniovision_pipeline/ethovision_trk.py convert FILE.trk -o out.csv [--interpolate] [--smooth 11]
```

**Two things to know before trusting numbers derived from this:**

1. **Units are raw image pixels, with no physical scale**, unless you
   calibrate. If you have (or can pull) one EthoVision "Raw data" export for
   the same project, run `calibrate`/`validate` (below) to recover cm-per-
   pixel and an origin, then pass `--scale`/`--x0`/`--y0` to `convert` to get
   real cm coordinates. Without calibration, distances/velocities are only
   meaningful as *relative* comparisons between wells/groups in the same
   recording (same camera, same frame) -- not as absolute figures.
2. **This is raw, unsmoothed, non-interpolated detector output.** EthoVision's
   own export has already linearly interpolated missing samples and applied
   your Detection Settings' track smoothing. On the dataset this script's
   author validated against, distance moved computed from raw coordinates
   was 4.27x EthoVision's own exported value. `--interpolate` matches
   EthoVision's gap handling; `--smooth N` is a tricube local-linear
   approximation of its smoother, not an exact match. Do not directly compare
   raw- or approximately-smoothed-derived distance/velocity numbers against
   numbers EthoVision itself reported, or across pipelines that differ in
   whether/how they smooth.

```bash
python daniovision_pipeline/ethovision_trk.py calibrate FILE.trk --export raw_export.xlsx --sheet 1
python daniovision_pipeline/ethovision_trk.py validate  FILE.trk --export raw_export.xlsx --sheet 1
```

### Better: use the paired `FilteredTrackFile*.btn` if you have it

EthoVision's raw data folder holds a second file per arena/object, named
like `FilteredTrackFileh0000t0000a0000o0000_0001.btn` next to
`Track_filet0000a0000o0000_0001.trk`. **It uses the exact same 58-byte
record layout as the `.trk` file** -- `ethovision_trk.py` reads it with no
changes -- but every X/Y position has already been run through EthoVision's
own track smoothing and gap-filling, in the same pixel space. This sidesteps
caveat 2 above entirely: no approximate `--smooth` needed, no 4.27x
raw-vs-real discrepancy, and you still don't need an EthoVision export.
Caveat 1 (no physical scale) still applies -- calibrate as above if you need
cm.

Confirmed on the sample arena-0 pair in this project with the `compare`
subcommand:

```bash
python daniovision_pipeline/ethovision_trk.py compare Track_filet0000a0000o0000_0001.trk FilteredTrackFileh0000t0000a0000o0000_0001.btn
```

- Area and Elongation (untouched by position smoothing) matched to float
  rounding noise (~1e-8) -- confirms the two files are a genuine matched
  pair and the record alignment is correct in both.
- All 27 raw-missing frames got a filled-in position in the filtered file
  (their `MergeState` still reads `-1`, so you can still tell they were
  originally undetected).
- Every frame's position changed slightly, not just the gaps -- this is a
  real smoothing pass over the whole trajectory, not a naive gap-fill.
- Total distance moved dropped from ~75,772 px (raw) to ~28,554 px
  (filtered) on this file, the same direction and rough magnitude as the
  4.27x raw/export gap `ethovision_trk.py`'s author found -- consistent
  with the filtered file being close to what EthoVision itself would report.

If you can pull the `.btn` alongside each well's `.trk`, prefer it as the
input to `convert` for all downstream metrics.

If you have neither a `.btn` pair nor a reference export, `raw_export_parser.py`
below still reads EthoVision's own export directly for the metrics pipeline
-- calibrated, interpolated, and smoothed already, at the cost of needing to
export every well by hand instead of pointing the pipeline at the raw data
folder.

## Alternative: EthoVision's own "Raw data" export

The safe, accurate, officially supported path is EthoVision's own raw data
export:

1. Open your project in EthoVision XT / DanioVision.
2. Go to the **Analysis** tab -> **Statistics**.
3. Choose **Raw data** as the export/output type.
4. Select all tracks (one per well/arena) for the trial.
5. Export to Excel or CSV -- one file per well (or a combined export; either
   works, see below).

This produces, per well, a small property header followed by a per-frame
data table with columns such as `Trial time`, `X center`, `Y center`,
`Distance moved`, `Velocity`, `Movement`, and (if you used a light/dark
protocol) a light/stimulus state column -- exactly what this pipeline reads.

## The filename -> well mapping problem

The `a####` arena index encoded in `.trk` filenames (and often echoed in raw
data export filenames as "Arena N") reflects the order arenas were drawn in
that project's Trial Setup grid. **There is no universal public standard for
this** -- it's specific to how your Trial Setup arena grid was configured.

For *this* project's plate, it's been directly confirmed from all 24 arenas'
pixel coordinates (not just assumed): each arena's X/Y-pixel range clusters
into a clean 6-column x 4-row grid, with arena index increasing left-to-right
then top-to-bottom --

```
arena   x range (px)        y range (px)
  0     24 - 184             84 - 245       row 0
  1    232 - 393             81 - 245       row 0
  2    440 - 597             83 - 245       row 0
  3    647 - 810             83 - 246       row 0
  4    855 -1017             82 - 244       row 0
  5   1062 -1223             85 - 249       row 0
  6     22 - 182            292 - 453       row 1
  ...                                       (rows 1-3 follow the same pattern)
 23   1067 -1231            711 - 874       row 3
```

i.e. arena_index = row*6 + col, which is exactly the row-major default
already in `config/plate_layout_24well.csv` (arena 0 = A1, arena 1 = A2,
... arena 5 = A6, arena 6 = B1, ... arena 23 = D6) -- so no edits needed for
this plate. This *cannot* tell you the plate's physical orientation, though
(camera could be mirrored or rotated relative to how you're reading the
plate by eye) -- confirm arena 0 is really the well you think it is (e.g.
A1, not A6 or D1) against your Trial Setup > Arena Settings screen or the
arena numbers overlaid on the video before trusting group assignments.

For a different plate/project, rerun this check yourself: `ethovision_trk.py
info` each arena and confirm the X ranges step through in the order you
expect for your plate's physical layout (and that Y ranges group by row),
then edit `plate_layout_24well.csv` if your grid was drawn in a different
order (column-major, mirrored, custom).

## Setup

```bash
cd daniovision_pipeline
pip install -r requirements.txt
```

## GUI

A local, no-command-line-flags way to run everything below: point it at your
`.trk`/`.btn` folder, fill in wells/groups and periods as editable tables,
click Run.

```bash
streamlit run daniovision_pipeline/gui_app.py
```

This opens in your browser (must be run on the machine that can see your
data folder -- it needs local filesystem access, so this has to run on your
own computer, not a remote/cloud session). It's a thin UI shell around
`cli.py`: every field maps to a CLI flag, and clicking Run calls the exact
same `run()` function the CLI uses, so the two are always in sync and there
are no separate code paths to keep consistent. Everything from here down in
the README describes the same options and output either interface produces
-- read it either way, the GUI just fills in the CSVs and flags for you:

- **Data**: choose `.trk`/`.btn` folder or EthoVision raw-data export folder,
  with a "Browse..." folder picker (falls back to a plain text field if
  `tkinter` isn't available -- e.g. on Linux you may need
  `sudo apt install python3-tk`).
- **Wells & groups**: an editable table (one row per well), plus a "fill all
  with" shortcut for setting every well to one group before hand-editing the
  few that differ. Every row must have a group before you can run.
- **Periods**: an editable table (name, start/end in minutes) pre-filled
  with this project's light/startle/dark protocol as a starting example --
  edit or delete rows to match your own, or clear the table for whole-trial-
  only analysis.
- **Run**: shows a progress spinner, the same log `cli.py` prints to the
  terminal, the per-well metrics table, the activity-over-time plot inline,
  and a button to download every output file as a single .zip.

If you'd rather script it (batch runs, no browser), use the CLI directly --
same options, see below.

## Usage

Two input modes -- pick whichever matches what you have:

**A. Direct from `.trk`/`.btn` files** (no EthoVision export needed):

1. Put all arenas' files in one folder. Include the paired
   `FilteredTrackFile*.btn` next to each `Track_file*.trk` when you have it
   -- the pipeline prefers it automatically (see above for why).
2. Check/edit `config/plate_layout_24well.csv` if your arena grid wasn't
   drawn row-major (see above).
3. Copy `config/groups_template.csv`, fill in a `group` label for every well.
4. Run:

```bash
python -m daniovision_pipeline.cli \
  --trk-dir /path/to/trk_folder \
  --groups /path/to/your_groups.csv \
  --outdir /path/to/results \
  --bin-size-s 60
```

Positions stay in raw pixels (metrics come out as `total_distance_px` etc.)
unless you pass `--scale <cm_per_px> --x0 <cm> --y0 <cm>` from
`ethovision_trk.py calibrate` against a reference export, in which case
you'll get `total_distance_mm` etc. instead.

There's no EthoVision Movement classification in the `.trk`/`.btn` record
schema (see `trk_loader.py`'s docstring for why), so `pct_time_mobile` and
the bout metrics come from a velocity threshold instead by default
(`--mobility-threshold auto`, the default): smooth each well's velocity over
`--mobility-smoothing-window-s` (default 0.5s -- raw frame-to-frame velocity
is too noisy to threshold directly, it produces thousands of 1-4-frame
flicker "bouts"), pool the smoothed velocity across every well in the run,
and use its `--mobility-percentile` (default 25, i.e. bottom quartile of
speed = immobile) as a single threshold applied to all wells. The resolved
threshold is printed and written to `run_info.txt` in the output folder.
**This is a heuristic speed cutoff, not a recovered EthoVision setting** --
sanity check `pct_time_mobile`/bout counts against a couple of videos, and
override with `--mobility-threshold <value>` (a specific px/s or mm/s
number) if the default doesn't match what you see. Pass
`--mobility-threshold none` to leave these metrics blank instead (the old
behavior). Distance/velocity/thigmotaxis are unaffected either way.

**B. EthoVision's own "Raw data" export:**

1. **Export raw data** for all 24 wells from EthoVision into one folder
   (CSV or Excel, one file per well/arena).
2. **Check/edit the plate layout** in `config/plate_layout_24well.csv` if
   your arena grid wasn't drawn row-major (see above).
3. **Assign groups**: copy `config/groups_template.csv` and fill in a
   `group` label (e.g. treatment, genotype) for every well you used.
4. **Run the pipeline**:

```bash
python -m daniovision_pipeline.cli \
  --raw-dir /path/to/raw_data_exports \
  --groups /path/to/your_groups.csv \
  --outdir /path/to/results \
  --bin-size-s 60
```

(`--plate-layout` defaults to `config/plate_layout_24well.csv`; pass your
own if you edited a copy instead.)

## Analyzing protocol periods (light/dark, acoustic startle, ...)

Add `--periods /path/to/periods.csv` to either mode above to additionally
break the analysis down by named time-windows within the trial -- e.g. a
light phase followed by a dark phase. This is a plain time filter (start_s
to end_s, from the start of the trial) applied before the exact same
metrics/stats/plots pipeline used for the whole trial, so it works
identically regardless of input mode, calibration, or mobility threshold --
nothing period-specific needs to know about any of that (see `periods.py`).
The whole-trial output always runs too; periods are additional, not a
replacement.

Copy `config/periods_template.csv` and edit it, e.g. for a 10-minute light
phase, a 5-minute startle phase, then a 35-minute dark phase (already
provided as `config/periods_light10_startle5_dark35.csv` -- this is the
real plate's protocol):

```csv
period,start_s,end_s
Light phase,0,600
Startle,600,900
Dark phase,900,3000
```

(a simpler 2-phase light/dark-only example is also provided as
`config/periods_light10_dark40.csv`, if that matches your protocol instead)

```bash
python -m daniovision_pipeline.cli \
  --trk-dir /path/to/trk_folder \
  --groups /path/to/your_groups.csv \
  --outdir /path/to/results \
  --periods config/periods_light10_startle5_dark35.csv
```

Each period gets its own subfolder, with the same files as the top-level
whole-trial output: `<outdir>/periods/<period_name>/{per_well_metrics,
group_summary,group_comparisons}.csv` and
`<outdir>/periods/<period_name>/plots/`. The whole-trial output at
`<outdir>` itself always runs too, regardless of `--periods` -- periods are
additional, never a replacement. The mobility threshold (if auto-computed)
is resolved once from the *whole* trial and reused for every period, so
mobile/immobile fractions stay comparable across periods instead of each
period silently getting its own cutoff.

The whole-trial `activity_over_time.svg` also gets a dashed vertical line
at every period boundary, with each span labeled along the top -- so you
can see e.g. exactly where "Startle" sits inside the full activity trace,
not just as an isolated 5-minute plot in its own subfolder. Individual
per-period plots aren't annotated this way (nothing else to mark once a
plot is already sliced to one period).

For a side-by-side view without opening every subfolder, `--periods` also
writes three combined files at `<outdir>` stacking the whole trial and
every period together with a `period` column distinguishing rows:
`all_periods_per_well_metrics.csv`, `all_periods_group_summary.csv`,
`all_periods_group_comparisons.csv`. These summarize exactly the same
numbers as the individual per-period files (e.g. a well's `light` +
`dark` `total_distance_px` sums to its `whole_trial` value) -- they're a
convenience view, not a separate computation.

On the real 24-well plate this surfaced a textbook larval zebrafish
dark-flash response in the `dark` period's `activity_over_time.svg`: a burst
of activity right at lights-off that decays over the next ~15-20 minutes,
visible in both groups.

For repeated brief-stimulus protocols (e.g. acoustic startle pulses), list
several short windows under the same period name to pool them --
distance/velocity/thigmotaxis pool correctly across the disjoint windows,
but see `periods.py`'s module docstring for a caveat on bout metrics
(`mobile_bout_count` etc.) in that specific case.

### Try it on synthetic demo data first

To see the expected input/output shape without real data:

```bash
python example_data/generate_example_data.py
python -m daniovision_pipeline.cli \
  --raw-dir example_data/synthetic_raw \
  --groups example_data/synthetic_groups.csv \
  --outdir example_data/synthetic_output
```

This data is entirely fabricated (5 wells, 2 groups, a light/dark split) --
it is **not** derived from the real `.trk` files, only used to exercise the
parser/metrics/stats/plots code end to end.

## Output

Written to `--outdir`:

| File | Contents |
|---|---|
| `file_well_group_mapping.csv` | which raw file -> which well -> which group (check this first) |
| `per_well_metrics.csv` | one row per well, all summary metrics |
| `per_well_time_binned_activity.csv` | distance moved per time bin, per well |
| `group_summary.csv` | mean / SEM / N per metric per group |
| `group_comparisons.csv` | omnibus test (Welch's t-test or one-way ANOVA) and pairwise p-values per metric, sorted by p-value |
| `plots/*.svg` | boxplot (with individual wells overlaid) per metric by group, plus `activity_over_time.svg` (dashed lines + labels mark `--periods` boundaries on the whole-trial plot) |
| `periods/<name>/...` | *(only with `--periods`)* the same five items above, computed on just that period's time window |
| `all_periods_per_well_metrics.csv`, `all_periods_group_summary.csv`, `all_periods_group_comparisons.csv` | *(only with `--periods`)* whole trial + every period stacked together with a `period` column, for a side-by-side view |

### Metrics glossary

Metric names carry a unit suffix that depends on input mode: `_mm`/`_mms`
from `--raw-dir` (or a calibrated `--trk-dir`), `_px`/`_pxs` from an
uncalibrated `--trk-dir`. Below, `<unit>` stands for whichever applies.

- `total_distance_<unit>`, `mean_velocity_<unit>s`, `max_velocity_<unit>s` -- overall movement.
- `pct_time_mobile` / `pct_time_immobile` -- fraction of time above/below a movement threshold (immobile time is commonly used as a freezing proxy). From `--raw-dir`: EthoVision's own Movement classification. From `--trk-dir`: this pipeline's own smoothed-velocity threshold (`--mobility-threshold`, see above) -- same concept, different (heuristic) source; check `run_info.txt` in the output folder for which one and what threshold was used.
- `mobile_bout_count`, `mean_mobile_bout_duration_s`, `mean_immobile_bout_duration_s` -- bout structure of movement, from whichever mobility source applies (same as above).
- `pct_time_in_outer_zone`, `pct_distance_in_outer_zone` -- thigmotaxis: time/distance spent beyond `outer_zone_fraction` (default 45%) of the well radius from center. By default the well center/radius are estimated from each track's own extent; pass explicit `well_center_xy`/`well_radius` via `MetricsConfig` for a fixed, calibrated well geometry if you have one.
- `distance_<unit>__<phase>`, `mean_velocity_<unit>s__<phase>` -- per-phase breakdown (e.g. `__light` / `__dark`) if a light/stimulus state column was exported. **`--raw-dir` only.**

## Layout

```
config/
  plate_layout_24well.csv   # arena_index -> well_id (edit if your grid order differs)
  groups_template.csv       # well_id -> group (copy and fill in per experiment)
  periods_template.csv      # period,start_s,end_s (copy and edit per protocol)
  periods_light10_startle5_dark35.csv  # ready-made: this plate's protocol (light/startle/dark)
  periods_light10_dark40.csv  # ready-made: simpler 2-phase light/dark-only example
daniovision_pipeline/
  well_mapping.py           # filename -> arena -> well -> group resolution
  ethovision_trk.py         # reads .trk/.btn files directly: info / convert / calibrate / validate / compare
  trk_loader.py             # .trk/.btn -> tidy DataFrame for the metrics pipeline (pixel or calibrated mm)
  raw_export_parser.py      # reads EthoVision "Raw data" CSV/Excel exports -> the same tidy DataFrame shape
  trk_probe.py              # minimal .trk sanity check predating ethovision_trk.py
  periods.py                # named time-windows -> track slicing, for --periods
  metrics.py                # per-well metric computation, unit-agnostic (mm or px, see glossary above)
  stats.py                  # group summaries + between-group tests
  plots.py                  # boxplots and activity-over-time plots
  cli.py                    # `python -m daniovision_pipeline.cli --trk-dir ... | --raw-dir ... [--periods ...]`
  gui_app.py                # `streamlit run daniovision_pipeline/gui_app.py` -- thin UI shell around cli.py
example_data/
  generate_example_data.py  # writes synthetic demo raw-data CSVs
```
