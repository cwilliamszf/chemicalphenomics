# DanioVision analysis pipeline

Assigns each well of a 24-well DanioVision plate to an experimental group and
extracts summary locomotor metrics per well and per group (distance moved,
velocity, mobile/immobile "freezing" bouts, thigmotaxis, and activity binned
over time e.g. for light/dark comparisons) -- in the spirit of
[sthyme/ZebrafishBehavior](https://github.com/sthyme/ZebrafishBehavior)'s
well->group->metrics->stats workflow, adapted for Noldus
EthoVision/DanioVision output.

## Important: input format

**This pipeline reads EthoVision's "Raw data" export (CSV/Excel), not the
internal `.trk` files.**

The `.trk` files inside a DanioVision/EthoVision project's raw data folder
(named like `Track_filet0000a0004o0000_0001.trk`) are the software's
internal, undocumented binary track database. Inspecting real sample files
confirms they do contain a full per-frame schema -- a header block literally
names the fields `SubjectId`, `StartTime`, `X/Y/Z Coordinate`, `Area`,
`ChangedArea`, `Elongation`, `MergeState`, `Enter/Hidden Zone Id` -- but the
numeric payload after that header is packed in a proprietary layout that
isn't publicly documented. Guessing the byte layout from samples alone risks
silently producing plausible-looking but *wrong* distance/velocity numbers,
which isn't an acceptable risk for a data pipeline feeding real analysis.
`daniovision_pipeline/trk_probe.py` can safely read the safe parts of these
files (field names present, trial start time, file size) for sanity-checking
a raw data folder, but it deliberately does not decode trajectories.

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

### The filename -> well mapping problem

The `a####` arena index encoded in `.trk` filenames (and often echoed in raw
data export filenames as "Arena N") reflects the order arenas were drawn in
that project's Trial Setup grid. **There is no universal public standard for
this** -- it's specific to how your Trial Setup arena grid was configured.

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
  raw_export_parser.py      # reads EthoVision "Raw data" CSV/Excel exports
  trk_probe.py              # safe, metadata-only .trk inspection (no trajectory decoding)
  metrics.py                # per-well metric computation
  stats.py                  # group summaries + between-group tests
  plots.py                  # boxplots and activity-over-time plots
  cli.py                    # `python -m daniovision_pipeline.cli ...`
example_data/
  generate_example_data.py  # writes synthetic demo raw-data CSVs
```
