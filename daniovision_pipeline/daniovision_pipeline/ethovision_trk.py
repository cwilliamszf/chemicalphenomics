#!/usr/bin/env python3
"""
ethovision_trk.py — read Noldus EthoVision XT ``.trk`` track files without EthoVision.

Format (reverse-engineered and validated against an EthoVision "Export Raw Data"
xlsx; see validate_against_export() and the README for the evidence):

  [ opaque prologue ]
  [ 16-byte GUID ]
  [ uint32  n_samples ]
  [ uint32  unknown (0) ]
  [ float64 sample_rate_hz ]
  [ float64 unknown (0.0) ]
  [ uint32 x4  unknown -- last two appear to be arena grid cols, rows ]
  [ UTF-16LE field-name table: SubjectId, StartTime, X Coordinate, Y Coordinate,
    Z Coordinate, Area, ChangedArea, Elongation, MergeState,
    Enter Zone Id, Hidden Zone Id ]
  [ UTF-16LE trial start timestamp, e.g. "2025-12-18 09:22:54.943371" ]
  [ zero padding ]
  [ n_samples * 58-byte fixed records, one per frame, in acquisition order ]

Record (58 bytes, little-endian, no alignment padding):

  off  type      field
    0  float64   X Coordinate      image pixels
    8  float64   Y Coordinate      image pixels, origin top-left, Y increases downward
   16  float64   Z Coordinate      always 0.0 in 2D tracking
   24  float64   Area              pixel count (integral)
   32  float64   ChangedArea       pixel count (integral)
   40  float64   Elongation        dimensionless
   48  int16     MergeState        0 = normal, -1 = no sample
   50  int32     Enter Zone Id     -1 = none
   54  int32     Hidden Zone Id    -1 = none

A frame with no detected subject is written as all-fields = -1. This is
supposed to always line up with MergeState == -1, but is not guaranteed to:
across a full 24-arena plate, one filtered file had 2 (of 75001) frames
where MergeState read 0 but position/area were still -1. Treat the field
values themselves, not MergeState, as the authoritative "no sample" signal
-- see _valid_mask().

IMPORTANT — this file holds RAW detection output. EthoVision's own export is
(a) calibrated to cm, (b) linearly interpolated across missing samples, and
(c) smoothed by the track-smoothing profile in Detection Settings. On the
validation dataset, total distance moved computed from the raw coordinates was
4.27x the value EthoVision exported. Do not compare raw-derived distance or
velocity against EthoVision-derived numbers. See --smooth and the README --
and see `compare`/compare_tracks() if you have the paired
FilteredTrackFile*.btn, which already carries EthoVision's own smoothing.

Usage:
  python ethovision_trk.py info    FILE.trk
  python ethovision_trk.py convert FILE.trk -o out.csv [--scale S --x0 X --y0 Y]
                                            [--interpolate] [--smooth N]
  python ethovision_trk.py calibrate FILE.trk --export raw_export.xlsx --sheet 1
  python ethovision_trk.py validate  FILE.trk --export raw_export.xlsx --sheet 1
  python ethovision_trk.py compare   raw.trk filtered.btn
"""

from __future__ import annotations

import argparse
import csv
import re
import struct
import sys
import zipfile
from dataclasses import dataclass, field

import numpy as np

RECORD_DTYPE = np.dtype(
    [
        ("x", "<f8"),
        ("y", "<f8"),
        ("z", "<f8"),
        ("area", "<f8"),
        ("changed_area", "<f8"),
        ("elongation", "<f8"),
        ("merge_state", "<i2"),
        ("enter_zone_id", "<i4"),
        ("hidden_zone_id", "<i4"),
    ]
)
RECORD_SIZE = 58
assert RECORD_DTYPE.itemsize == RECORD_SIZE

EXPECTED_FIELDS = [
    "SubjectId",
    "StartTime",
    "X Coordinate",
    "Y Coordinate",
    "Z Coordinate",
    "Area",
    "Elongation",
    "MergeState",
]

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

