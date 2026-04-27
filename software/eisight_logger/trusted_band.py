"""trusted_band.py -- §H.5 trusted-band selection: per-frequency
band membership for the §I.5 raw and §I.6 calibration CSVs.

§H.5 admits a frequency point to the trusted band only if ALL
of the following hold:

  1. magnitude residual on R330 / R470 / R1k below threshold
     (H.4 v1: <= 3 %, stronger paper target 1.5 %)
  2. phase residual on resistors not pathological
     (H.4 v1: <= 5 deg, stronger 3 deg)
  3. replicate CV acceptable
     (H.4 v1: <= 1 %, stronger 0.5 %)
  4. AD5933 STATUS flags valid (H.8: invalid-data flag rejects)
  5. no real/imag saturation at the §I.2.a int16 endpoints
     (H.8 / §I.2.a)
  6. no phase discontinuity (H.8: phase jump > 10 deg between
     adjacent resistor frequency points; both endpoints taint)
  7. G-SAT did not flag the load at this frequency (F.10.a
     analysis result; supplied by gates.py)

Magnitude residual uses the §H.2 identity: with
GF_X(f) = 1 / (R_X * M_X(f)), the apparent calibrated
|Z_X|(f) under the anchor's GF is R_X * (GF_X / GF_anchor),
so the per-frequency residual collapses to
(GF_X - GF_anchor) / GF_anchor. No re-derivation from raw
real/imag -- the §I.6 calibration table already holds GF per
load.

The trusted band is module-level (per §H.5): all loads at a
given (module, frequency) share the same boolean. Per-row
trusted_flag is the literal "True"/"False"/"" string per the
§I.5/§I.6 convention locked in raw_writer.py.

Implements: §H.5 (trusted-band selection), §H.4 (default
thresholds), §H.8 (status / saturation / phase-jump rules),
§I.2.a (int16 saturation endpoints). Consumes: §I.5 raw CSV,
§I.6 calibration CSV, optional gates.py G-SAT result.
"""

from __future__ import annotations

from typing import Optional, Set, Tuple

import numpy as np
import pandas as pd

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

