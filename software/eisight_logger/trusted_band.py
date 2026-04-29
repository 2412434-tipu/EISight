"""trusted_band.py -- §H.5 trusted-band selection: per-frequency
band membership for the §I.5 raw and §I.6 calibration CSVs.

§H.5 admits a frequency point to the trusted band only if ALL
of the following hold:

  1. magnitude residual on R330 / R470 / R1k below threshold
     (H.4 v1: <= 3 %, stronger paper target 1.5 %)
  2. replicate CV acceptable on every required band resistor
     (H.4 v1: <= 1 %, stronger 0.5 %). NaN CV (under-replicated
     CAL group; calibration emits NaN when fewer than 2 repeats
     survived) is treated as untrusted -- the §H.4 cv check
     cannot pass without an actual replicate variance, so the
     'silent NaN passes' default would be unsafe.
  3. AD5933 STATUS flags valid (H.8: invalid-data flag rejects)
  4. no real/imag saturation at the §I.2.a int16 endpoints
     (H.8 / §I.2.a)
  5. no phase discontinuity (H.8: |Δφ_system| > 10 deg between
     adjacent resistor frequency points; both endpoints taint).
     This is a smoothness check on the per-load atan2 sequence,
     not an absolute-phase residual -- phase_system_deg by
     itself is not a resistor residual (the IV-stage TIA + ADC
     adds a per-frequency offset that is a real characterization,
     not a defect), so an absolute |φ_system_deg| > N rejection
     would falsely throw out healthy frequencies.
  6. G-SAT did not flag the load at this frequency (F.10.a
     analysis result; supplied by gates.py)

Earlier revisions of this module also applied an absolute
|phase_system_deg| > 5 deg rejection criterion. That was
removed: phase_system_deg is the *system* phase offset (the
fixture + AD5933 IV-stage added phase), and treating it as a
resistor residual conflates calibration evidence with defect
detection. A corrected resistor phase-*residual* check would
need to compute the post-calibration phase residual against the
ideal-resistor 0-deg model, not the raw phase_system_deg, and
is intentionally not implemented here pending the §H.4 v2
phase-residual definition.

Magnitude residual uses the §H.2 identity: with
GF_X(f) = 1 / (R_X * M_X(f)), the apparent calibrated
|Z_X|(f) under the anchor's GF is R_X * (GF_X / GF_anchor),
so the per-frequency residual collapses to
(GF_X - GF_anchor) / GF_anchor. No re-derivation from raw
real/imag -- the §I.6 calibration table already holds GF per
load.

The trusted band is module/range-level (per the §I.6 calibration
contract): all loads at a given (module, range, frequency) share
the same boolean. Per-row trusted_flag is the literal
"True"/"False"/"" string per the §I.5/§I.6 convention locked in
raw_writer.py.

Implements: §H.5 (trusted-band selection), §H.4 (default
thresholds), §H.8 (status / saturation / phase-jump rules),
§I.2.a (int16 saturation endpoints). Consumes: §I.5 raw CSV,
§I.6 calibration CSV, optional gates.py G-SAT result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Set, Tuple, Union

import numpy as np
import pandas as pd

from eisight_logger.calibration import CAL_CSV_COLUMNS
from eisight_logger.raw_writer import RAW_CSV_COLUMNS

# §I.2.a int16 endpoints. Either is a saturation flag.
_INT16_MAX = 32767
_INT16_MIN = -32768
# AD5933 STATUS bit D1 = valid real/imag (datasheet).
_STATUS_VALID_DATA_MASK = 0x02

# H.4 v1 minimum-go thresholds. Exposed so callers (cli.py,
# tests, alternate-band experiments) override per-call without
# re-defining the magic numbers.
TRUSTED_BAND_MAG_RESIDUAL_PCT = 3.0
TRUSTED_BAND_PHASE_RESIDUAL_DEG = 5.0
TRUSTED_BAND_CV_PCT = 1.0
TRUSTED_BAND_PHASE_JUMP_DEG = 10.0  # §H.8

# §H.5 names R330/R470/R1k; F.10 anchors on R1k. Defaults match
# the F.7 R<value>_<index> labelling convention.
TRUSTED_BAND_RESISTORS: Tuple[str, ...] = ("R330_01", "R470_01", "R1k_01")
TRUSTED_BAND_ANCHOR_LOAD_ID = "R1k_01"

_KEY_COLUMNS = ["module_id", "range_setting", "frequency_hz"]
_FLAG_COLUMNS = _KEY_COLUMNS + ["trusted", "reasons"]


def evaluate_trusted_band(
    cal_df: pd.DataFrame,
    raw_df: pd.DataFrame,
    *,
    band_resistors: Tuple[str, ...] = TRUSTED_BAND_RESISTORS,
    anchor_load_id: str = TRUSTED_BAND_ANCHOR_LOAD_ID,
    mag_residual_pct_max: float = TRUSTED_BAND_MAG_RESIDUAL_PCT,
    phase_residual_deg_max: float = TRUSTED_BAND_PHASE_RESIDUAL_DEG,
    cv_pct_max: float = TRUSTED_BAND_CV_PCT,
    phase_jump_deg_max: float = TRUSTED_BAND_PHASE_JUMP_DEG,
    g_sat_failures: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Evaluate per-(module_id, range_setting, frequency_hz) §H.5 band membership.

    cal_df is a §I.6 calibration table (calibration.CAL_CSV_COLUMNS).
    raw_df is a §I.5 raw long-format table
    (raw_writer.RAW_CSV_COLUMNS).

    g_sat_failures, when supplied, must have columns
    (module_id, range_setting, frequency_hz, load_id) -- one row
    per G-SAT-flagged (module, range, freq, load) quadruple, as
    produced by gates.py. A frequency appearing for any
    band_resistor in the same range is marked untrusted with
    reason 'G-SAT failed'. Pass None while gates.py has not yet
    been wired -- criterion 7 is silently skipped.

    Returns a DataFrame with columns (module_id, range_setting,
    frequency_hz, trusted, reasons). Use trusted_flags_to_str +
    merge_trusted_flag to push the result back into raw.csv and
    calibration.csv.
    """
    if anchor_load_id not in band_resistors:
        raise ValueError(
            f"anchor_load_id {anchor_load_id!r} must be one of "
            f"band_resistors {band_resistors!r}"
        )

    cal = _coerce_cal(cal_df)
    raw = _coerce_raw(raw_df)
    grid = _build_grid(cal, raw)
    if grid.empty:
        return pd.DataFrame(columns=_FLAG_COLUMNS)

    out_rows = []
    for (module_id, range_setting), mod_grid in grid.groupby(
        ["module_id", "range_setting"], sort=True
    ):
        cal_mod = cal[
            (cal["module_id"] == module_id)
            & (cal["range_setting"] == range_setting)
        ]
        raw_mod = raw[
            (raw["module_id"] == module_id)
            & (raw["range_setting"] == range_setting)
        ]

        phase_jump_freqs = _phase_jump_freqs(
            cal_mod, band_resistors, phase_jump_deg_max
        )
        anchor_rows = cal_mod[cal_mod["load_id"] == anchor_load_id]
        anchor_gf = dict(zip(
            anchor_rows["frequency_hz"].astype(float),
            anchor_rows["gain_factor"].astype(float),
        ))
        g_sat_freqs = _g_sat_freqs_for(
            g_sat_failures, module_id, range_setting, band_resistors
        )

        for freq in sorted(mod_grid["frequency_hz"].unique().tolist()):
            freq = float(freq)
            cal_at_f = cal_mod[
                (cal_mod["frequency_hz"] == freq)
                & (cal_mod["load_id"].isin(band_resistors))
            ]
            raw_at_f = raw_mod[
                (raw_mod["frequency_hz"] == freq)
                & (raw_mod["load_id"].isin(band_resistors))
            ]

            reasons = []
            reasons += _check_presence(cal_at_f, raw_at_f, band_resistors)
            reasons += _check_mag_residual(
                cal_at_f, anchor_gf.get(freq), anchor_load_id,
                mag_residual_pct_max,
            )
            # The absolute |phase_system_deg| residual check was
            # removed -- phase_system is a system characterization,
            # not a resistor defect (see module docstring). The
            # adjacency-jump check below stays as the smoothness
            # criterion. phase_residual_deg_max is retained as a
            # parameter for back-compat / signature stability but
            # no longer drives a per-frequency rejection.
            _ = phase_residual_deg_max
            reasons += _check_cv(cal_at_f, cv_pct_max, band_resistors)
            reasons += _check_status_and_saturation(raw_at_f)
            if freq in phase_jump_freqs:
                reasons.append(f"phase jump >{phase_jump_deg_max:g}deg adjacent")
            if freq in g_sat_freqs:
                reasons.append("G-SAT failed")

            out_rows.append({
                "module_id": module_id,
                "range_setting": range_setting,
                "frequency_hz": freq,
                "trusted": len(reasons) == 0,
                "reasons": "; ".join(reasons),
            })

    return pd.DataFrame(out_rows, columns=_FLAG_COLUMNS)


