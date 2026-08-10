"""Group-level summaries and between-group statistical tests for each metric."""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

try:
    from statsmodels.stats.multicomp import pairwise_tukeyhsd

    _HAVE_STATSMODELS = True
except ImportError:  # pragma: no cover - statsmodels is an optional dependency
    _HAVE_STATSMODELS = False

NON_METRIC_COLUMNS = {"well_id", "group", "plate_id", "date"}


def metric_columns(summary_df: pd.DataFrame) -> list[str]:
    """Numeric, non-identifier columns -- i.e. actual metrics.

    Filtering by dtype (not just the NON_METRIC_COLUMNS name list) means a
    new identifier/provenance column added later (e.g. aggregate.py's
    plate_id, date) can't accidentally get treated as a metric and crash
    group_summary/compare_groups trying to convert it to float.
    """
    return [
        c for c in summary_df.columns
        if c not in NON_METRIC_COLUMNS and pd.api.types.is_numeric_dtype(summary_df[c])
    ]


def group_summary(summary_df: pd.DataFrame) -> pd.DataFrame:
    """Mean, SEM, and N per group for every metric (long format)."""
    rows = []
    for metric in metric_columns(summary_df):
        for group, sub in summary_df.groupby("group"):
            values = sub[metric].dropna().to_numpy(dtype=float)
            n = len(values)
            rows.append(
                {
                    "metric": metric,
                    "group": group,
                    "n": n,
                    "mean": float(np.mean(values)) if n else np.nan,
                    "sem": float(np.std(values, ddof=1) / np.sqrt(n)) if n > 1 else np.nan,
                }
            )
    return pd.DataFrame(rows)


def _bonferroni_pairwise_ttests(values_by_group: dict[str, np.ndarray]) -> list[dict]:
    group_names = list(values_by_group)
    pairs = list(combinations(group_names, 2))
    n_pairs = len(pairs) or 1
    rows = []
    for g1, g2 in pairs:
        v1, v2 = values_by_group[g1], values_by_group[g2]
        if len(v1) < 2 or len(v2) < 2:
            p = np.nan
        else:
            _, p = sp_stats.ttest_ind(v1, v2, equal_var=False, nan_policy="omit")
        rows.append(
            {
                "group_1": g1,
                "group_2": g2,
                "p_value": p,
                "p_value_bonferroni": min(p * n_pairs, 1.0) if pd.notna(p) else np.nan,
            }
        )
    return rows


def compare_groups(summary_df: pd.DataFrame) -> pd.DataFrame:
    """For every metric: an omnibus test across groups, plus pairwise comparisons.

    Two groups -> Welch's t-test. More than two groups -> one-way ANOVA, with
    pairwise comparisons via Tukey HSD (if statsmodels is installed) or
    Bonferroni-corrected Welch's t-tests otherwise.
    """
    results = []
    for metric in metric_columns(summary_df):
        sub = summary_df[["group", metric]].dropna()
        values_by_group = {g: v[metric].to_numpy(dtype=float) for g, v in sub.groupby("group")}
        groups_with_data = {g: v for g, v in values_by_group.items() if len(v) > 0}
        if len(groups_with_data) < 2:
            continue

        if len(groups_with_data) == 2:
            (g1, v1), (g2, v2) = groups_with_data.items()
            omnibus_p = (
                sp_stats.ttest_ind(v1, v2, equal_var=False, nan_policy="omit").pvalue
                if len(v1) > 1 and len(v2) > 1
                else np.nan
            )
            test_name = "welch_ttest"
            pairwise = _bonferroni_pairwise_ttests(groups_with_data)
        else:
            omnibus_p = sp_stats.f_oneway(*groups_with_data.values()).pvalue
            test_name = "one_way_anova"
            if _HAVE_STATSMODELS:
                flat_values = np.concatenate(list(groups_with_data.values()))
                flat_groups = np.concatenate(
                    [[g] * len(v) for g, v in groups_with_data.items()]
                )
                tukey = pairwise_tukeyhsd(flat_values, flat_groups)
                pairwise = [
                    {
                        "group_1": row[0],
                        "group_2": row[1],
                        "p_value": row[3],
                        "p_value_bonferroni": row[3],  # Tukey already corrects for multiple comparisons
                    }
                    for row in tukey._results_table.data[1:]
                ]
            else:
                pairwise = _bonferroni_pairwise_ttests(groups_with_data)

        for pw in pairwise:
            results.append(
                {
                    "metric": metric,
                    "omnibus_test": test_name,
                    "omnibus_p_value": omnibus_p,
                    **pw,
                }
            )

    out = pd.DataFrame(results)
    if not out.empty:
        out = out.sort_values("p_value", na_position="last").reset_index(drop=True)
    return out