_FLAG_COLUMNS = ["module_id", "frequency_hz", "trusted", "reasons"]


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
    """Evaluate per-(module_id, frequency_hz) §H.5 band membership.

    cal_df is a §I.6 calibration table (calibration.CAL_CSV_COLUMNS).
    raw_df is a §I.5 raw long-format table
    (raw_writer.RAW_CSV_COLUMNS).

    g_sat_failures, when supplied, must have columns
    (module_id, frequency_hz, load_id) -- one row per
    G-SAT-flagged (module, freq, load) triple, as produced by
    gates.py. A frequency appearing for any band_resistor is
    marked untrusted with reason 'G-SAT failed'. Pass None
    while gates.py has not yet been wired -- criterion 7 is
    silently skipped.

    Returns a DataFrame with columns (module_id, frequency_hz,
    trusted, reasons). Use trusted_flags_to_str + merge_trusted_flag
    to push the result back into raw.csv and calibration.csv.
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
    for module_id, mod_grid in grid.groupby("module_id", sort=True):
        cal_mod = cal[cal["module_id"] == module_id]
        raw_mod = raw[raw["module_id"] == module_id]

        phase_jump_freqs = _phase_jump_freqs(
            cal_mod, band_resistors, phase_jump_deg_max
        )
        anchor_rows = cal_mod[cal_mod["load_id"] == anchor_load_id]
        anchor_gf = dict(zip(
            anchor_rows["frequency_hz"].astype(float),
            anchor_rows["gain_factor"].astype(float),
        ))
        g_sat_freqs = _g_sat_freqs_for(
            g_sat_failures, module_id, band_resistors
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
            reasons += _check_phase_residual(cal_at_f, phase_residual_deg_max)
            reasons += _check_cv(cal_at_f, cv_pct_max)
            reasons += _check_status_and_saturation(raw_at_f)
            if freq in phase_jump_freqs:
                reasons.append(f"phase jump >{phase_jump_deg_max:g}deg adjacent")
            if freq in g_sat_freqs:
                reasons.append("G-SAT failed")

            out_rows.append({
                "module_id": module_id,
                "frequency_hz": freq,
                "trusted": len(reasons) == 0,
                "reasons": "; ".join(reasons),
            })

    return pd.DataFrame(out_rows, columns=_FLAG_COLUMNS)


def _coerce_cal(cal_df: pd.DataFrame) -> pd.DataFrame:
    cal = cal_df.copy()
    for col in ("frequency_hz", "gain_factor", "phase_system_deg"):
        cal[col] = pd.to_numeric(cal[col], errors="raise")
    cal["repeat_cv_percent"] = pd.to_numeric(
        cal["repeat_cv_percent"], errors="coerce"
    )
    return cal


def _coerce_raw(raw_df: pd.DataFrame) -> pd.DataFrame:
    raw = raw_df[raw_df["row_type"] == "CAL"].copy()
    if raw.empty:
        return raw
    for col in ("real", "imag", "frequency_hz", "status"):
        raw[col] = pd.to_numeric(raw[col], errors="raise")
    return raw


def _build_grid(cal: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    cal_grid = cal[["module_id", "frequency_hz"]].drop_duplicates()
    raw_grid = (
        raw[["module_id", "frequency_hz"]].drop_duplicates()
        if not raw.empty
        else pd.DataFrame(columns=["module_id", "frequency_hz"])
    )
    return (
        pd.concat([cal_grid, raw_grid], ignore_index=True)
        .drop_duplicates()
        .sort_values(["module_id", "frequency_hz"])
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
    band_resistors: Tuple[str, ...],
) -> Set[float]:
    if g_sat_failures is None:
        return set()
    gsf = g_sat_failures[
        (g_sat_failures["module_id"] == module_id)
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


def _check_phase_residual(cal_at_f: pd.DataFrame, threshold_deg: float) -> list:
    # Ideal R has 0 phase, so |phi_system_deg| IS the residual.
    reasons = []
    for _, row in cal_at_f.iterrows():
        phi_deg = abs(float(row["phase_system_deg"]))
        if phi_deg > threshold_deg:
            reasons.append(
                f"|phi_system| {phi_deg:.2f}deg > {threshold_deg:g}deg on {row['load_id']}"
            )
    return reasons


def _check_cv(cal_at_f: pd.DataFrame, threshold_pct: float) -> list:
    reasons = []
    for _, row in cal_at_f.iterrows():
        cv = row["repeat_cv_percent"]
        if pd.notna(cv) and float(cv) > threshold_pct:
            reasons.append(
                f"CV {float(cv):.2f}% > {threshold_pct:g}% on {row['load_id']}"
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
    """Set trusted_flag on target_df via (module_id, frequency_hz) join.

    target_df is either a §I.5 raw DataFrame or a §I.6 calibration
    DataFrame; both have module_id and frequency_hz columns. The
    trusted band is module-level per §H.5, so every row at a
    given (module, freq) shares the same boolean.

    Returns a new DataFrame; target_df is not mutated. Rows whose
    (module_id, frequency_hz) is absent from flags_df get
    trusted_flag = '' (the not-yet-evaluated marker).
    """
    out = target_df.copy()
    out["frequency_hz"] = pd.to_numeric(out["frequency_hz"], errors="coerce")
    flags = flags_df[["module_id", "frequency_hz", "trusted"]].copy()
    flags["frequency_hz"] = flags["frequency_hz"].astype(float)
    merged = out.merge(flags, on=["module_id", "frequency_hz"], how="left")
    merged["trusted_flag"] = trusted_flags_to_str(merged["trusted"])
    return merged.drop(columns=["trusted"])
