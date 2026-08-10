"""Per-well behavioral metrics computed from a tidy raw-track DataFrame.

Metric choices mirror the categories used by other larval zebrafish
locomotor pipelines (e.g. sthyme/ZebrafishBehavior's bout/activity metrics
and light-dark designs): total movement, velocity, mobile/immobile
("freezing") bouts, thigmotaxis (center vs. edge), and time-binned activity
so light/dark or other stimulus phases can be compared.

Unit-agnostic by design: a track can carry calibrated positions
(x_mm/y_mm/distance_mm/velocity_mms, from raw_export_parser.py or a
calibrated ethovision_trk.py convert) or raw pixel positions
(x_px/y_px/distance_px/velocity_pxs, from an uncalibrated
ethovision_trk.py convert). Output metric names carry whichever unit the
input used (e.g. total_distance_mm vs total_distance_px) so pixel-derived
numbers never get silently mislabeled as physical units.

Mobility/bout metrics (pct_time_mobile, mobile_bout_count, etc.) come from
one of two sources:

1. An EthoVision-derived `mobility_state` text column (from
   raw_export_parser.py's export reading) -- EthoVision's own Movement
   classification, using whatever Detection Settings the project used.
2. A velocity threshold applied to this module's own frame-to-frame
   velocity (used automatically when no `mobility_state` column exists,
   e.g. for .trk/.btn-derived tracks, and `MetricsConfig.mobility_velocity_threshold`
   is set). This is NOT EthoVision's algorithm -- there's no Movement
   classification in the .trk/.btn record schema to recover -- it's a
   straightforward "is smoothed speed above X" rule. Raw frame-to-frame
   velocity is too noisy to threshold directly (produces thousands of
   1-4-frame flicker "bouts" from detector jitter); it's smoothed with a
   rolling mean over `mobility_smoothing_window_s` first. Pick the
   threshold from the SAME pooled distribution across every well in a run
   (see compute_pooled_mobility_threshold() below) -- a per-well threshold
   would adapt away exactly the between-well activity differences you're
   trying to measure.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MOBILE_TOKENS = {"moving", "mobile", "active"}
IMMOBILE_TOKENS = {"not moving", "immobile", "inactive", "not_moving"}
LENGTH_UNITS = ("mm", "px")  # checked in this order


@dataclass
class MetricsConfig:
    bin_size_s: float = 60.0          # time-binned activity bin width
    well_center_xy: tuple[float, float] | None = None   # same unit as track positions; None -> use track centroid
    well_radius: float | None = None                    # same unit as track positions; None -> use max observed radius
    outer_zone_fraction: float = 0.45  # fraction of radius considered "outer/edge" (thigmotaxis)
    mobility_velocity_threshold: float | None = None  # same unit/s as track velocity; None -> no velocity-derived mobility
    mobility_smoothing_window_s: float = 0.5  # rolling-mean window applied to velocity before thresholding


def _length_unit(track: pd.DataFrame) -> str | None:
    """Which length unit this track's columns are in ('mm', 'px', or None)."""
    for unit in LENGTH_UNITS:
        if f"x_{unit}" in track.columns or f"distance_{unit}" in track.columns:
            return unit
    return None


def _mobility_bool(series: pd.Series) -> pd.Series:
    lowered = series.astype(str).str.strip().str.lower()
    is_mobile = lowered.isin(MOBILE_TOKENS)
    is_immobile = lowered.isin(IMMOBILE_TOKENS)
    out = pd.Series(pd.NA, index=series.index, dtype="object")
    out[is_mobile] = True
    out[is_immobile] = False
    return out


def _smoothed_velocity(track: pd.DataFrame, unit: str, window_s: float) -> pd.Series:
    """Rolling-mean velocity, for thresholding rather than reporting."""
    vel_col = f"velocity_{unit}s"
    v = track[vel_col]
    dt = track["time_s"].diff().median()
    window = max(1, int(round(window_s / dt))) if dt and np.isfinite(dt) and dt > 0 else 1
    return v.rolling(window, center=True, min_periods=1).mean()


def _velocity_mobility_bool(track: pd.DataFrame, unit: str, cfg: MetricsConfig) -> pd.Series | None:
    vel_col = f"velocity_{unit}s"
    if cfg.mobility_velocity_threshold is None or vel_col not in track:
        return None
    v_smooth = _smoothed_velocity(track, unit, cfg.mobility_smoothing_window_s)
    out = pd.Series(pd.NA, index=track.index, dtype="object")
    valid = v_smooth.notna()
    out[valid] = v_smooth[valid] > cfg.mobility_velocity_threshold
    return out


