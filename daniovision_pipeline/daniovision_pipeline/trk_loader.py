"""Turn an ethovision_trk.py TrkFile into the tidy per-frame DataFrame metrics.py expects.

This is the connector between direct .trk/.btn reading and the rest of the
metrics/stats/plots pipeline (which was originally built around
raw_export_parser.py's EthoVision-export DataFrames). Prefer pointing this
at a FilteredTrackFile*.btn over its paired Track_file*.trk when you have
both -- see ethovision_trk.py's module docstring and the README for why
(it's EthoVision's own smoothed, gap-filled trajectory, not an
approximation of it).

Without a --scale (from ethovision_trk.py's calibrate/validate against a
reference EthoVision export), positions stay in raw image pixels and the
output columns are named x_px/y_px/distance_px/velocity_pxs -- never x_mm
etc -- so metrics.py's unit-autodetection reports totals as
total_distance_px, not total_distance_mm. Do not rename these columns to
"_mm" without actually calibrating; see metrics.py's module docstring.

There is no "Movement (Moving/Not moving)" classification available in the
.trk/.btn record schema (EthoVision computes that from the smoothed track
internally, using detection-settings-specific thresholds we don't have
access to), so tracks loaded here never get a `mobility_state` column --
pct_time_mobile/immobile and bout metrics will read NaN. Distance,
velocity, and thigmotaxis are unaffected.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .ethovision_trk import TrkFile, read_trk


def load_trk_track(
    path: str | Path,
    scale: float | None = None,
    x0: float = 0.0,
    y0: float = 0.0,
) -> pd.DataFrame:
    """Load one .trk or .btn file into a tidy per-frame DataFrame.

    Pass `scale` (cm-per-pixel, from ethovision_trk.py calibrate) plus `x0`/
    `y0` to get calibrated x_mm/y_mm/distance_mm/velocity_mms columns
    (cm->mm handled here); omit it to get raw x_px/y_px/distance_px/
    velocity_pxs columns instead.
    """
    trk = read_trk(str(path))
    return _trk_to_frame(trk, scale, x0, y0)


def _trk_to_frame(trk: TrkFile, scale: float | None, x0: float, y0: float) -> pd.DataFrame:
    r = trk.records
    ok = ~trk.missing

    if scale is not None:
        unit = "mm"
        x = scale * 10.0 * r["x"].astype(float) + x0 * 10.0
        y = -scale * 10.0 * r["y"].astype(float) + y0 * 10.0
    else:
        unit = "px"
        x = r["x"].astype(float)
        y = r["y"].astype(float)

    x = np.where(ok, x, np.nan)
    y = np.where(ok, y, np.nan)

    dx = np.diff(x, prepend=np.nan)
    dy = np.diff(y, prepend=np.nan)
    distance = np.hypot(dx, dy)
    distance[0] = np.nan  # no "previous frame" for the first sample
    velocity = distance * trk.sample_rate

    return pd.DataFrame(
        {
            "time_s": trk.time_s,
            f"x_{unit}": x,
            f"y_{unit}": y,
            f"distance_{unit}": distance,
            f"velocity_{unit}s": velocity,
        }
    )
