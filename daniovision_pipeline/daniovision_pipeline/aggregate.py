"""Aggregate multiple plates'/days' already-computed pipeline outputs.

Each plate keeps running through cli.py entirely independently -- its own
well -> group resolution, its own metrics, its own QC. This module doesn't
re-read raw .trk/.btn/export files at all; it only combines what cli.py
already wrote (per_well_metrics.csv, per_well_time_binned_activity.csv, and
the same under periods/<name>/ if periods were used), tagged with a
plate_id (and optional date) from a manifest CSV, so group comparisons and
the activity-over-time trace pool wells across plates instead of being
scoped to one plate at a time.

IMPORTANT CAVEAT if any plate used uncalibrated .trk/.btn pixel units
(total_distance_px etc., no --scale): pixel counts are only comparable
across plates if every plate was recorded with the IDENTICAL camera
position/zoom/resolution. If the rig could have moved or been reconfigured
between recording days, calibrate every plate to physical units first
(ethovision_trk.py calibrate + cli.py --scale) before aggregating --
otherwise this would silently average numbers that don't mean the same
physical distance. aggregate() checks for and warns about this.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import plots as plots_mod
from . import stats as stats_mod
from .periods import Period, load_periods


@dataclass
class PlateRun:
    plate_id: str
    outdir: Path
    date: str | None = None


def load_manifest(path: str | Path) -> list[PlateRun]:
    df = pd.read_csv(path)
    required = {"plate_id", "outdir"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Manifest {path} is missing columns: {missing}")
    if df["plate_id"].duplicated().any():
        dupes = df.loc[df["plate_id"].duplicated(), "plate_id"].tolist()
        raise ValueError(f"Manifest {path} has duplicate plate_id(s): {dupes}")
    runs = []
    for _, row in df.iterrows():
        date = str(row["date"]).strip() if "date" in df.columns and pd.notna(row.get("date")) else None
        runs.append(PlateRun(plate_id=str(row["plate_id"]).strip(), outdir=Path(row["outdir"]), date=date))
    return runs


def _sort_key(run: PlateRun) -> str:
    """Sortable label -- date-prefixed (if given) so alphabetical sort is chronological."""
    return f"{run.date}_{run.plate_id}" if run.date else run.plate_id


def _load_one(run: PlateRun, subdir: str = "") -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    base = run.outdir / subdir if subdir else run.outdir
    summary_df = None
    summary_path = base / "per_well_metrics.csv"
    if summary_path.exists():
        summary_df = pd.read_csv(summary_path)
        # Force "group" to string: cli.py's well_mapping.load_groups() already does this
        # on the original groups CSV, but round-tripping through per_well_metrics.csv
        # loses it -- a purely-numeric-looking group label like "1" gets re-inferred as
        # int64 by pd.read_csv. One plate labeled "1"/"2" and another "control"/"treated"
        # would then combine into an int+str mixed column that crashes sorted() in plots.py.
        summary_df["group"] = summary_df["group"].astype(str)
        summary_df.insert(0, "plate_id", run.plate_id)
        summary_df.insert(1, "date", run.date)

    binned_df = None
    binned_path = base / "per_well_time_binned_activity.csv"
    if binned_path.exists():
        binned_df = pd.read_csv(binned_path)
        if not binned_df.empty:
            binned_df["group"] = binned_df["group"].astype(str)
            binned_df.insert(0, "plate_id", run.plate_id)
            binned_df.insert(1, "date", run.date)

    return summary_df, binned_df


def _check_unit_consistency(combined_summary: pd.DataFrame) -> list[str]:
    """Flag mixed px/mm across plates (not comparable), or all-px (needs same camera setup)."""
    warnings = []
    dist_cols = [c for c in combined_summary.columns if c.startswith("total_distance_")]
    units_present = {
        c.removeprefix("total_distance_") for c in dist_cols if combined_summary[c].notna().any()
    }
    if len(units_present) > 1:
        warnings.append(
            f"Plates mix distance units {sorted(units_present)} -- these are NOT comparable "
            "(uncalibrated pixels from one plate vs. calibrated mm from another). Recalibrate "
            "every plate to the same unit before aggregating."
        )
    elif units_present == {"px"}:
        warnings.append(
            "Distances are uncalibrated pixels (total_distance_px). Pixel counts are only "
            "comparable across plates if EVERY plate was recorded with the identical camera "
            "position/zoom/resolution -- if the rig could have moved or been reconfigured "
            "between recording days, calibrate every plate to physical units first "
            "(ethovision_trk.py calibrate + cli.py --scale) before trusting this aggregate."
        )
    return warnings


def _by_plate_labels(df: pd.DataFrame, runs_by_id: dict[str, PlateRun]) -> pd.DataFrame:
    """Relabel `group` as 'group | plate' (chronological if dates were given).

    A diagnostic view of plate-to-plate spread within each group -- NOT the
    main pooled output. Tight clustering of the same group's plates here
    means pooling is probably safe; wide spread is a batch effect to
    investigate before trusting the pooled comparison.
    """
    out = df.copy()
    sort_key_by_plate = {pid: _sort_key(run) for pid, run in runs_by_id.items()}
    out["group"] = out["group"].astype(str) + " | " + out["plate_id"].map(sort_key_by_plate)
    return out


def aggregate_one(
    summaries: list[pd.DataFrame],
    binneds: list[pd.DataFrame | None],
    outdir: Path,
    runs_by_id: dict[str, PlateRun],
    period_markers: list[Period] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Combine one set of per-plate (summary, binned) pairs into pooled outputs.

    Shared by the whole-trial aggregate and each period's -- a period is
    just a different (smaller) set of per-plate files, everything
    downstream of loading them is identical. Writes the same file shapes
    cli.py's analyze() does, prefixed `aggregated_` to distinguish from a
    single plate's own files.
    """
    outdir.mkdir(parents=True, exist_ok=True)

    combined_summary = pd.concat(summaries, ignore_index=True)
    combined_summary.to_csv(outdir / "aggregated_per_well_metrics.csv", index=False)

    group_summary_df = stats_mod.group_summary(combined_summary)
    group_summary_df.to_csv(outdir / "aggregated_group_summary.csv", index=False)
    comparisons_df = stats_mod.compare_groups(combined_summary)
    comparisons_df.to_csv(outdir / "aggregated_group_comparisons.csv", index=False)
    print(f"Wrote aggregated per-well metrics and group stats -> {outdir}")

    plot_dir = outdir / "plots"
    plots_mod.plot_all_metric_boxplots(combined_summary, plot_dir)
    plots_mod.plot_all_metric_boxplots(_by_plate_labels(combined_summary, runs_by_id), plot_dir / "by_plate")

    real_binned = [b for b in binneds if b is not None and not b.empty]
    combined_binned = pd.concat(real_binned, ignore_index=True) if real_binned else pd.DataFrame()
    if not combined_binned.empty:
        combined_binned.to_csv(outdir / "aggregated_per_well_time_binned_activity.csv", index=False)
        plots_mod.plot_binned_activity(
            combined_binned, plot_dir / "activity_over_time.svg", periods=period_markers
        )
        plots_mod.plot_binned_activity(
            _by_plate_labels(combined_binned, runs_by_id), plot_dir / "activity_over_time_by_plate.svg"
        )
    print(f"Wrote plots (incl. by_plate/ batch-effect diagnostics) -> {plot_dir}")

    return combined_summary, group_summary_df, comparisons_df