def compute_pooled_mobility_threshold(
    tracks_by_well: dict[str, pd.DataFrame],
    percentile: float = 25.0,
    smoothing_window_s: float = 0.5,
) -> float | None:
    """A single velocity threshold shared across every well in a run.

    Pools smoothed velocity across all wells and returns its `percentile`-th
    value (default: the bottom quartile of smoothed speed is "immobile").
    This is a heuristic, not a recovered EthoVision setting -- inspect the
    resulting pct_time_mobile/bout numbers against a few videos and override
    with an explicit threshold (MetricsConfig.mobility_velocity_threshold)
    if it doesn't match what you see. Returns None if no track has a
    velocity column.
    """
    pooled = []
    units_seen = set()
    for track in tracks_by_well.values():
        unit = _length_unit(track)
        if unit is None:
            continue
        vel_col = f"velocity_{unit}s"
        if vel_col not in track:
            continue
        units_seen.add(unit)
        pooled.append(_smoothed_velocity(track, unit, smoothing_window_s).dropna().to_numpy())
    if not pooled:
        return None
    if len(units_seen) > 1:
        raise ValueError(
            f"tracks use mixed length units {units_seen} -- pooling their velocities "
            "into one threshold would compare pixels to millimeters. All wells in a "
            "run should come from the same input mode/calibration."
        )
    return float(np.percentile(np.concatenate(pooled), percentile))


def _bout_stats(mobile_bool: pd.Series, time_s: pd.Series) -> dict[str, float]:
    """Count contiguous True/False runs and their mean durations."""
    valid = mobile_bool.notna()
    m = mobile_bool[valid].astype(bool).to_numpy()
    t = time_s[valid].to_numpy()
    if len(m) < 2:
        return {
            "mobile_bout_count": np.nan,
            "mean_mobile_bout_duration_s": np.nan,
            "mean_immobile_bout_duration_s": np.nan,
        }
    change_points = np.where(np.diff(m.astype(int)) != 0)[0] + 1
    bounds = np.concatenate(([0], change_points, [len(m)]))
    mobile_durs, immobile_durs = [], []
    for start, end in zip(bounds[:-1], bounds[1:]):
        dur = t[end - 1] - t[start] if end > start else 0.0
        (mobile_durs if m[start] else immobile_durs).append(dur)
    mobile_bout_count = len(mobile_durs)
    return {
        "mobile_bout_count": mobile_bout_count,
        "mean_mobile_bout_duration_s": float(np.mean(mobile_durs)) if mobile_durs else np.nan,
        "mean_immobile_bout_duration_s": float(np.mean(immobile_durs)) if immobile_durs else np.nan,
    }


def _thigmotaxis(track: pd.DataFrame, cfg: MetricsConfig, unit: str | None) -> dict[str, float]:
    if unit is None or f"x_{unit}" not in track or f"y_{unit}" not in track:
        return {"pct_time_in_outer_zone": np.nan, "pct_distance_in_outer_zone": np.nan}
    x = track[f"x_{unit}"].to_numpy()
    y = track[f"y_{unit}"].to_numpy()
    valid = ~np.isnan(x) & ~np.isnan(y)
    if valid.sum() < 2:
        return {"pct_time_in_outer_zone": np.nan, "pct_distance_in_outer_zone": np.nan}
    cx, cy = cfg.well_center_xy if cfg.well_center_xy else (np.nanmean(x), np.nanmean(y))
    r = np.hypot(x - cx, y - cy)
    max_r = cfg.well_radius if cfg.well_radius else np.nanmax(r)
    if not max_r:
        return {"pct_time_in_outer_zone": np.nan, "pct_distance_in_outer_zone": np.nan}
    outer_threshold = max_r * (1 - cfg.outer_zone_fraction)
    in_outer = r >= outer_threshold
    pct_time_outer = 100.0 * np.nansum(in_outer[valid]) / valid.sum()

    step_dist = np.hypot(np.diff(x), np.diff(y))
    step_valid = valid[1:] & valid[:-1]
    step_in_outer = in_outer[1:] & step_valid
    total_dist = np.nansum(step_dist[step_valid])
    outer_dist = np.nansum(step_dist[step_in_outer])
    pct_dist_outer = 100.0 * outer_dist / total_dist if total_dist else np.nan
    return {"pct_time_in_outer_zone": pct_time_outer, "pct_distance_in_outer_zone": pct_dist_outer}


