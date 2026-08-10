"""End-to-end CLI: raw data folder + plate layout + groups -> metrics, stats, plots."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import metrics as metrics_mod
from . import plots as plots_mod
from . import stats as stats_mod
from .raw_export_parser import load_raw_track
from .well_mapping import PlateLayout, build_file_well_group_table, load_groups

RAW_EXTENSIONS = {".csv", ".xlsx", ".xls"}


def find_raw_files(raw_dir: Path) -> list[Path]:
    files = sorted(p for p in raw_dir.iterdir() if p.suffix.lower() in RAW_EXTENSIONS)
    if not files:
        raise FileNotFoundError(
            f"No .csv/.xlsx raw-data export files found in {raw_dir}. "
            "Export 'Raw data' for each well from EthoVision/DanioVision first "
            "(see README) -- this pipeline does not read .trk files directly."
        )
    return files


def run(
    raw_dir: Path,
    plate_layout_csv: Path,
    groups_csv: Path,
    outdir: Path,
    bin_size_s: float,
) -> None:
    outdir.mkdir(parents=True, exist_ok=True)

    raw_files = find_raw_files(raw_dir)
    plate_layout = PlateLayout.from_csv(plate_layout_csv)
    groups = load_groups(groups_csv)

    file_table = build_file_well_group_table(raw_files, plate_layout, groups)
    file_table.to_csv(outdir / "file_well_group_mapping.csv", index=False)
    print(f"Resolved {len(file_table)} file(s) to wells/groups "
          f"-> {outdir / 'file_well_group_mapping.csv'}")

    tracks_by_well = {}
    well_group = {}
    for _, row in file_table.iterrows():
        tracks_by_well[row["well_id"]] = load_raw_track(row["file"])
        well_group[row["well_id"]] = row["group"]

    cfg = metrics_mod.MetricsConfig(bin_size_s=bin_size_s)
    summary_df, binned_df = metrics_mod.compute_all_wells(tracks_by_well, well_group, cfg)
    summary_df.to_csv(outdir / "per_well_metrics.csv", index=False)
    binned_df.to_csv(outdir / "per_well_time_binned_activity.csv", index=False)
    print(f"Wrote per-well metrics -> {outdir / 'per_well_metrics.csv'}")

    group_summary_df = stats_mod.group_summary(summary_df)
    group_summary_df.to_csv(outdir / "group_summary.csv", index=False)

    comparisons_df = stats_mod.compare_groups(summary_df)
    comparisons_df.to_csv(outdir / "group_comparisons.csv", index=False)
    print(f"Wrote group summary and comparisons -> {outdir}")

    plot_dir = outdir / "plots"
    plots_mod.plot_all_metric_boxplots(summary_df, plot_dir)
    if not binned_df.empty:
        plots_mod.plot_binned_activity(binned_df, plot_dir / "activity_over_time.png")
    print(f"Wrote plots -> {plot_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True,
                         help="Folder of per-well EthoVision raw-data export files (.csv/.xlsx)")
    parser.add_argument("--plate-layout", type=Path,
                         default=Path(__file__).resolve().parents[1] / "config" / "plate_layout_24well.csv",
                         help="arena_index -> well_id CSV (default: config/plate_layout_24well.csv)")
    parser.add_argument("--groups", type=Path, required=True,
                         help="well_id -> group CSV (copy config/groups_template.csv and fill it in)")
    parser.add_argument("--outdir", type=Path, required=True, help="Output directory")
    parser.add_argument("--bin-size-s", type=float, default=60.0,
                         help="Time bin width (seconds) for activity-over-time plots")
    args = parser.parse_args()
    run(args.raw_dir, args.plate_layout, args.groups, args.outdir, args.bin_size_s)


if __name__ == "__main__":
    main()
