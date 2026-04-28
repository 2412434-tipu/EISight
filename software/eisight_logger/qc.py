"""qc.py -- §H.8 sweep QC and rejection rules for the EISight v4.0c
laptop pipeline.

§H.8 lists per-sweep rejection criteria. This module evaluates the
software-checkable subset row-by-row against a §I.5 raw long-format
DataFrame and emits the qc_pass / qc_reasons pair that
raw_writer.py reserved for downstream population.

Rules implemented (per row, unless noted):

  1. AD5933 STATUS invalid-data flag (D1=0): status & 0x02 == 0
     fails the row -- the firmware passes the register through
     unchanged, so the laptop is the first place the bit is
     interpreted (datasheet \\cite{ad5933ds}).
  2. Saturation at the §I.2.a int16 endpoints (real or imag at
     +/- 32767 or -32768): the §F.10/G-SAT clipping fingerprint;
     a saturated point cannot be calibrated.
  3. Non-physical magnitude (sqrt(R^2 + I^2) == 0): a dead-zero
     DFT point is firmware/bus error, not signal. §H.2's M(f)
     is always >= 0 for real-valued R/I, so §H.8's 'negative
     magnitude' wording reduces in software to a zero-check.
  4. Phase jump > 10 deg between adjacent same-sweep,
     same-load, frequency-sorted rows (CAL row_type only).
     Both endpoints of a flagged adjacency are tainted. Mirrors
     the §H.5/§H.8 'no phase discontinuity' rule that
     trusted_band.py applies at module level on phi_system_deg;
     here it runs at sweep level on raw atan2(I, R) converted
     to degrees, so a discontinuity is caught even when
     calibration has not yet been computed.
  5. DS18B20 pre/post drift > 0.5 deg C (per §H.7). All rows of
     the offending sweep_id share the same pre/post temps via
     raw_writer's back-fill, so the per-row check naturally
     flags every row of a drifted sweep.

Rules NOT implemented here (out of scope for the per-row
evaluator):

  - 'missing frequency points': the §I.5 raw CSV does not carry
    start_hz / stop_hz / points; sweep_begin metadata is
    needed. This is the listener stage's responsibility, or a
    follow-up sweep_begin/sweep_end audit pass.
  - CAL session-bracketing checks (drift between start- and
    end-of-session R1k repeats), bubble/fill/cable-motion
    operator notes, post-milk DI blank failures: session-level
    or operator-logged, not row-level. The operator records
    those in the notes column at ingest.

Boolean and reason encoding: qc_pass is a boolean True/False in
the returned evaluator frame. qc_reasons follows the canonical
empty-string-when-passing, semicolon-joined-when-failing
convention from raw_writer.py. merge_qc_columns pushes both back
into a §I.5 DataFrame using the locked 'True'/'False'/''
string encoding.

Implements: §H.8 (sweep QC and rejection rules), §H.7 (temp
drift). Consumes: §I.5 raw CSV (raw_writer.RAW_CSV_COLUMNS).
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from eisight_logger.phase import (
    dft_magnitude,
    phase_to_deg,
    raw_phase_rad,
)

# AD5933 STATUS bit D1 = valid real/imag (datasheet).
_STATUS_VALID_DATA_MASK = 0x02
# §I.2.a int16 endpoints. Either is a saturation flag.
_INT16_MAX = 32767
_INT16_MIN = -32768

# Default §H.8 / §H.7 thresholds. Exposed so callers (cli.py,
# tests) override per-call without re-defining the magic numbers.
QC_PHASE_JUMP_DEG = 10.0  # §H.8
QC_TEMP_DRIFT_C = 0.5     # §H.7

# Columns this module writes back into a §I.5 DataFrame.
QC_OUTPUT_COLUMNS = ["qc_pass", "qc_reasons"]


def evaluate_qc(
    raw_df: pd.DataFrame,
    *,
    phase_jump_deg_max: float = QC_PHASE_JUMP_DEG,
    temp_drift_c_max: float = QC_TEMP_DRIFT_C,
) -> pd.DataFrame:
    """Evaluate §H.8 per-row QC on a §I.5 raw long-format DataFrame.

    Returns a DataFrame indexed identically to raw_df with two
    columns: qc_pass (bool) and qc_reasons (semicolon-joined str,
    "" when qc_pass is True). Use merge_qc_columns to push the
    result back into a §I.5 DataFrame as the locked
    'True'/'False'/'' encoding.

    raw_df is not mutated. Numeric columns ('real', 'imag',
    'frequency_hz', 'status') are coerced via pd.to_numeric on a
    local copy; ds18b20_pre_c / ds18b20_post_c are coerced with
    errors='coerce' since absent metrology is logged as an empty
    cell, not a numeric error.
    """
    if raw_df.empty:
        return pd.DataFrame(
            columns=QC_OUTPUT_COLUMNS, index=raw_df.index
        )

    df = raw_df.copy()
    for col in ("real", "imag", "frequency_hz", "status"):
        df[col] = pd.to_numeric(df[col], errors="raise")
    for col in ("ds18b20_pre_c", "ds18b20_post_c"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    reasons: List[List[str]] = [[] for _ in range(len(df))]

    _flag_status_invalid(df, reasons)
    _flag_saturation(df, reasons)
    _flag_zero_magnitude(df, reasons)
    _flag_phase_jumps(df, reasons, phase_jump_deg_max)
    _flag_temp_drift(df, reasons, temp_drift_c_max)

    qc_pass = [len(r) == 0 for r in reasons]
    qc_reasons = ["; ".join(r) for r in reasons]
    return pd.DataFrame(
        {"qc_pass": qc_pass, "qc_reasons": qc_reasons},
        index=df.index,
    )


# ----------------------------------------------------------------------
# Per-rule helpers. Each appends to the positional reasons list; the
# caller composes them and turns the list-lengths into qc_pass.
# ----------------------------------------------------------------------


def _flag_status_invalid(
    df: pd.DataFrame, reasons: List[List[str]]
) -> None:
    bad = (df["status"].astype(int) & _STATUS_VALID_DATA_MASK) == 0
    for pos in np.where(bad.to_numpy())[0]:
        reasons[pos].append("status invalid-data flag")


def _flag_saturation(
    df: pd.DataFrame, reasons: List[List[str]]
) -> None:
    r = df["real"].astype(int)
    i = df["imag"].astype(int)
    sat = (
        (r >= _INT16_MAX) | (r <= _INT16_MIN)
        | (i >= _INT16_MAX) | (i <= _INT16_MIN)
    )
    for pos in np.where(sat.to_numpy())[0]:
        reasons[pos].append("real/imag saturated at int16 endpoint")


def _flag_zero_magnitude(
    df: pd.DataFrame, reasons: List[List[str]]
) -> None:
    mag = dft_magnitude(
        df["real"].to_numpy(dtype=np.float64),
        df["imag"].to_numpy(dtype=np.float64),
    )
    for pos in np.where(mag == 0.0)[0]:
        reasons[pos].append("non-physical magnitude (sqrt(R^2+I^2)=0)")


def _flag_phase_jumps(
    df: pd.DataFrame,
    reasons: List[List[str]],
    threshold_deg: float,
) -> None:
    """§H.8 phase jump > threshold deg on adjacent CAL points.

    Group by (sweep_id, load_id), sort by frequency, compute raw
    atan2(I, R) in degrees, flag both endpoints of any adjacency
    whose |Δφ| exceeds threshold. Operates on raw phase (no
    calibration needed). The §H.2 unwrap rule applies before
    slope/derivative features, which this discontinuity check is
    not -- adjacency-jump detection only needs |Δφ|.

    CAL-only by design. §H.8's phase-jump rule is anchored to
    near-ideal resistor loads, where any per-frequency phase
    excursion is a system pathology. Sample sweeps (milk, DI,
    fixture-open) have legitimate frequency-dependent phase
    variation, so jump-based QC there would be false-positive
    heavy. Do not extend this helper to sample rows without
    rewriting the threshold logic against an expected-phase
    model.
    """
    cal = df[df["row_type"] == "CAL"]
    if cal.empty:
        return
    label_to_pos: Dict = {label: pos for pos, label in enumerate(df.index)}
    for (sid, lid), grp in cal.groupby(["sweep_id", "load_id"], sort=False):
        if len(grp) < 2:
            continue
        ordered = grp.sort_values("frequency_hz")
        labels = ordered.index.tolist()
        phi_deg = phase_to_deg(
            raw_phase_rad(
                ordered["real"].to_numpy(dtype=np.float64),
                ordered["imag"].to_numpy(dtype=np.float64),
            )
        )
        d_phi = np.abs(np.diff(phi_deg))
        for j in np.where(d_phi > threshold_deg)[0]:
            msg = (
                f"phase jump {float(d_phi[j]):.2f}deg > "
                f"{threshold_deg:g}deg adjacent "
                f"(sweep={sid}, load={lid})"
            )
            for adj_label in (labels[j], labels[j + 1]):
                pos = label_to_pos[adj_label]
                if msg not in reasons[pos]:
                    reasons[pos].append(msg)


def _flag_temp_drift(
    df: pd.DataFrame,
    reasons: List[List[str]],
    threshold_c: float,
) -> None:
    drift = (df["ds18b20_post_c"] - df["ds18b20_pre_c"]).abs()
    bad = drift > threshold_c
    # NaN drift (one of pre/post missing) is False under > thr,
    # so absent metrology is silently skipped rather than flagged.
    for pos in np.where(bad.to_numpy())[0]:
        reasons[pos].append(
            f"DS18B20 drift {float(drift.iat[pos]):.3f}C > {threshold_c:g}C"
        )


# ----------------------------------------------------------------------
# Encoding + merge helpers (mirror trusted_band.merge_trusted_flag).
# ----------------------------------------------------------------------


def qc_pass_to_str(qc_pass: pd.Series) -> pd.Series:
    """Bool Series -> 'True'/'False'/'' encoding (locked convention).

    Same convention as trusted_band.trusted_flags_to_str: 'True'
    or 'False' for evaluated rows, '' for not-yet-evaluated.
    NaN inputs become ''.
    """
    return qc_pass.map({True: "True", False: "False"}).fillna("")


def merge_qc_columns(
    target_df: pd.DataFrame, qc_df: pd.DataFrame
) -> pd.DataFrame:
    """Set qc_pass and qc_reasons on target_df via row-label join.

    qc_df must come from evaluate_qc on the same target_df (the
    indexes line up). Returns a new DataFrame; target_df is not
    mutated. qc_df indexes that are not in target_df.index raise;
    callers compute and merge in one shot, so a partial join is
    unexpected and not silently tolerated.
    """
    extra = set(qc_df.index) - set(target_df.index)
    if extra:
        raise ValueError(
            f"qc_df indexes not found in target_df: {sorted(extra)[:5]}..."
        )
    out = target_df.copy()
    out.loc[qc_df.index, "qc_pass"] = qc_pass_to_str(qc_df["qc_pass"])
    out.loc[qc_df.index, "qc_reasons"] = qc_df["qc_reasons"].astype(str)
    return out
