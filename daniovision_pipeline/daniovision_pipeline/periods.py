"""Named protocol time-windows, analyzed by slicing a track and reusing metrics.py.

A period is a name plus one or more [start_s, end_s) time windows, loaded
from a simple CSV (see config/periods_template.csv):

    period,start_s,end_s
    light,0,600
    dark,600,3000

This covers sustained-phase protocols (light/dark, baseline/drug) directly.
For repeated brief-stimulus protocols (e.g. acoustic startle pulses), list
several short windows under the same period name to pool them -- but note
the caveat below before trusting bout metrics from a multi-window period.

Deliberately simple by design (see metrics.py's unit-agnostic approach for
the same philosophy): a period is just a time filter, so it works
identically whether the track came from --raw-dir or --trk-dir, calibrated
or not, with or without a mobility threshold -- nothing period-specific
needs to know about any of that.

Caveat for multi-window periods: distance/velocity/thigmotaxis are sums/
means over the selected frames, which pool correctly across disjoint
windows. Bout metrics (mobile_bout_count, mean_*_bout_duration_s) are
computed by metrics.py's contiguous-run detector over the concatenated
selection, which does not know the windows are non-adjacent in real time --
a bout that happens to be "on" at the end of one window and the start of
the next gets merged into one long bout. For a single contiguous window
(the common case, e.g. light/dark) this is exact; for several well-separated
windows, treat bout metrics with caution (distance/velocity/thigmotaxis are
unaffected).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class Period:
    name: str
    intervals: list[tuple[float, float]]  # [(start_s, end_s), ...], end exclusive


def load_periods(path: str | Path) -> list[Period]:
    df = pd.read_csv(path)
    required = {"period", "start_s", "end_s"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Periods CSV {path} is missing columns: {missing}")
    periods = []
    for name, sub in df.groupby("period", sort=False):
        intervals = list(zip(sub["start_s"].astype(float), sub["end_s"].astype(float)))
        for start, end in intervals:
            if end <= start:
                raise ValueError(f"Periods CSV {path}: period '{name}' has end_s <= start_s ({start}, {end})")
        periods.append(Period(name=str(name), intervals=intervals))
    return periods


def slice_to_period(track: pd.DataFrame, period: Period) -> pd.DataFrame:
    """Rows of `track` whose time_s falls in any of the period's windows."""
    mask = pd.Series(False, index=track.index)
    for start, end in period.intervals:
        mask |= (track["time_s"] >= start) & (track["time_s"] < end)
    return track.loc[mask].reset_index(drop=True)
