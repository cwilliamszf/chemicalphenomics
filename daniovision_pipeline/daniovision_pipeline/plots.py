"""Plots comparing groups: per-metric boxplots and time-binned activity traces."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .stats import metric_columns


def plot_metric_boxplot(summary_df: pd.DataFrame, metric: str, outpath: str | Path) -> None:
    groups = sorted(summary_df["group"].dropna().unique())
    data = [summary_df.loc[summary_df["group"] == g, metric].dropna().to_numpy() for g in groups]

    fig, ax = plt.subplots(figsize=(1.2 * len(groups) + 2, 4))
    try:
        ax.boxplot(data, tick_labels=groups, showfliers=False)  # matplotlib >= 3.9
    except TypeError:
        ax.boxplot(data, labels=groups, showfliers=False)  # matplotlib < 3.9
    rng = np.random.default_rng(0)
    for i, values in enumerate(data, start=1):
        jitter = rng.uniform(-0.08, 0.08, size=len(values))
        ax.scatter(np.full(len(values), i) + jitter, values, alpha=0.7, s=20, color="black")
    ax.set_ylabel(metric)
    ax.set_title(metric)
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)


def plot_all_metric_boxplots(summary_df: pd.DataFrame, outdir: str | Path) -> list[Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for metric in metric_columns(summary_df):
        if not pd.api.types.is_numeric_dtype(summary_df[metric]):
            continue
        outpath = outdir / f"{metric}.png"
        plot_metric_boxplot(summary_df, metric, outpath)
        paths.append(outpath)
    return paths


def plot_binned_activity(binned_df: pd.DataFrame, outpath: str | Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 4))
    for group, sub in binned_df.groupby("group"):
        agg = sub.groupby("bin_start_s")["distance_mm"].agg(["mean", "sem"])
        ax.plot(agg.index / 60.0, agg["mean"], label=str(group))
        ax.fill_between(
            agg.index / 60.0,
            agg["mean"] - agg["sem"].fillna(0),
            agg["mean"] + agg["sem"].fillna(0),
            alpha=0.2,
        )
    ax.set_xlabel("Time (min)")
    ax.set_ylabel("Distance moved per bin (mm)")
    ax.set_title("Activity over time by group")
    ax.legend()
    fig.tight_layout()
    fig.savefig(outpath, dpi=150)
    plt.close(fig)