def discover_period_names(runs: list[PlateRun]) -> dict[str, list[PlateRun]]:
    """Which period subfolders exist across the manifested plates, and on which ones."""
    by_name: dict[str, list[PlateRun]] = {}
    for run in runs:
        periods_dir = run.outdir / "periods"
        if not periods_dir.is_dir():
            continue
        for p in sorted(periods_dir.iterdir()):
            if p.is_dir():
                by_name.setdefault(p.name, []).append(run)
    return by_name


def aggregate(manifest_csv: str | Path, outdir: Path, periods_csv: str | Path | None = None) -> None:
    runs = load_manifest(manifest_csv)
    runs_by_id = {r.plate_id: r for r in runs}
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    summaries, binneds = [], []
    for run in runs:
        s, b = _load_one(run)
        if s is None:
            print(f"WARNING: {run.plate_id}: no per_well_metrics.csv in {run.outdir} -- skipped "
                  "(run cli.py for this plate first)")
            continue
        summaries.append(s)
        binneds.append(b)
    if not summaries:
        raise FileNotFoundError(
            "No plate in the manifest had a per_well_metrics.csv to aggregate -- "
            "run cli.py for each plate first, pointing --outdir at what the manifest lists."
        )

    period_markers = load_periods(periods_csv) if periods_csv else None

    print(f"--- whole trial: aggregating {len(summaries)} plate(s) ---")
    combined_summary, _, _ = aggregate_one(summaries, binneds, outdir, runs_by_id, period_markers)

    unit_warnings = _check_unit_consistency(combined_summary)
    for w in unit_warnings:
        print(f"WARNING: {w}")
    (outdir / "aggregate_run_info.txt").write_text(
        "plates aggregated:\n"
        + "\n".join(f"  {r.plate_id}" + (f"  ({r.date})" if r.date else "") + f"  <- {r.outdir}" for r in runs)
        + "\n\n" + "\n".join(unit_warnings) + "\n"
    )

    period_names = discover_period_names(runs)
    for name, runs_with_period in period_names.items():
        print(f"--- period: {name}: aggregating {len(runs_with_period)} plate(s) ---")
        missing = [r.plate_id for r in runs if r not in runs_with_period]
        if missing:
            print(f"  NOTE: plate(s) without this period, excluded from this period's aggregate: "
                  f"{', '.join(missing)}")
        s_list, b_list = [], []
        for run in runs_with_period:
            s, b = _load_one(run, subdir=f"periods/{name}")
            if s is not None:
                s_list.append(s)
                b_list.append(b)
        if s_list:
            aggregate_one(s_list, b_list, outdir / "periods" / name, runs_by_id)