def compute_well_metrics(track: pd.DataFrame, cfg: MetricsConfig | None = None) -> dict[str, float]:
    """Compute one row of summary metrics for a single well's per-frame track."""
    cfg = cfg or MetricsConfig()
    out: dict[str, float] = {}
    unit = _length_unit(track)

    duration_s = float(track["time_s"].max() - track["time_s"].min()) if len(track) else np.nan
    out["duration_s"] = duration_s
    out["n_frames"] = len(track)

    if unit is None:
        out["total_distance"] = np.nan
        out["mean_velocity"] = np.nan
        out["max_velocity"] = np.nan
    else:
        dist_col, vel_col = f"distance_{unit}", f"velocity_{unit}s"
        if dist_col in track:
            out[f"total_distance_{unit}"] = float(np.nansum(track[dist_col]))
        else:
            dx = np.diff(track[f"x_{unit}"].to_numpy())
            dy = np.diff(track[f"y_{unit}"].to_numpy())
            out[f"total_distance_{unit}"] = float(np.nansum(np.hypot(dx, dy)))

        if vel_col in track:
            out[f"mean_velocity_{unit}s"] = float(np.nanmean(track[vel_col]))
            out[f"max_velocity_{unit}s"] = float(np.nanmax(track[vel_col]))
        else:
            out[f"mean_velocity_{unit}s"] = np.nan
            out[f"max_velocity_{unit}s"] = np.nan

    if "mobility_state" in track:
        mobile_bool = _mobility_bool(track["mobility_state"])
    elif unit is not None:
        mobile_bool = _velocity_mobility_bool(track, unit, cfg)
    else:
        mobile_bool = None

    if mobile_bool is not None:
        valid = mobile_bool.notna()
        if valid.any():
            out["pct_time_mobile"] = 100.0 * mobile_bool[valid].astype(bool).mean()
            out["pct_time_immobile"] = 100.0 - out["pct_time_mobile"]
        else:
            out["pct_time_mobile"] = np.nan
            out["pct_time_immobile"] = np.nan
        out.update(_bout_stats(mobile_bool, track["time_s"]))
    else:
        out["pct_time_mobile"] = np.nan
        out["pct_time_immobile"] = np.nan
        out.update(_bout_stats(pd.Series(dtype=float), pd.Series(dtype=float)))

    out.update(_thigmotaxis(track, cfg, unit))

    if "light_state" in track and unit is not None:
        dist_col, vel_col = f"distance_{unit}", f"velocity_{unit}s"
        for phase, sub in track.groupby("light_state"):
            phase_key = str(phase).strip().lower().replace(" ", "_")
            if dist_col in sub:
                out[f"{dist_col}__{phase_key}"] = float(np.nansum(sub[dist_col]))
            if vel_col in sub:
                out[f"mean_{vel_col}__{phase_key}"] = float(np.nanmean(sub[vel_col]))

    return out


def compute_time_binned_activity(track: pd.DataFrame, cfg: MetricsConfig | None = None) -> pd.DataFrame:
    """Distance moved per fixed-width time bin, for activity-over-time plots.

    Returns columns [bin_start_s, distance_<unit>] where <unit> is 'mm' or
    'px' depending on the input track (see _length_unit); empty (no rows) if
    the track has no time or position/distance data at all.
    """
    cfg = cfg or MetricsConfig()
    unit = _length_unit(track)
    if "time_s" not in track or track.empty or unit is None:
        return pd.DataFrame(columns=["bin_start_s", "distance_mm"])
    dist_col = f"distance_{unit}"
    if dist_col not in track:
        track = track.copy()
        dx = np.diff(track[f"x_{unit}"].to_numpy(), prepend=np.nan)
        dy = np.diff(track[f"y_{unit}"].to_numpy(), prepend=np.nan)
        track[dist_col] = np.hypot(dx, dy)
    bins = (track["time_s"] // cfg.bin_size_s) * cfg.bin_size_s
    binned = track.assign(bin_start_s=bins).groupby("bin_start_s", as_index=False)[dist_col].sum()
    return binned


def compute_all_wells(
    tracks_by_well: dict[str, pd.DataFrame],
    well_group: dict[str, str],
    cfg: MetricsConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute summary metrics and time-binned activity for every well.

    Returns (summary_df, binned_activity_df).
    """
    cfg = cfg or MetricsConfig()
    summary_rows = []
    binned_frames = []
    for well_id, track in tracks_by_well.items():
        row = {"well_id": well_id, "group": well_group.get(well_id)}
        row.update(compute_well_metrics(track, cfg))
        summary_rows.append(row)

        binned = compute_time_binned_activity(track, cfg)
        binned["well_id"] = well_id
        binned["group"] = well_group.get(well_id)
        binned_frames.append(binned)

    summary_df = pd.DataFrame(summary_rows)
    binned_df = pd.concat(binned_frames, ignore_index=True) if binned_frames else pd.DataFrame()
    return summary_df, binned_df