def _coerce_cal(cal_df: pd.DataFrame) -> pd.DataFrame:
    cal = cal_df.copy()
    _ensure_range_setting(cal)
    for col in ("frequency_hz", "gain_factor", "phase_system_deg"):
        cal[col] = pd.to_numeric(cal[col], errors="raise")
    cal["repeat_cv_percent"] = pd.to_numeric(
        cal["repeat_cv_percent"], errors="coerce"
    )
    return cal


def _coerce_raw(raw_df: pd.DataFrame) -> pd.DataFrame:
    raw = raw_df[raw_df["row_type"] == "CAL"].copy()
    _ensure_range_setting(raw)
    if raw.empty:
        return raw
    for col in ("real", "imag", "frequency_hz", "status"):
        raw[col] = pd.to_numeric(raw[col], errors="raise")
    return raw


def _build_grid(cal: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    cal_grid = cal[_KEY_COLUMNS].drop_duplicates()
    raw_grid = (
        raw[_KEY_COLUMNS].drop_duplicates()
        if not raw.empty
        else pd.DataFrame(columns=_KEY_COLUMNS)
    )
    return (
        pd.concat([cal_grid, raw_grid], ignore_index=True)
        .drop_duplicates()
        .sort_values(_KEY_COLUMNS)
        .reset_index(drop=True)
    )


def _phase_jump_freqs(
    cal_mod: pd.DataFrame,
    band_resistors: Tuple[str, ...],
    threshold_deg: float,
) -> Set[float]:
    # Both endpoints of an adjacent-pair jump are tainted; without
    # independent evidence either side could be the cause.
    out: Set[float] = set()
    for load_id in band_resistors:
        phi_load = (
            cal_mod[cal_mod["load_id"] == load_id]
            .sort_values("frequency_hz")
        )
        if len(phi_load) < 2:
            continue
        f_arr = phi_load["frequency_hz"].to_numpy(dtype=np.float64)
        p_arr = phi_load["phase_system_deg"].to_numpy(dtype=np.float64)
        d_phi = np.abs(np.diff(p_arr))
        for j in np.where(d_phi > threshold_deg)[0]:
            out.add(float(f_arr[j]))
            out.add(float(f_arr[j + 1]))
    return out


def _g_sat_freqs_for(
    g_sat_failures: Optional[pd.DataFrame],
    module_id: str,
    range_setting: str,
    band_resistors: Tuple[str, ...],
) -> Set[float]:
    # Consumes the schema defined in gates.g_sat.G_SAT_FAILURE_COLUMNS.
    if g_sat_failures is None:
        return set()
    if "range_setting" not in g_sat_failures.columns:
        raise ValueError(
            "G-SAT failures table lacks 'range_setting'; trusted-band "
            "cannot safely apply range-specific G-SAT exclusions"
        )
    gsf = g_sat_failures[
        (g_sat_failures["module_id"] == module_id)
        & (g_sat_failures["range_setting"] == range_setting)
        & (g_sat_failures["load_id"].isin(band_resistors))
    ]
    return set(
        pd.to_numeric(gsf["frequency_hz"], errors="raise")
        .astype(float)
        .tolist()
    )


def _check_presence(
    cal_at_f: pd.DataFrame,
    raw_at_f: pd.DataFrame,
    band_resistors: Tuple[str, ...],
) -> list:
    reasons = []
    missing = set(band_resistors) - set(cal_at_f["load_id"].astype(str).tolist())
    if missing:
        reasons.append(f"no calibration data: {sorted(missing)}")
    if raw_at_f.empty:
        reasons.append("no raw data")
    return reasons


def _check_mag_residual(
    cal_at_f: pd.DataFrame,
    anchor_gf_value: Optional[float],
    anchor_load_id: str,
    threshold_pct: float,
) -> list:
    if anchor_gf_value is None or anchor_gf_value == 0.0:
        return [f"no anchor GF at this frequency for {anchor_load_id}"]
    reasons = []
    for _, row in cal_at_f.iterrows():
        if row["load_id"] == anchor_load_id:
            continue  # anchor is its own reference
        gf_x = float(row["gain_factor"])
        residual_pct = abs(gf_x - anchor_gf_value) / anchor_gf_value * 100.0
        if residual_pct > threshold_pct:
            reasons.append(
                f"|epsilon_R| {residual_pct:.2f}% > {threshold_pct:g}% on {row['load_id']}"
            )
    return reasons


def _check_cv(
    cal_at_f: pd.DataFrame,
    threshold_pct: float,
    band_resistors: Tuple[str, ...],
) -> list:
    """§H.4 replicate-CV gate; NaN CV on a required band resistor
    is untrusted.

    NaN repeat_cv_percent means under-replicated CAL (calibration
    emits NaN when fewer than 2 sweeps survived for that
    (module, range, load, frequency) group). The §H.4 cv check is
    "replicate variance is small enough"; we cannot confirm that
    without an actual variance, so for required band resistors a
    NaN cv is rejected. Non-band resistors (informational only)
    keep the legacy silent-skip behavior.
    """
    reasons = []
    required = set(band_resistors)
    for _, row in cal_at_f.iterrows():
        cv = row["repeat_cv_percent"]
        load = str(row["load_id"])
        if pd.isna(cv):
            if load in required:
                reasons.append(
                    f"CV NaN (under-replicated) on {load} -- "
                    "required band resistor"
                )
            continue
        if float(cv) > threshold_pct:
            reasons.append(
                f"CV {float(cv):.2f}% > {threshold_pct:g}% on {load}"
            )
    return reasons


def _check_status_and_saturation(raw_at_f: pd.DataFrame) -> list:
    reasons = []
    for _, row in raw_at_f.iterrows():
        if (int(row["status"]) & _STATUS_VALID_DATA_MASK) == 0:
            reasons.append(
                f"status invalid-data on {row['load_id']} (sweep_id={row['sweep_id']})"
            )
        if (
            int(row["real"]) >= _INT16_MAX
            or int(row["real"]) <= _INT16_MIN
            or int(row["imag"]) >= _INT16_MAX
            or int(row["imag"]) <= _INT16_MIN
        ):
            reasons.append(f"real/imag saturated on {row['load_id']}")
    return reasons


def trusted_flags_to_str(trusted: pd.Series) -> pd.Series:
    """Bool Series -> 'True'/'False'/'' encoding (locked convention).

    Matches the §I.5/§I.6 boolean encoding from raw_writer.py:
    'True' or 'False' for evaluated rows, '' for not-yet-evaluated.
    NaN inputs (the sentinel for not evaluated) become ''.
    """
    return trusted.map({True: "True", False: "False"}).fillna("")


def merge_trusted_flag(
    target_df: pd.DataFrame, flags_df: pd.DataFrame
) -> pd.DataFrame:
    """Set trusted_flag on target_df via the trusted-band key join.

    target_df is either a §I.5 raw DataFrame or a §I.6 calibration
    DataFrame; current §I.5/§I.6 tables have module_id,
    range_setting, and frequency_hz columns. The trusted band is
    module/range-level, so every row at a given (module, range,
    freq) shares the same boolean.

    Returns a new DataFrame; target_df is not mutated. Rows whose
    trusted-band key is absent from flags_df get
    trusted_flag = '' (the not-yet-evaluated marker).
    """
    out = target_df.copy()
    out["frequency_hz"] = pd.to_numeric(out["frequency_hz"], errors="coerce")
    has_target_range = "range_setting" in out.columns
    has_flags_range = "range_setting" in flags_df.columns
    if has_target_range != has_flags_range:
        raise ValueError(
            "trusted_flag merge cannot mix range-aware and range-less "
            "tables; include range_setting on both sides"
        )
    join_cols = list(_KEY_COLUMNS if has_target_range else ["module_id", "frequency_hz"])
    if has_target_range:
        out["range_setting"] = out["range_setting"].fillna("").astype(str)
    flags = flags_df[join_cols + ["trusted"]].copy()
    flags["frequency_hz"] = flags["frequency_hz"].astype(float)
    if "range_setting" in flags.columns:
        flags["range_setting"] = flags["range_setting"].fillna("").astype(str)
    merged = out.merge(flags, on=join_cols, how="left")
    merged["trusted_flag"] = trusted_flags_to_str(merged["trusted"])
    return merged.drop(columns=["trusted"])


def _ensure_range_setting(df: pd.DataFrame) -> None:
    if "range_setting" not in df.columns:
        df["range_setting"] = ""
    df["range_setting"] = df["range_setting"].fillna("").astype(str)


def run_trusted_band(
    raw_path: Union[Path, str],
    cal_path: Union[Path, str],
    raw_output: Optional[Union[Path, str]] = None,
    cal_output: Optional[Union[Path, str]] = None,
    g_sat_failures_path: Optional[Union[Path, str]] = None,
    *,
    mag_residual_pct_max: float = TRUSTED_BAND_MAG_RESIDUAL_PCT,
    phase_residual_deg_max: float = TRUSTED_BAND_PHASE_RESIDUAL_DEG,
    cv_pct_max: float = TRUSTED_BAND_CV_PCT,
    phase_jump_deg_max: float = TRUSTED_BAND_PHASE_JUMP_DEG,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Read §I.5 raw + §I.6 cal; run §H.5 trusted-band; merge; optionally write.

    Composes evaluate_trusted_band + merge_trusted_flag for both
    the raw and cal frames. Returns (merged_raw, merged_cal)
    regardless of output paths. Pass either output to None to
    skip that write; pass both to None to use the result
    in-memory (dashboards, paper figures).

    g_sat_failures_path, when supplied, is read with the same
    str-typed convention and forwarded to evaluate_trusted_band's
    criterion 7. Schema is gates.g_sat.G_SAT_FAILURE_COLUMNS.
    """
    raw_df = pd.read_csv(raw_path, dtype=str, keep_default_na=False)
    cal_df = pd.read_csv(cal_path, dtype=str, keep_default_na=False)
    g_sat = (
        pd.read_csv(g_sat_failures_path, dtype=str, keep_default_na=False)
        if g_sat_failures_path is not None
        else None
    )
    flags = evaluate_trusted_band(
        cal_df, raw_df,
        mag_residual_pct_max=mag_residual_pct_max,
        phase_residual_deg_max=phase_residual_deg_max,
        cv_pct_max=cv_pct_max,
        phase_jump_deg_max=phase_jump_deg_max,
        g_sat_failures=g_sat,
    )
    merged_raw = merge_trusted_flag(raw_df, flags)
    merged_cal = merge_trusted_flag(cal_df, flags)
    if raw_output is not None:
        out = Path(raw_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        merged_raw[RAW_CSV_COLUMNS].to_csv(out, index=False, na_rep="")
    if cal_output is not None:
        out = Path(cal_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        merged_cal[CAL_CSV_COLUMNS].to_csv(out, index=False, na_rep="")
    return merged_raw, merged_cal
