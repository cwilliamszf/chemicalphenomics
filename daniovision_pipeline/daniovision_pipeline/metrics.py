"""Per-well behavioral metrics computed from a tidy raw-track DataFrame.

Metric choices mirror the categories used by other larval zebrafish
locomotor pipelines (e.g. sthyme/ZebrafishBehavior's bout/activity metrics
and light-dark designs): total movement, velocity, mobile/immobile
("freezing") bouts, thigmotaxis (center vs. edge), and time-binned activity
so light/dark or other stimulus phases can be compared.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MOBILE_TOKENS = {"moving", "mobile", "active"}
IMMOBILE_TOKENS = {"not moving", "immobile", "inactive", "not_moving"}


@dataclass
class MetricsConfig:
    bin_size_s: float = 60.0          # time-binned activity bin width
    well_center_xy: tuple[float, float] | None = None   # mm; None -> use track centroid
    well_radius_mm: float | None = None                 # mm; None -> use max observed radius
    outer_zone_fraction: float = 0.45  # fraction of radius considered "outer/edge" (thigmotaxis)


def _mobility_bool(series: pd.Series) -> pd.Series:
    lowered = series.astype(str).str.strip().str.lower()
    is_mobile = lowered.isin(MOBILE_TOKENS)
    is_immobile = lowered.isin(IMMOBILE_TOKENS)
    out = pd.Series(pd.NA, index=series.index, dtype="object")
    out[is_mobile] = True
    out[is_immobile] = False
    return out


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


def _thigmotaxis(track: pd.DataFrame, cfg: MetricsConfig) -> dict[str, float]:
    if "x_mm" not in track or "y_mm" not in track:
        return {"pct_time_in_outer_zone": np.nan, "pct_distance_in_outer_zone": np.nan}
    x = track["x_mm"].to_numpy()
    y = track["y_mm"].to_numpy()
    valid = ~np.isnan(x) & ~np.isnan(y)
    if valid.sum() < 2:
        return {"pct_time_in_outer_zone": np.nan, "pct_distance_in_outer_zone": np.nan}
    cx, cy = cfg.well_center_xy if cfg.well_center_xy else (np.nanmean(x), np.nanmean(y))
    r = np.hypot(x - cx, y - cy)
    max_r = cfg.well_radius_mm if cfg.well_radius_mm else np.nanmax(r)
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

    duration_s = float(track["time_s"].max() - track["time_s"].min()) if len(track) else np.nan
    out["duration_s"] = duration_s
    out["n_frames"] = len(track)

    if "distance_mm" in track:
        out["total_distance_mm"] = float(np.nansum(track["distance_mm"]))
    elif {"x_mm", "y_mm"} <= set(track.columns):
        dx = np.diff(track["x_mm"].to_numpy())
        dy = np.diff(track["y_mm"].to_numpy())
        out["total_distance_mm"] = float(np.nansum(np.hypot(dx, dy)))
    else:
        out["total_distance_mm"] = np.nan

    if "velocity_mms" in track:
        out["mean_velocity_mms"] = float(np.nanmean(track["velocity_mms"]))
        out["max_velocity_mms"] = float(np.nanmax(track["velocity_mms"]))
    else:
        out["mean_velocity_mms"] = np.nan
        out["max_velocity_mms"] = np.nan

    if "mobility_state" in track:
        mobile_bool = _mobility_bool(track["mobility_state"])
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

    out.update(_thigmotaxis(track, cfg))

    if "light_state" in track:
        for phase, sub in track.groupby("light_state"):
            phase_key = str(phase).strip().lower().replace(" ", "_")
            if "distance_mm" in sub:
                out[f"distance_mm__{phase_key}"] = float(np.nansum(sub["distance_mm"]))
            if "velocity_mms" in sub:
                out[f"mean_velocity_mms__{phase_key}"] = float(np.nanmean(sub["velocity_mms"]))

    return out


def compute_time_binned_activity(track: pd.DataFrame, cfg: MetricsConfig | None = None) -> pd.DataFrame:
    """Distance moved per fixed-width time bin, for activity-over-time plots."""
    cfg = cfg or MetricsConfig()
    if "time_s" not in track or track.empty:
        return pd.DataFrame(columns=["bin_start_s", "distance_mm"])
    dist_col = "distance_mm"
    if dist_col not in track:
        if {"x_mm", "y_mm"} <= set(track.columns):
            track = track.copy()
            dx = np.diff(track["x_mm"].to_numpy(), prepend=np.nan)
            dy = np.diff(track["y_mm"].to_numpy(), prepend=np.nan)
            track[dist_col] = np.hypot(dx, dy)
        else:
            return pd.DataFrame(columns=["bin_start_s", "distance_mm"])
    bins = (track["time_s"] // cfg.bin_size_s) * cfg.bin_size_s
    binned = track.assign(bin_start_s=bins).groupby("bin_start_s", as_index=False)[dist_col].sum()
    binned = binned.rename(columns={dist_col: "distance_mm"})
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