# Matches both raw track filenames (Track_filet0000a0000o0000_0001.trk) and
# their paired filtered-track counterparts
# (FilteredTrackFileh0000t0000a0000o0000_0001.btn). The two share the exact
# same 58-byte-record layout -- see cmd_compare / compare_tracks() below --
# the filtered file just has every X/Y position run through EthoVision's own
# track smoothing and gap-filling, still in the same raw pixel space.
TRACK_NAME_RE = re.compile(
    r"(?:h(?P<hist>\d+))?t(?P<trial>\d+)a(?P<arena>\d+)o(?P<object>\d+)_(?P<seq>\d+)\.(?:trk|btn)$",
    re.I,
)


class TrkFormatError(Exception):
    """The file does not match the validated 58-byte-record layout."""


def _valid_mask(records: np.ndarray) -> np.ndarray:
    """Frames with an actual detected/filled position.

    Per the format's own convention (see module docstring: "A frame with no
    detected subject is written as all-fields = -1"), MergeState == -1 is
    supposed to be the authoritative "no sample" flag. On a full 24-arena
    plate, one filtered file had 2 out of 75001 frames where MergeState read
    0 (normal) but X/Y/Z/Area/ChangedArea were all still -1 -- i.e. the two
    signals disagreed. Treating the field values themselves as authoritative
    (not just MergeState) is what the documented convention actually says,
    and is safer than trusting a flag that's been observed to be wrong.
    """
    return (records["merge_state"] != -1) & (records["x"] != -1.0)


@dataclass
class TrkFile:
    path: str
    n_samples: int
    sample_rate: float
    start_time: str
    data_offset: int
    stride: int
    strings: list = field(default_factory=list)
    records: np.ndarray = field(default=None, repr=False)

    @property
    def missing(self) -> np.ndarray:
        """Boolean mask of frames with no usable position (see _valid_mask)."""
        return ~_valid_mask(self.records)

    @property
    def time_s(self) -> np.ndarray:
        return np.arange(self.n_samples) / self.sample_rate


def _utf16_strings(blob: bytes, min_len: int = 3):
    out = []
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len, blob):
        out.append((m.start(), m.group().decode("utf-16-le")))
    return out


def read_trk(path: str, load_records: bool = True) -> TrkFile:
    with open(path, "rb") as fh:
        blob = fh.read()

    anchor = blob.find("SubjectId".encode("utf-16-le"))
    if anchor < 0:
        raise TrkFormatError(
            f"{path}: no 'SubjectId' field-name marker found. This is not an "
            "EthoVision XT track file, or it uses a layout this parser has not "
            "been validated against."
        )

    n_samples, = struct.unpack_from("<I", blob, anchor - 48)
    sample_rate, = struct.unpack_from("<d", blob, anchor - 40)

    if not (0 < n_samples < 1 << 30):
        raise TrkFormatError(f"{path}: implausible sample count {n_samples}")
    if not (0 < sample_rate <= 1000):
        raise TrkFormatError(f"{path}: implausible sample rate {sample_rate}")

    strings = [s for _, s in _utf16_strings(blob[anchor - 64: anchor + 4096])]

    # Refuse anything whose per-frame schema differs from the validated one.
    missing_fields = [f for f in EXPECTED_FIELDS if f not in strings]
    if missing_fields:
        raise TrkFormatError(
            f"{path}: field-name table is missing {missing_fields}. The record "
            "layout is schema-dependent; refusing to guess. Export this trial "
            "from EthoVision and open an issue with the header bytes."
        )

    start_time = ""
    for _, s in _utf16_strings(blob[anchor: anchor + 4096]):
        if TIMESTAMP_RE.match(s):
            start_time = s
            break

    payload = len(blob) - n_samples * RECORD_SIZE
    if payload <= anchor:
        raise TrkFormatError(
            f"{path}: file is too short for {n_samples} records of "
            f"{RECORD_SIZE} bytes. It may be truncated."
        )
    stride = (len(blob) - payload) // n_samples
    if stride != RECORD_SIZE:
        raise TrkFormatError(f"{path}: derived stride {stride} != {RECORD_SIZE}")

    trk = TrkFile(
        path=path,
        n_samples=n_samples,
        sample_rate=sample_rate,
        start_time=start_time,
        data_offset=payload,
        stride=stride,
        strings=strings,
    )
    if load_records:
        trk.records = np.frombuffer(
            blob, dtype=RECORD_DTYPE, count=n_samples, offset=payload
        )
        _sanity_check(trk)
    return trk


