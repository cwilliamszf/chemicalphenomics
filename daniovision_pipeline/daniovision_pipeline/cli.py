"""End-to-end CLI: raw data folder + plate layout + groups -> metrics, stats, plots.

Two input modes, pick one:

--raw-dir  EthoVision "Raw data" CSV/Excel exports (one file per well).
--trk-dir  A folder of raw .trk files, ideally with their paired
           FilteredTrackFile*.btn files alongside them (preferred when
           present -- see ethovision_trk.py and the README for why). No
           EthoVision export needed. Positions are uncalibrated pixels
           unless you pass --scale (from `ethovision_trk.py calibrate`).

Optional --periods: analyze one or more named protocol time-windows (e.g.
light/dark) separately, in addition to the whole trial -- see periods.py.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from . import metrics as metrics_mod
from . import plots as plots_mod
from . import stats as stats_mod
from .periods import load_periods, slice_to_period
from .raw_export_parser import load_raw_track
from .trk_loader import load_trk_track
from .well_mapping import PlateLayout, build_file_well_group_table, load_groups, parse_arena_index_from_filename

RAW_EXTENSIONS = {".csv", ".xlsx", ".xls"}
TRK_EXTENSIONS = {".trk", ".btn"}


def find_raw_files(raw_dir: Path) -> list[Path]:
    files = sorted(p for p in raw_dir.iterdir() if p.suffix.lower() in RAW_EXTENSIONS)
    if not files:
        raise FileNotFoundError(
            f"No .csv/.xlsx raw-data export files found in {raw_dir}. "
            "Export 'Raw data' for each well from EthoVision/DanioVision first, "
            "or use --trk-dir to read .trk/.btn files directly (see README)."
        )
    return files


def find_trk_files(trk_dir: Path) -> list[Path]:
    """One file per arena: FilteredTrackFile*.btn if present, else Track_file*.trk."""
    candidates = [p for p in trk_dir.iterdir() if p.suffix.lower() in TRK_EXTENSIONS]
    by_arena: dict[int, dict[str, Path]] = {}
    unresolved = []
    for p in candidates:
        arena = parse_arena_index_from_filename(p.name)
        if arena is None:
            unresolved.append(p)
            continue
        kind = "filtered" if p.suffix.lower() == ".btn" else "raw"
        by_arena.setdefault(arena, {})[kind] = p
    if unresolved:
        names = ", ".join(p.name for p in unresolved)
        raise ValueError(f"Could not determine the arena for: {names}")
    if not by_arena:
        raise FileNotFoundError(
            f"No .trk/.btn files found in {trk_dir}. Use --raw-dir instead if you "
            "have EthoVision 'Raw data' CSV/Excel exports (see README)."
        )
    chosen = []
    for arena in sorted(by_arena):
        paths = by_arena[arena]
        chosen.append(paths.get("filtered", paths.get("raw")))
    return chosen


def analyze(
    tracks_by_well: dict[str, pd.DataFrame],
    well_group: dict[str, str],
    cfg: metrics_mod.MetricsConfig,
    outdir: Path,
) -> None:
    """Metrics -> per-well table -> group stats -> plots, for one set of tracks.

    Shared by the whole-trial run and each --periods window: a period is
    just a different (smaller) tracks_by_well, everything downstream of
    slicing is identical.
    """
    outdir.mkdir(parents=True, exist_ok=True)
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


def run(
    raw_dir: Path | None,
    trk_dir: Path | None,
    plate_layout_csv: Path,
    groups_csv: Path,
    outdir: Path,
    bin_size_s: float,
    scale: float | None = None,
    x0: float = 0.0,
    y0: float = 0.0,
    mobility_threshold: str | None = "auto",
    mobility_percentile: float = 25.0,
    mobility_smoothing_window_s: float = 0.5,
    periods_csv: Path | None = None,
) -> None:
    outdir.mkdir(parents=True, exist_ok=True)

    if trk_dir is not None:
        input_files = find_trk_files(trk_dir)

        def loader(path: str) -> pd.DataFrame:
            return load_trk_track(path, scale=scale, x0=x0, y0=y0)
    else:
        input_files = find_raw_files(raw_dir)
        loader = load_raw_track

    plate_layout = PlateLayout.from_csv(plate_layout_csv)
    groups = load_groups(groups_csv)

    file_table = build_file_well_group_table(input_files, plate_layout, groups)
    file_table.to_csv(outdir / "file_well_group_mapping.csv", index=False)
    print(f"Resolved {len(file_table)} file(s) to wells/groups "
          f"-> {outdir / 'file_well_group_mapping.csv'}")

    tracks_by_well = {}
    well_group = {}
    for _, row in file_table.iterrows():
        tracks_by_well[row["well_id"]] = loader(row["file"])
        well_group[row["well_id"]] = row["group"]

    cfg = metrics_mod.MetricsConfig(bin_size_s=bin_size_s, mobility_smoothing_window_s=mobility_smoothing_window_s)

    run_info_lines = []
    has_mobility_state = any("mobility_state" in t.columns for t in tracks_by_well.values())
    if has_mobility_state:
        run_info_lines.append("mobility source: EthoVision Movement classification (mobility_state column)")
    elif mobility_threshold is not None and mobility_threshold.lower() not in ("none", "off"):
        if mobility_threshold.lower() == "auto":
            threshold = metrics_mod.compute_pooled_mobility_threshold(
                tracks_by_well, percentile=mobility_percentile, smoothing_window_s=mobility_smoothing_window_s
            )
            source_desc = (
                f"auto: {mobility_percentile:g}th percentile of {mobility_smoothing_window_s:g}s-smoothed "
                f"velocity, pooled across all {len(tracks_by_well)} wells in this run"
            )
        else:
            threshold = float(mobility_threshold)
            source_desc = "explicit --mobility-threshold value"
        cfg.mobility_velocity_threshold = threshold
        msg = (
            f"Mobility threshold: {threshold:.4g} (per-second unit matches the run's distance unit) "
            f"[{source_desc}]. This is a heuristic speed cutoff, NOT EthoVision's own Movement "
            "classification -- see metrics.py's module docstring. Override with --mobility-threshold "
            "if pct_time_mobile/bout counts don't match what you see in the video."
        )
        print(msg)
        run_info_lines.append(msg)
    else:
        run_info_lines.append(
            "mobility source: none (no mobility_state column and --mobility-threshold=none) -- "
            "pct_time_mobile/immobile and bout metrics will be blank"
        )

    if run_info_lines:
        (outdir / "run_info.txt").write_text("\n".join(run_info_lines) + "\n")

    print("--- whole trial ---")
    analyze(tracks_by_well, well_group, cfg, outdir)

    if periods_csv is not None:
        periods = load_periods(periods_csv)
        for period in periods:
            print(f"--- period: {period.name} ({period.intervals}) ---")
            period_tracks = {
                well: slice_to_period(track, period) for well, track in tracks_by_well.items()
            }
            empty_wells = [w for w, t in period_tracks.items() if t.empty]
            if empty_wells:
                print(f"  WARNING: no frames in this period for well(s): {', '.join(empty_wells)}")
            analyze(period_tracks, well_group, cfg, outdir / "periods" / period.name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--raw-dir", type=Path,
                      help="Folder of per-well EthoVision raw-data export files (.csv/.xlsx)")
    src.add_argument("--trk-dir", type=Path,
                      help="Folder of .trk/.btn files, one pair per arena")
    parser.add_argument("--plate-layout", type=Path,
                         default=Path(__file__).resolve().parents[1] / "config" / "plate_layout_24well.csv",
                         help="arena_index -> well_id CSV (default: config/plate_layout_24well.csv)")
    parser.add_argument("--groups", type=Path, required=True,
                         help="well_id -> group CSV (copy config/groups_template.csv and fill it in)")
    parser.add_argument("--outdir", type=Path, required=True, help="Output directory")
    parser.add_argument("--bin-size-s", type=float, default=60.0,
                         help="Time bin width (seconds) for activity-over-time plots")
    parser.add_argument("--scale", type=float,
                         help="[--trk-dir only] cm per pixel, from `ethovision_trk.py calibrate`. "
                              "Omit to keep raw pixel units.")
    parser.add_argument("--x0", type=float, default=0.0, help="[--trk-dir only] x offset in cm")
    parser.add_argument("--y0", type=float, default=0.0, help="[--trk-dir only] y offset in cm")
    parser.add_argument(
        "--mobility-threshold", type=str, default="auto",
        help="Only used when there's no EthoVision mobility_state column (i.e. --trk-dir "
             "without a raw-data export). 'auto' (default): pick the "
             "--mobility-percentile of pooled smoothed velocity across every well in this "
             "run. 'none': leave mobility/bout metrics blank. Or a specific number in the "
             "run's distance unit per second (px/s if uncalibrated, mm/s if --scale given). "
             "This is a heuristic speed cutoff, not EthoVision's own classification -- see "
             "metrics.py's docstring."
    )
    parser.add_argument("--mobility-percentile", type=float, default=25.0,
                         help="Percentile of pooled smoothed velocity used as the 'auto' "
                              "mobility threshold (default: 25, i.e. bottom quartile = immobile)")
    parser.add_argument("--mobility-smoothing-window-s", type=float, default=0.5,
                         help="Rolling-mean window (seconds) applied to velocity before "
                              "thresholding for mobility, to suppress frame-level detector jitter")
    parser.add_argument(
        "--periods", type=Path, default=None,
        help="Optional CSV of named protocol time-windows (period,start_s,end_s -- see "
             "config/periods_template.csv) to additionally analyze separately, e.g. light/dark "
             "phases. Each period gets its own subfolder under <outdir>/periods/<name>/ with "
             "the same per_well_metrics.csv / group_summary.csv / group_comparisons.csv / plots "
             "as the whole-trial output. The whole-trial analysis always runs regardless."
    )
    args = parser.parse_args()
    run(args.raw_dir, args.trk_dir, args.plate_layout, args.groups, args.outdir,
        args.bin_size_s, args.scale, args.x0, args.y0,
        args.mobility_threshold, args.mobility_percentile, args.mobility_smoothing_window_s,
        args.periods)


if __name__ == "__main__":
    main()
