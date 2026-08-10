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

If you don't have (and can't get) a reference export, `raw_export_parser.py`
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
The X-pixel-range evidence above is a good way to check your own files: run
`ethovision_trk.py info` on each arena and confirm the X ranges step through
in the order you expect for your plate's physical layout (and that Y ranges
group by row).

`config/plate_layout_24well.csv` ships a row-major default for a 4-row x
6-column plate (arena 0 = A1, arena 1 = A2, ... arena 5 = A6, arena 6 = B1,
... arena 23 = D6), because that's how EthoVision's "Track multiple arenas"
wizard lays out a grid by default. **Verify this against your own Trial
Setup > Arena Settings screen (or the arena numbers overlaid on the video)
before trusting results**, and edit the CSV if your grid was drawn in a
different order (column-major, mirrored, custom).

## Setup

```bash
cd daniovision_pipeline
pip install -r requirements.txt
```

## Usage

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
| `plots/*.png` | boxplot (with individual wells overlaid) per metric by group, plus `activity_over_time.png` |

### Metrics glossary

- `total_distance_mm`, `mean_velocity_mms`, `max_velocity_mms` -- overall movement.
- `pct_time_mobile` / `pct_time_immobile` -- fraction of time above/below EthoVision's movement threshold (immobile time is commonly used as a freezing proxy).
- `mobile_bout_count`, `mean_mobile_bout_duration_s`, `mean_immobile_bout_duration_s` -- bout structure of movement.
- `pct_time_in_outer_zone`, `pct_distance_in_outer_zone` -- thigmotaxis: time/distance spent beyond `outer_zone_fraction` (default 45%) of the well radius from center. By default the well center/radius are estimated from each track's own extent; pass explicit `well_center_xy`/`well_radius_mm` via `MetricsConfig` for a fixed, calibrated well geometry if you have one.
- `distance_mm__<phase>`, `mean_velocity_mms__<phase>` -- per-phase breakdown (e.g. `__light` / `__dark`) if a light/stimulus state column was exported.

## Layout

```
config/
  plate_layout_24well.csv   # arena_index -> well_id (edit if your grid order differs)
  groups_template.csv       # well_id -> group (copy and fill in per experiment)
daniovision_pipeline/
  well_mapping.py           # filename -> arena -> well -> group resolution
  ethovision_trk.py         # reads .trk files directly: info / convert / calibrate / validate
  raw_export_parser.py      # reads EthoVision "Raw data" CSV/Excel exports
  trk_probe.py              # minimal .trk sanity check predating ethovision_trk.py
  metrics.py                # per-well metric computation (consumes raw_export_parser output)
  stats.py                  # group summaries + between-group tests
  plots.py                  # boxplots and activity-over-time plots
  cli.py                    # `python -m daniovision_pipeline.cli ...` (raw_export_parser-based pipeline)
example_data/
  generate_example_data.py  # writes synthetic demo raw-data CSVs
```