def _sanity_check(trk: TrkFile) -> None:
    """Cheap structural checks that catch a misaligned parse immediately."""
    r = trk.records
    ok = _valid_mask(r)
    if ok.sum() == 0:
        raise TrkFormatError(f"{trk.path}: every frame reads as missing")
    bad_merge = ~np.isin(r["merge_state"], (-1, 0, 1, 2, 3))
    if bad_merge.mean() > 0.001:
        raise TrkFormatError(
            f"{trk.path}: MergeState took {np.unique(r['merge_state'])[:8]} — "
            "record alignment is wrong."
        )
    area = r["area"][ok]
    if not np.all(np.isfinite(area)) or area.min() < 0:
        raise TrkFormatError(f"{trk.path}: Area contains non-finite or negative values")
    if not np.allclose(area, np.round(area)):
        raise TrkFormatError(
            f"{trk.path}: Area is not integral — expected a pixel count. "
            "Record alignment is wrong."
        )
    el = r["elongation"][ok]
    if el.min() < -0.001 or el.max() > 1.001:
        raise TrkFormatError(
            f"{trk.path}: Elongation outside [0,1] (min {el.min()}, max {el.max()})"
        )


# --------------------------------------------------------------------------
# post-processing
# --------------------------------------------------------------------------

def interpolate_gaps(v: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """Linear interpolation across missing samples, matching EthoVision."""
    out = v.astype(float).copy()
    idx = np.arange(len(v))
    if ok.sum() < 2:
        return out
    out[~ok] = np.interp(idx[~ok], idx[ok], v[ok])
    return out


def lowess_linear(v: np.ndarray, window: int) -> np.ndarray:
    """Local linear regression with tricube weights over a fixed odd window.

    APPROXIMATION. This is not EthoVision's smoother; it is the closest simple
    filter I could fit to the reference export. Report the window you used.
    """
    if window < 3 or window % 2 == 0:
        raise ValueError("window must be an odd integer >= 3")
    h = window // 2
    t = np.arange(-h, h + 1, dtype=float)
    w = (1.0 - np.abs(t / (h + 1)) ** 3) ** 3
    s0, s1, s2 = w.sum(), (w * t).sum(), (w * t * t).sum()
    kern = (s2 * w - s1 * w * t) / (s0 * s2 - s1 * s1)
    pad = np.r_[np.full(h, v[0]), v, np.full(h, v[-1])]
    return np.convolve(pad, kern[::-1], mode="valid")


# --------------------------------------------------------------------------
# reference export reader (for calibrate / validate)
# --------------------------------------------------------------------------

_ROW_RE = re.compile(r'<row r="(\d+)">(.*?)</row>', re.S)
_CELL_RE = re.compile(
    r'<c r="([A-Z]+)\d+"[^>]*>(?:<v>([^<]*)</v>|<is><t>([^<]*)</t></is>)?</c>'
)


def read_export_sheet(xlsx_path: str, sheet_index: int = 1):
    """Stream one sheet of an EthoVision raw-data export.

    Returns (header dict, dict of column-letter -> float array).
    """
    with zipfile.ZipFile(xlsx_path) as z:
        name = f"xl/worksheets/sheet{sheet_index}.xml"
        if name not in z.namelist():
            raise FileNotFoundError(f"{name} not in {xlsx_path}")
        buf = z.read(name).decode("utf-8", "replace")

    header, rows = {}, []
    n_header = None
    for m in _ROW_RE.finditer(buf):
        r = int(m.group(1))
        cells = {}
        for cm in _CELL_RE.finditer(m.group(2)):
            cells[cm.group(1)] = cm.group(2) if cm.group(2) is not None else cm.group(3)
        if r == 1:
            n_header = int(cells.get("B", "36"))
        if n_header is not None and r <= n_header - 2:
            if "A" in cells:
                header[cells["A"]] = cells.get("B", "")
        elif n_header is not None and r > n_header:
            rows.append(cells)

    cols = sorted({c for row in rows for c in row})
    out = {}
    for c in cols:
        arr = np.full(len(rows), np.nan)
        for i, row in enumerate(rows):
            v = row.get(c)
            if v is not None:
                try:
                    arr[i] = float(v)
                except ValueError:
                    pass
        out[c] = arr
    return header, out


def calibrate(trk: TrkFile, xlsx_path: str, sheet_index: int = 1):
    """Recover cm-per-pixel and the arena origin from a reference export.

    Scale comes from Area, which EthoVision stores as an integral pixel count in
    the .trk and exports in cm^2; the ratio is exact to the export's 6
    significant figures. Offsets come from the median of (exported - scaled raw),
    which is robust to the track smoothing that the export has already applied.
    """
    _, cols = read_export_sheet(xlsx_path, sheet_index)
    Xe, Ye, Ae = cols["C"], cols["D"], cols["E"]
    if len(Xe) != trk.n_samples:
        raise ValueError(
            f"export has {len(Xe)} rows, .trk has {trk.n_samples} records — "
            "these are not the same track"
        )
    r = trk.records
    ok = _valid_mask(r) & np.isfinite(Ae)
    ratio = Ae[ok] / r["area"][ok].astype(float)
    scale = float(np.sqrt(np.median(ratio)))
    x0 = float(np.median(Xe[ok] - scale * r["x"][ok]))
    y0 = float(np.median(Ye[ok] + scale * r["y"][ok]))
    spread = float(np.ptp(ratio) / np.median(ratio))
    return {
        "scale_cm_per_px": scale,
        "x0_cm": x0,
        "y0_cm": y0,
        "area_ratio_spread": spread,
        "n_used": int(ok.sum()),
    }


def validate_against_export(trk: TrkFile, xlsx_path: str, sheet_index: int = 1,
                            smooth_window: int = 11):
    cal = calibrate(trk, xlsx_path, sheet_index)
    s, x0, y0 = cal["scale_cm_per_px"], cal["x0_cm"], cal["y0_cm"]
    _, cols = read_export_sheet(xlsx_path, sheet_index)
    Xe, Ye, Ae, ACe, ELe, DMe = (cols["C"], cols["D"], cols["E"],
                                 cols["F"], cols["G"], cols["H"])
    r = trk.records
    ok = _valid_mask(r)

    def relerr(exp, raw, k=1.0):
        m = ok & np.isfinite(exp)
        e, v = exp[m], raw[m].astype(float) * k
        return float(np.percentile(np.abs(e - v) / np.maximum(np.abs(e), 1e-12), 100))

    rep = {
        "calibration": cal,
        "area_max_rel_err": relerr(Ae, r["area"], s * s),
        "changed_area_max_rel_err": relerr(ACe, r["changed_area"], s * s),
        "elongation_max_rel_err": relerr(ELe, r["elongation"]),
        "n_missing_in_trk": int((~ok).sum()),
    }

    x = interpolate_gaps(s * r["x"].astype(float) + x0, ok)
    y = interpolate_gaps(-s * r["y"].astype(float) + y0, ok)
    raw_dist = float(np.sum(np.hypot(np.diff(x), np.diff(y))))
    sx, sy = lowess_linear(x, smooth_window), lowess_linear(y, smooth_window)
    sm_dist = float(np.sum(np.hypot(np.diff(sx), np.diff(sy))))
    exp_dist = float(np.nansum(DMe))
    rep["total_distance_cm"] = {
        "ethovision_export": exp_dist,
        "raw_unsmoothed": raw_dist,
        "raw_over_export": raw_dist / exp_dist,
        f"lowess_w{smooth_window}": sm_dist,
        "lowess_over_export": sm_dist / exp_dist,
    }
    return rep


# --------------------------------------------------------------------------
# raw vs. filtered track comparison
# --------------------------------------------------------------------------

def compare_tracks(raw: TrkFile, filt: TrkFile) -> dict:
    """Compare a raw Track_file*.trk against its paired FilteredTrackFile*.btn.

    Both files share the identical 58-byte record layout; "filtered" applies
    EthoVision's own track smoothing to every X/Y sample and fills gaps
    (frames where MergeState == -1 in both files, since that flag reports
    the original detection, not whether a position was later filled in) with
    smoothed values rather than leaving them at -1. Area/ChangedArea/
    Elongation are per-frame detection properties and pass through unchanged
    (equal to float rounding noise, ~1e-8) -- large differences there would
    indicate the two files are not actually a matched pair / are misaligned.
    """
    if raw.n_samples != filt.n_samples:
        raise ValueError(
            f"sample count mismatch: raw has {raw.n_samples}, filtered has "
            f"{filt.n_samples} -- these are probably not a matched pair"
        )
    rr, fr = raw.records, filt.records
    ok_raw = _valid_mask(rr)
    ok_filt = _valid_mask(fr)
    both_ok = ok_raw & ok_filt

    area_diff = np.abs(fr["area"][both_ok].astype(float) - rr["area"][both_ok].astype(float))
    elong_diff = np.abs(fr["elongation"][both_ok].astype(float) - rr["elongation"][both_ok].astype(float))

    dx = fr["x"][both_ok].astype(float) - rr["x"][both_ok].astype(float)
    dy = fr["y"][both_ok].astype(float) - rr["y"][both_ok].astype(float)

    still_missing = fr["x"][~ok_raw] == -1

    def dist(x, y, ok):
        x, y = np.where(ok, x, np.nan), np.where(ok, y, np.nan)
        return float(np.nansum(np.hypot(np.diff(x), np.diff(y))))

    return {
        "n_samples": raw.n_samples,
        "merge_state_masks_identical": bool(np.array_equal(ok_raw, ok_filt)),
        "area_max_abs_diff": float(area_diff.max()) if len(area_diff) else float("nan"),
        "elongation_max_abs_diff": float(elong_diff.max()) if len(elong_diff) else float("nan"),
        "position_diff_px": {
            "dx_mean": float(dx.mean()), "dx_std": float(dx.std()), "dx_max_abs": float(np.abs(dx).max()),
            "dy_mean": float(dy.mean()), "dy_std": float(dy.std()), "dy_max_abs": float(np.abs(dy).max()),
        },
        "raw_missing_frames": int((~ok_raw).sum()),
        "raw_missing_frames_filled_by_filter": int((~still_missing).sum()),
        "total_distance_px": {
            "raw_unsmoothed": dist(rr["x"].astype(float), rr["y"].astype(float), ok_raw),
            "filtered": dist(fr["x"].astype(float), fr["y"].astype(float), ok_filt),
        },
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cmd_info(args):
    trk = read_trk(args.trk)
    ok = ~trk.missing
    print(f"file           {trk.path}")
    m = TRACK_NAME_RE.search(trk.path)
    if m:
        hist = f", history {int(m['hist'])}" if m["hist"] else ""
        print(f"trial/arena    trial {int(m['trial'])}, arena {int(m['arena'])}, "
              f"object {int(m['object'])}{hist}")
    kind = "filtered" if "filtered" in trk.path.lower() else "raw"
    print(f"kind           {kind}"
          + ("" if kind == "raw" else " (smoothed + gap-filled by EthoVision, still uncalibrated pixels)"))
    print(f"start time     {trk.start_time}")
    print(f"samples        {trk.n_samples}")
    print(f"sample rate    {trk.sample_rate} Hz")
    print(f"duration       {trk.n_samples / trk.sample_rate:.3f} s")
    print(f"data offset    {trk.data_offset} (0x{trk.data_offset:x}), "
          f"stride {trk.stride} B")
    print(f"missing frames {(~ok).sum()} ({100 * (~ok).mean():.4f} %)")
    r = trk.records
    print(f"x px           {r['x'][ok].min():.2f} .. {r['x'][ok].max():.2f}")
    print(f"y px           {r['y'][ok].min():.2f} .. {r['y'][ok].max():.2f}")
    print(f"area px        {r['area'][ok].min():.0f} .. {r['area'][ok].max():.0f}")
    print(f"zones present  enter={np.unique(r['enter_zone_id'])}, "
          f"hidden={np.unique(r['hidden_zone_id'])}")


def cmd_convert(args):
    trk = read_trk(args.trk)
    r = trk.records
    ok = ~trk.missing
    t = trk.time_s

    calibrated = args.scale is not None
    if calibrated:
        x = args.scale * r["x"].astype(float) + (args.x0 or 0.0)
        y = -args.scale * r["y"].astype(float) + (args.y0 or 0.0)
        area = r["area"].astype(float) * args.scale ** 2
        charea = r["changed_area"].astype(float) * args.scale ** 2
    else:
        x, y = r["x"].astype(float), r["y"].astype(float)
        area, charea = r["area"].astype(float), r["changed_area"].astype(float)

    x, y = np.where(ok, x, np.nan), np.where(ok, y, np.nan)
    area = np.where(ok, area, np.nan)
    charea = np.where(ok, charea, np.nan)
    el = np.where(ok, r["elongation"].astype(float), np.nan)

    if args.interpolate:
        x, y = interpolate_gaps(x, ok), interpolate_gaps(y, ok)
    if args.smooth:
        if not args.interpolate:
            x, y = interpolate_gaps(x, ok), interpolate_gaps(y, ok)
        x, y = lowess_linear(x, args.smooth), lowess_linear(y, args.smooth)

    unit = "cm" if calibrated else "px"
    with open(args.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([f"# source: {trk.path}"])
        w.writerow([f"# start_time: {trk.start_time}"])
        w.writerow([f"# sample_rate_hz: {trk.sample_rate}"])
        w.writerow([f"# n_samples: {trk.n_samples}"])
        w.writerow([f"# calibration: scale={args.scale} x0={args.x0} y0={args.y0}"])
        w.writerow([f"# interpolated: {bool(args.interpolate)}  "
                    f"smoothing: lowess_w{args.smooth}" if args.smooth
                    else f"# interpolated: {bool(args.interpolate)}  smoothing: none"])
        w.writerow(
            ["frame", "time_s", f"x_{unit}", f"y_{unit}", f"area_{unit}2",
             f"changed_area_{unit}2", "elongation", "merge_state",
             "enter_zone_id", "hidden_zone_id", "detected"]
        )
        for i in range(trk.n_samples):
            w.writerow(
                [i, f"{t[i]:.3f}",
                 "" if np.isnan(x[i]) else f"{x[i]:.6g}",
                 "" if np.isnan(y[i]) else f"{y[i]:.6g}",
                 "" if np.isnan(area[i]) else f"{area[i]:.6g}",
                 "" if np.isnan(charea[i]) else f"{charea[i]:.6g}",
                 "" if np.isnan(el[i]) else f"{el[i]:.6g}",
                 int(r["merge_state"][i]),
                 int(r["enter_zone_id"][i]),
                 int(r["hidden_zone_id"][i]),
                 int(ok[i])]
            )
    print(f"wrote {args.out}: {trk.n_samples} rows, units {unit}")


def cmd_calibrate(args):
    trk = read_trk(args.trk)
    cal = calibrate(trk, args.export, args.sheet)
    print(f"scale  {cal['scale_cm_per_px']:.10g} cm/px "
          f"({1 / cal['scale_cm_per_px']:.4f} px/cm)")
    print(f"x0     {cal['x0_cm']:.8g} cm")
    print(f"y0     {cal['y0_cm']:.8g} cm")
    print(f"       x_cm =  scale * x_px + x0")
    print(f"       y_cm = -scale * y_px + y0")
    print(f"area ratio spread {cal['area_ratio_spread']:.2e} over "
          f"{cal['n_used']} frames")


def cmd_validate(args):
    trk = read_trk(args.trk)
    rep = validate_against_export(trk, args.export, args.sheet, args.smooth or 11)
    cal = rep["calibration"]
    print("calibration recovered from export")
    print(f"  scale {cal['scale_cm_per_px']:.10g} cm/px, "
          f"x0 {cal['x0_cm']:.6g}, y0 {cal['y0_cm']:.6g}")
    print("worst-case relative error, .trk vs export, all frames")
    print(f"  Area         {rep['area_max_rel_err']:.2e}")
    print(f"  ChangedArea  {rep['changed_area_max_rel_err']:.2e}")
    print(f"  Elongation   {rep['elongation_max_rel_err']:.2e}")
    print(f"  (export prints 6 significant figures, floor ~5e-6)")
    print(f"missing frames in .trk: {rep['n_missing_in_trk']}")
    d = rep["total_distance_cm"]
    print("total distance moved, cm")
    for k, v in d.items():
        print(f"  {k:24s} {v:.4f}")


def cmd_compare(args):
    raw = read_trk(args.raw)
    filt = read_trk(args.filtered)
    rep = compare_tracks(raw, filt)
    print(f"n_samples                            {rep['n_samples']}")
    print(f"MergeState missing-frame masks match  {rep['merge_state_masks_identical']}")
    print(f"raw missing frames                    {rep['raw_missing_frames']}")
    print(f"  ...filled in by filtered file       {rep['raw_missing_frames_filled_by_filter']}")
    print(f"Area max abs diff (sanity: ~0 expected)        {rep['area_max_abs_diff']:.2e}")
    print(f"Elongation max abs diff (sanity: ~0 expected)  {rep['elongation_max_abs_diff']:.2e}")
    pd = rep["position_diff_px"]
    print("position diff, filtered - raw, px (where both have a sample)")
    print(f"  x: mean {pd['dx_mean']:+.4f}  std {pd['dx_std']:.4f}  max|.| {pd['dx_max_abs']:.4f}")
    print(f"  y: mean {pd['dy_mean']:+.4f}  std {pd['dy_std']:.4f}  max|.| {pd['dy_max_abs']:.4f}")
    d = rep["total_distance_px"]
    print("total distance moved, px (uncalibrated, no interpolation/extra smoothing applied here)")
    print(f"  raw_unsmoothed  {d['raw_unsmoothed']:.2f}")
    print(f"  filtered        {d['filtered']:.2f}")
    if rep["area_max_abs_diff"] > 0.01 or rep["elongation_max_abs_diff"] > 0.001:
        print(
            "WARNING: Area/Elongation differ more than float rounding noise -- "
            "these two files may not actually be a matched raw/filtered pair.",
            file=sys.stderr,
        )


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("info", help="print header and track summary")
    pi.add_argument("trk")
    pi.set_defaults(func=cmd_info)

    pc = sub.add_parser("convert", help="write a CSV")
    pc.add_argument("trk")
    pc.add_argument("-o", "--out", required=True)
    pc.add_argument("--scale", type=float, help="cm per pixel")
    pc.add_argument("--x0", type=float, default=0.0, help="x offset in cm")
    pc.add_argument("--y0", type=float, default=0.0, help="y offset in cm")
    pc.add_argument("--interpolate", action="store_true",
                    help="linearly fill missing samples, as EthoVision does")
    pc.add_argument("--smooth", type=int, metavar="N",
                    help="apply tricube local-linear smoothing over an odd "
                         "N-sample window (approximation, see docstring)")
    pc.set_defaults(func=cmd_convert)

    pk = sub.add_parser("calibrate", help="recover scale and origin from an export")
    pk.add_argument("trk")
    pk.add_argument("--export", required=True)
    pk.add_argument("--sheet", type=int, default=1)
    pk.set_defaults(func=cmd_calibrate)

    pv = sub.add_parser("validate", help="check the parse against an export")
    pv.add_argument("trk")
    pv.add_argument("--export", required=True)
    pv.add_argument("--sheet", type=int, default=1)
    pv.add_argument("--smooth", type=int, default=11)
    pv.set_defaults(func=cmd_validate)

    pp = sub.add_parser("compare", help="compare a raw .trk against its paired FilteredTrackFile*.btn")
    pp.add_argument("raw", help="Track_file*.trk")
    pp.add_argument("filtered", help="FilteredTrackFile*.btn")
    pp.set_defaults(func=cmd_compare)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except TrkFormatError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
