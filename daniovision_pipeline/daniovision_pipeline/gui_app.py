"""Local GUI for the DanioVision pipeline: point at a folder, edit wells/groups
and periods in a spreadsheet-like table, click Run.

Launch it on your own machine (not in a remote/cloud session -- it needs to
see your local .trk/.btn folder):

    pip install -r requirements.txt
    streamlit run daniovision_pipeline/gui_app.py

This is a thin UI shell around cli.py -- every checkbox/table here maps
directly to a `python -m daniovision_pipeline.cli ...` flag, and clicking
Run calls the exact same `run()` function the CLI uses. Nothing about the
underlying analysis differs between the two; this just saves you from
hand-editing CSVs and remembering flag names.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import tempfile
import traceback
from pathlib import Path

import pandas as pd
import streamlit as st

from daniovision_pipeline import aggregate as aggregate_mod
from daniovision_pipeline import cli as cli_mod
from daniovision_pipeline.well_mapping import PlateLayout

try:
    import tkinter as _tk
    from tkinter import filedialog as _filedialog

    _TKINTER_AVAILABLE = True
except ImportError:
    _TKINTER_AVAILABLE = False

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
DEFAULT_PLATE_LAYOUT = CONFIG_DIR / "plate_layout_24well.csv"

st.set_page_config(page_title="DanioVision Analysis", layout="wide")
st.title("DanioVision analysis pipeline")
st.caption(
    "Runs the same pipeline as `python -m daniovision_pipeline.cli` -- this is just a "
    "friendlier way to fill in the folder, wells/groups, and periods."
)


def _browse_folder(label: str, key: str) -> str:
    """Text input plus an optional native folder-picker button (if tkinter works)."""
    col1, col2 = st.columns([5, 1])
    with col1:
        path = st.text_input(label, value=st.session_state.get(key, ""), key=f"{key}_text")
    with col2:
        st.write("")  # vertical spacer to align the button with the text input
        if _TKINTER_AVAILABLE and st.button("Browse...", key=f"{key}_browse"):
            root = _tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            chosen = _filedialog.askdirectory()
            root.destroy()
            if chosen:
                path = chosen
                st.session_state[f"{key}_text"] = chosen
                st.rerun()
    return path


def _default_groups_df() -> pd.DataFrame:
    layout = PlateLayout.from_csv(DEFAULT_PLATE_LAYOUT)
    df = layout.table[["well_id"]].copy()
    df["group"] = ""
    return df


def _default_periods_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "period": ["Light phase", "Startle", "Dark phase"],
            "start_min": [0.0, 10.0, 15.0],
            "end_min": [10.0, 15.0, 50.0],
        }
    )


# --------------------------------------------------------------------------
# 1. Data source
# --------------------------------------------------------------------------
st.header("1. Data")
input_mode = st.radio(
    "Input type",
    [".trk / .btn files (direct, no EthoVision export needed)", "EthoVision \"Raw data\" export (.csv/.xlsx)"],
    help="See the README for the tradeoffs. .trk/.btn is uncalibrated pixels unless you "
         "provide a calibration below; the EthoVision export is already calibrated/smoothed "
         "but requires exporting every well by hand first.",
)
is_trk_mode = input_mode.startswith(".trk")
data_dir = _browse_folder(
    "Folder containing .trk/.btn files" if is_trk_mode else "Folder containing raw-data export files",
    key="data_dir",
)

if data_dir:
    data_path = Path(data_dir)
    if not data_path.is_dir():
        st.error(f"Not a folder: {data_dir}")
    else:
        try:
            found = (
                cli_mod.find_trk_files(data_path) if is_trk_mode else cli_mod.find_raw_files(data_path)
            )
            st.success(f"Found {len(found)} file(s) in {data_dir}")
        except (FileNotFoundError, ValueError) as e:
            st.warning(str(e))

with st.expander("Calibration (optional, .trk/.btn mode only)"):
    st.caption(
        "Leave blank to keep raw pixel units. Get these from "
        "`python daniovision_pipeline/ethovision_trk.py calibrate FILE.trk --export reference_export.xlsx`."
    )
    c1, c2, c3 = st.columns(3)
    scale = c1.number_input("Scale (cm/px)", value=0.0, format="%.6f", min_value=0.0)
    x0 = c2.number_input("X offset (cm)", value=0.0, format="%.4f")
    y0 = c3.number_input("Y offset (cm)", value=0.0, format="%.4f")

# --------------------------------------------------------------------------
# 2. Wells & groups
# --------------------------------------------------------------------------
st.header("2. Wells & groups")
if "groups_df" not in st.session_state:
    st.session_state.groups_df = _default_groups_df()

fill_col1, fill_col2, fill_col3 = st.columns([2, 1, 3])
bulk_group = fill_col1.text_input("Fill all wells with group:", key="bulk_group_value")
if fill_col2.button("Apply to all", key="apply_group_btn"):
    st.session_state.groups_df["group"] = bulk_group

groups_df = st.data_editor(
    st.session_state.groups_df,
    num_rows="fixed",
    key="groups_editor",
    width='stretch',
    height=250,
    column_config={
        "well_id": st.column_config.TextColumn("Well", disabled=True),
        "group": st.column_config.TextColumn("Group", help="e.g. control, treated, genotype name"),
    },
)
st.session_state.groups_df = groups_df

with st.expander("Plate layout (arena -> well; only edit if arenas weren't drawn row-major)"):
    st.caption(
        "Default is row-major for a 4-row x 6-column plate (arena 0 = A1, arena 1 = A2, "
        "... arena 23 = D6). Verify against your Trial Setup > Arena Settings screen -- see "
        "the README's 'filename -> well mapping problem' section."
    )
    if "plate_layout_df" not in st.session_state:
        st.session_state.plate_layout_df = pd.read_csv(DEFAULT_PLATE_LAYOUT)
    st.session_state.plate_layout_df = st.data_editor(
        st.session_state.plate_layout_df, num_rows="dynamic", key="plate_layout_editor", width='stretch'
    )

# --------------------------------------------------------------------------
# 3. Periods
# --------------------------------------------------------------------------
st.header("3. Periods (optional)")
st.caption(
    "Analyze named phases of the trial separately (e.g. light/dark/startle), in addition to "
    "the whole trial. Times are minutes from the start of the trial. Leave the table empty to "
    "skip period analysis and only get whole-trial results."
)
if "periods_df" not in st.session_state:
    st.session_state.periods_df = _default_periods_df()
periods_df = st.data_editor(
    st.session_state.periods_df,
    num_rows="dynamic",
    key="periods_editor",
    width='stretch',
    column_config={
        "period": st.column_config.TextColumn("Period name"),
        "start_min": st.column_config.NumberColumn("Start (min)", min_value=0.0, step=0.5),
        "end_min": st.column_config.NumberColumn("End (min)", min_value=0.0, step=0.5),
    },
)
st.session_state.periods_df = periods_df

# --------------------------------------------------------------------------
# 4. Options
# --------------------------------------------------------------------------
st.header("4. Options")
o1, o2 = st.columns(2)
bin_size_min = o1.number_input(
    "Activity plot bin size (min)", value=1.0, min_value=0.05, step=0.5, key="bin_size_min"
)
outdir = o2.text_input(
    "Output folder",
    value=st.session_state.get(
        "outdir_text", str(Path(data_dir).parent / "daniovision_results") if data_dir else ""
    ),
    key="outdir_text",
)

with st.expander("Mobility threshold (advanced)"):
    st.caption(
        "Only relevant for .trk/.btn input, which has no EthoVision Movement classification. "
        "'auto' derives a shared speed cutoff from this run's own data -- see the README."
    )
    mobility_mode = st.radio("Mode", ["auto", "none", "custom value"], horizontal=True)
    if mobility_mode == "custom value":
        mobility_threshold = str(st.number_input("Threshold (px/s or mm/s, matching your units)", value=1.0))
    else:
        mobility_threshold = mobility_mode
    m1, m2 = st.columns(2)
    mobility_percentile = m1.number_input("Auto percentile", value=25.0, min_value=0.0, max_value=100.0)
    mobility_smoothing_window_s = m2.number_input("Smoothing window (s)", value=0.5, min_value=0.0)

# --------------------------------------------------------------------------
# 5. Run
# --------------------------------------------------------------------------
st.header("5. Run")
run_clicked = st.button("Run analysis", type="primary", key="run_btn")

if run_clicked:
    errors = []
    if not data_dir or not Path(data_dir).is_dir():
        errors.append("Pick a valid data folder in step 1.")
    missing_groups = groups_df[groups_df["group"].astype(str).str.strip() == ""]
    if not missing_groups.empty:
        errors.append(
            f"{len(missing_groups)} well(s) have no group assigned: "
            f"{', '.join(missing_groups['well_id'])}. Fill in every well, or delete its row."
        )
    if not outdir:
        errors.append("Set an output folder in step 4.")

    clean_periods = periods_df.dropna(how="all")
    if not clean_periods.empty:
        bad = clean_periods[clean_periods["end_min"] <= clean_periods["start_min"]]
        if not bad.empty:
            errors.append(f"Period(s) with end <= start: {', '.join(bad['period'].astype(str))}")

    if errors:
        for e in errors:
            st.error(e)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            groups_csv = tmp / "groups.csv"
            groups_df.assign(notes="").to_csv(groups_csv, index=False)

            plate_layout_csv = tmp / "plate_layout.csv"
            st.session_state.plate_layout_df.to_csv(plate_layout_csv, index=False)

            periods_csv = None
            if not clean_periods.empty:
                periods_csv = tmp / "periods.csv"
                pd.DataFrame(
                    {
                        "period": clean_periods["period"],
                        "start_s": clean_periods["start_min"] * 60.0,
                        "end_s": clean_periods["end_min"] * 60.0,
                    }
                ).to_csv(periods_csv, index=False)

            log = io.StringIO()
            outdir_path = Path(outdir)
            try:
                with st.spinner("Running... this can take a minute for a full plate."):
                    with contextlib.redirect_stdout(log):
                        cli_mod.run(
                            raw_dir=None if is_trk_mode else Path(data_dir),
                            trk_dir=Path(data_dir) if is_trk_mode else None,
                            plate_layout_csv=plate_layout_csv,
                            groups_csv=groups_csv,
                            outdir=outdir_path,
                            bin_size_s=bin_size_min * 60.0,
                            scale=scale or None,
                            x0=x0,
                            y0=y0,
                            mobility_threshold=mobility_threshold,
                            mobility_percentile=mobility_percentile,
                            mobility_smoothing_window_s=mobility_smoothing_window_s,
                            periods_csv=periods_csv,
                        )
                st.session_state.last_outdir = str(outdir_path)
                st.session_state.last_log = log.getvalue()
                st.success(f"Done -> {outdir_path}")
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")
                with st.expander("Full error"):
                    st.code(traceback.format_exc())
                st.session_state.last_log = log.getvalue()

# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
if st.session_state.get("last_outdir"):
    outdir_path = Path(st.session_state["last_outdir"])
    if outdir_path.is_dir():
        st.header("Results")
        with st.expander("Run log"):
            st.code(st.session_state.get("last_log", ""))

        per_well_csv = outdir_path / "per_well_metrics.csv"
        if per_well_csv.exists():
            st.subheader("Per-well metrics (whole trial)")
            st.dataframe(pd.read_csv(per_well_csv), width='stretch')

        activity_svg = outdir_path / "plots" / "activity_over_time.svg"
        if activity_svg.exists():
            st.subheader("Activity over time")
            st.markdown(activity_svg.read_text(), unsafe_allow_html=True)

        zip_base = outdir_path.parent / (outdir_path.name + "_results")
        zip_path = shutil.make_archive(str(zip_base), "zip", root_dir=outdir_path)
        with open(zip_path, "rb") as f:
            st.download_button("Download all results (.zip)", f, file_name=Path(zip_path).name)


# --------------------------------------------------------------------------
# Aggregate across plates/days
# --------------------------------------------------------------------------
st.divider()
st.title("Aggregate across plates/days")
st.caption(
    "Pool wells of the same group/condition across multiple plates. **Every folder listed "
    "below must already be the output of a completed run** -- either `cli.py`, or the 'Run "
    "analysis' step above. This section does not read raw .trk/.btn/export files and will not "
    "process a plate for you; it only combines per_well_metrics.csv / "
    "per_well_time_binned_activity.csv (and periods/ subfolders, if used) that a prior run "
    "already wrote. Process each plate first, then come back here to pool them."
)


def _default_agg_df() -> pd.DataFrame:
    return pd.DataFrame({"plate_id": [], "outdir": [], "date": []}, dtype="object")


if "agg_plates_df" not in st.session_state:
    st.session_state.agg_plates_df = _default_agg_df()

st.subheader("Plates to aggregate")
add_col1, add_col2, add_col3, add_col4 = st.columns([4, 1, 2, 1])
with add_col1:
    new_plate_dir = st.text_input("Add a plate's already-processed output folder", key="agg_new_dir_text")
with add_col2:
    st.write("")
    if _TKINTER_AVAILABLE and st.button("Browse...", key="agg_new_dir_browse"):
        root = _tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = _filedialog.askdirectory()
        root.destroy()
        if chosen:
            st.session_state["agg_new_dir_text"] = chosen
            st.rerun()
with add_col3:
    new_plate_date = st.text_input("Date (optional)", key="agg_new_date_text", placeholder="2026-07-28")
with add_col4:
    st.write("")
    if st.button("Add plate", key="agg_add_btn"):
        folder = st.session_state.get("agg_new_dir_text", "").strip()
        if folder:
            new_row = pd.DataFrame(
                {
                    "plate_id": [Path(folder).name or folder],
                    "outdir": [folder],
                    "date": [st.session_state.get("agg_new_date_text", "").strip() or None],
                }
            )
            st.session_state.agg_plates_df = pd.concat(
                [st.session_state.agg_plates_df, new_row], ignore_index=True
            )
            st.session_state["agg_new_dir_text"] = ""
            st.session_state["agg_new_date_text"] = ""
            st.rerun()

st.caption(
    "Or type/paste directly into the table below (add/delete rows with the icons on the "
    "right) -- the button above is just a shortcut for browsing to a folder."
)
agg_plates_df = st.data_editor(
    st.session_state.agg_plates_df,
    num_rows="dynamic",
    key="agg_plates_editor",
    width="stretch",
    column_config={
        "plate_id": st.column_config.TextColumn("Plate ID", help="Must be unique across rows"),
        "outdir": st.column_config.TextColumn("Output folder (already processed)"),
        "date": st.column_config.TextColumn(
            "Date (optional)", help="Any format; used only for display/sort order, not computation"
        ),
    },
)
st.session_state.agg_plates_df = agg_plates_df

agg_col1, agg_col2 = st.columns(2)
agg_outdir = agg_col1.text_input("Aggregated output folder", key="agg_outdir_text")
agg_periods_path = agg_col2.text_input(
    "Periods CSV for boundary markers (optional)",
    key="agg_periods_text",
    help="Same shape as the Periods table above, as a CSV (period,start_s,end_s). Only draws "
         "boundary lines on the pooled whole-trial activity plot -- period-level aggregation "
         "itself is auto-discovered from each plate's own periods/<name>/ folders regardless.",
)

agg_run_clicked = st.button("Run aggregation", type="primary", key="agg_run_btn")

if agg_run_clicked:
    agg_errors = []
    clean_plates = agg_plates_df.dropna(subset=["plate_id", "outdir"])
    clean_plates = clean_plates[clean_plates["plate_id"].astype(str).str.strip() != ""]
    clean_plates = clean_plates[clean_plates["outdir"].astype(str).str.strip() != ""]
    if len(clean_plates) < 1:
        agg_errors.append("Add at least one plate's already-processed output folder.")
    if clean_plates["plate_id"].duplicated().any():
        dupes = clean_plates.loc[clean_plates["plate_id"].duplicated(), "plate_id"].tolist()
        agg_errors.append(f"Duplicate plate_id(s): {', '.join(dupes)}")
    missing_dirs = [row["outdir"] for _, row in clean_plates.iterrows() if not Path(row["outdir"]).is_dir()]
    if missing_dirs:
        agg_errors.append(
            f"Folder(s) not found: {', '.join(missing_dirs)}. Each row must point at a "
            "folder that cli.py (or the Run step above) already wrote results into."
        )
    if not agg_outdir:
        agg_errors.append("Set an aggregated output folder.")
    if agg_periods_path and not Path(agg_periods_path).is_file():
        agg_errors.append(f"Periods CSV not found: {agg_periods_path}")

    if agg_errors:
        for e in agg_errors:
            st.error(e)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            manifest_csv = tmp / "manifest.csv"
            clean_plates.to_csv(manifest_csv, index=False)

            agg_log = io.StringIO()
            agg_outdir_path = Path(agg_outdir)
            try:
                with st.spinner("Aggregating..."):
                    with contextlib.redirect_stdout(agg_log):
                        aggregate_mod.aggregate(
                            manifest_csv=manifest_csv,
                            outdir=agg_outdir_path,
                            periods_csv=Path(agg_periods_path) if agg_periods_path else None,
                        )
                st.session_state.last_agg_outdir = str(agg_outdir_path)
                st.session_state.last_agg_log = agg_log.getvalue()
                st.success(f"Done -> {agg_outdir_path}")
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")
                with st.expander("Full error"):
                    st.code(traceback.format_exc())
                st.session_state.last_agg_log = agg_log.getvalue()

# --------------------------------------------------------------------------
# Aggregation results
# --------------------------------------------------------------------------
if st.session_state.get("last_agg_outdir"):
    agg_outdir_path = Path(st.session_state["last_agg_outdir"])
    if agg_outdir_path.is_dir():
        st.subheader("Aggregation results")
        agg_log_text = st.session_state.get("last_agg_log", "")
        with st.expander("Aggregation log"):
            st.code(agg_log_text)

        agg_run_info = agg_outdir_path / "aggregate_run_info.txt"
        if agg_run_info.exists():
            info_text = agg_run_info.read_text()
            if "WARNING:" in agg_log_text:
                st.warning(info_text)
            else:
                st.caption(info_text)

        agg_per_well_csv = agg_outdir_path / "aggregated_per_well_metrics.csv"
        if agg_per_well_csv.exists():
            st.write("Aggregated per-well metrics")
            st.dataframe(pd.read_csv(agg_per_well_csv), width="stretch")

        agg_activity_svg = agg_outdir_path / "plots" / "activity_over_time.svg"
        if agg_activity_svg.exists():
            st.write("Pooled activity over time")
            st.markdown(agg_activity_svg.read_text(), unsafe_allow_html=True)

        agg_by_plate_svg = agg_outdir_path / "plots" / "activity_over_time_by_plate.svg"
        if agg_by_plate_svg.exists():
            st.write("Activity over time, by plate (batch-effect check)")
            st.markdown(agg_by_plate_svg.read_text(), unsafe_allow_html=True)

        agg_zip_base = agg_outdir_path.parent / (agg_outdir_path.name + "_agg_results")
        agg_zip_path = shutil.make_archive(str(agg_zip_base), "zip", root_dir=agg_outdir_path)
        with open(agg_zip_path, "rb") as f:
            st.download_button(
                "Download all aggregated results (.zip)",
                f,
                file_name=Path(agg_zip_path).name,
                key="agg_zip_dl",
            )
