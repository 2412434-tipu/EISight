"""calibration.py -- Per-frequency gain factor and system phase
from CAL sweeps (v4.0c §H.2, §I.6).

§H.2 equations implemented:

  M(f)        = sqrt(R^2 + I^2)              (eq:dft_magnitude)
  GF(f)       = 1 / (R_cal * M_cal(f))       (eq:gain_factor)
  |Z_unk|(f)  = 1 / (GF(f) * M_unk(f))       (eq:unknown_impedance)
  phi_sys(f)  = atan2(I_cal, R_cal)          (eq:system_phase)

build_calibration_table turns the §I.5 raw CSV (filtered to
row_type == 'CAL') into the §I.6 calibration CSV: one row per
(module_id, load_id, frequency_hz). For each (module_id, load_id,
frequency) the F.10 three-repeat sweeps are pooled by averaging
real and imag across sweep_ids first; magnitude and system phase
are then computed from the pooled values. Pooling DFT samples
before computing features avoids the mean-of-atan2 wraparound
trap that mean(phi_per_repeat) would hit. repeat_cv_percent is
std(M_per_repeat) / mean(M_per_repeat) * 100 with ddof=1,
matching the §H.4 "Within-session CV on resistor magnitude"
metric.

phi_system_deg is degrees-converted directly off the per-load
atan2 output -- no unwrap inside this module. The §H.2 unwrap
rule applies to phi_sample (corrected) phase used in feature
extraction; phi_system is a per-load characterization that the
downstream consumer subtracts BEFORE unwrapping.

Anchor selection is downstream: §I.6 stores GF(f) per load, and
the consumer chooses which load (per F.10, R1k by default) is
the gain-factor anchor when calibrating an unknown sweep via
apply_gain_factor.

trusted_flag is left empty here; trusted_band.py fills it after
the H.5 criteria run. The empty-string-when-not-yet-evaluated
convention matches the §I.5 boolean encoding locked in
raw_writer.py.

Implements: §H.2 (calibration math), §I.6 (calibration CSV
schema). Consumes: §I.5 raw CSV.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Union

import numpy as np
import pandas as pd

from eisight_logger.phase import (
    dft_magnitude,
    phase_to_deg,
    raw_phase_rad,
)

# §I.6 calibration CSV columns. Authoritative order.
CAL_CSV_COLUMNS = [
    "session_id",
    "module_id",
    "load_id",
    "nominal_ohm",
    "actual_ohm",
    "dmm_model",
    "dmm_accuracy_class_pct",
    "frequency_hz",
    "gain_factor",
    "phase_system_deg",
    "repeat_cv_percent",
    "trusted_flag",
]


@dataclass(frozen=True)
class ResistorAnchor:
    """Per-load calibration anchor metrology.

    actual_ohm comes from §F.7 (resistor_inventory.csv);
    typically the lab-DMM cross-checked value when G-DMMx
    passes -- see §F.7. dmm_model and dmm_accuracy_class_pct
    propagate to the §I.6 CSV so the metrology bound is
    auditable downstream. dmm_accuracy_class_pct is None when
    the DMM accuracy class was not recorded (G-DMMx skipped);
    it is written to the CSV as an empty cell.
    """

    nominal_ohm: float
    actual_ohm: float
    dmm_model: str = ""
    dmm_accuracy_class_pct: Optional[float] = None


def build_calibration_table(
    raw_df: pd.DataFrame,
    actuals: Dict[str, ResistorAnchor],
    *,
    session_id: Optional[str] = None,
) -> pd.DataFrame:
    """Compute the §I.6 calibration table from §I.5 raw CSV rows.

    raw_df must follow the §I.5 long-format schema (the columns
    raw_writer.RAW_CSV_COLUMNS list). Only rows with
    row_type == 'CAL' contribute. actuals maps load_id ->
    ResistorAnchor; any load_id present in raw_df['load_id']
    but missing from actuals is skipped, since GF cannot be
    computed without R_cal.

    session_id, when supplied, overrides the value carried in
    raw_df. Otherwise raw_df's CAL rows must carry exactly one
    session_id; a mixed-session CAL slice is a pipeline-stage
    error, not silently averaged.

    Returns a DataFrame with the §I.6 columns in declared order.
    trusted_flag is the empty string per the boolean-encoding
    convention; trusted_band.py fills it.
    """
    cal = raw_df[raw_df["row_type"] == "CAL"].copy()
    if cal.empty:
        return pd.DataFrame(columns=CAL_CSV_COLUMNS)

    # raw.csv stores everything as strings via DictWriter; coerce
    # the numeric columns we touch so arithmetic does not silently
    # operate on object dtype.
    for col in ("real", "imag", "frequency_hz"):
        cal[col] = pd.to_numeric(cal[col], errors="raise")

    if session_id is None:
        sids = cal["session_id"].unique()
        if len(sids) != 1:
            raise ValueError(
                "raw_df CAL rows span multiple session_ids "
                f"({list(sids)}); pass session_id= to override."
            )
        session_id = str(sids[0])

    out_rows = []
    grouped = cal.groupby(
        ["module_id", "load_id", "frequency_hz"], sort=True
    )
    for (module_id, load_id, freq), grp in grouped:
        anchor = actuals.get(str(load_id))
        if anchor is None:
            # No metrology -> no row. The §I.5 raw stays intact;
            # downstream can re-run with a fuller actuals map.
            continue

        # Per-sweep mean -- one (sweep_id, frequency) point is one
        # (real, imag) pair, so the .mean() is a no-op pivot when
        # the operator did not duplicate frequencies within a
        # sweep, and a true average if they did.
        per_sweep = grp.groupby("sweep_id")[["real", "imag"]].mean()
        r_mean = float(per_sweep["real"].mean())
        i_mean = float(per_sweep["imag"].mean())

        m_pooled = float(
            dft_magnitude(np.array([r_mean]), np.array([i_mean]))[0]
        )
        if m_pooled <= 0.0 or anchor.actual_ohm <= 0.0:
            # Pathological -- a zero magnitude on a CAL row is a
            # firmware fault. Emit no row; downstream QC will
            # flag the missing point.
            continue

        gain_factor = 1.0 / (anchor.actual_ohm * m_pooled)
        phi_sys_deg = float(
            phase_to_deg(
                raw_phase_rad(np.array([r_mean]), np.array([i_mean]))
            )[0]
        )

        # CV across repeats: each repeat's own |Z| (not the pooled
        # one). Matches §H.4 'Within-session CV on resistor
        # magnitude'. ddof=1 is standard sample CV.
        per_sweep_mag = np.sqrt(
            per_sweep["real"].to_numpy(dtype=np.float64) ** 2
            + per_sweep["imag"].to_numpy(dtype=np.float64) ** 2
        )
        if len(per_sweep_mag) >= 2 and per_sweep_mag.mean() > 0:
            cv_percent = float(
                per_sweep_mag.std(ddof=1) / per_sweep_mag.mean() * 100.0
            )
        else:
            cv_percent = float("nan")

        dmm_acc = (
            anchor.dmm_accuracy_class_pct
            if anchor.dmm_accuracy_class_pct is not None
            else float("nan")
        )

        out_rows.append({
            "session_id": session_id,
            "module_id": module_id,
            "load_id": load_id,
            "nominal_ohm": anchor.nominal_ohm,
            "actual_ohm": anchor.actual_ohm,
            "dmm_model": anchor.dmm_model,
            "dmm_accuracy_class_pct": dmm_acc,
            "frequency_hz": float(freq),
            "gain_factor": gain_factor,
            "phase_system_deg": phi_sys_deg,
            "repeat_cv_percent": cv_percent,
            "trusted_flag": "",
        })

    return pd.DataFrame(out_rows, columns=CAL_CSV_COLUMNS)


def write_calibration_csv(
    df: pd.DataFrame, path: Union[Path, str]
) -> None:
    """Write a §I.6 calibration DataFrame to disk in column order.

    Raises if df has unexpected or missing columns. NaN cells
    serialize to the empty string via na_rep so a str-only
    round-trip preserves the 'not yet evaluated' semantic.
    """
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    extra = set(df.columns) - set(CAL_CSV_COLUMNS)
    if extra:
        raise ValueError(
            f"calibration df has unexpected columns: {sorted(extra)}"
        )
    missing = set(CAL_CSV_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(
            f"calibration df missing columns: {sorted(missing)}"
        )
    df = df[CAL_CSV_COLUMNS]
    df.to_csv(out_path, index=False, na_rep="")


def apply_gain_factor(
    gain_factor: np.ndarray, magnitude_unknown: np.ndarray
) -> np.ndarray:
    """|Z_unk|(f) = 1 / (GF(f) * M_unk(f))  (eq:unknown_impedance).

    gain_factor is the per-frequency anchor GF(f) array (typically
    extracted from the §I.6 calibration CSV by selecting one
    load_id, e.g. 'R1k_01' per F.10), and magnitude_unknown is
    M_unk(f) computed via phase.dft_magnitude on the unknown
    sweep's signed real/imag arrays at the same frequency grid.

    Returns the calibrated impedance magnitude array; np.inf
    where (GF * M_unk) is zero. The caller is expected to filter
    against the trusted band before interpreting the result.
    """
    gf = np.asarray(gain_factor, dtype=np.float64)
    mu = np.asarray(magnitude_unknown, dtype=np.float64)
    if gf.shape != mu.shape:
        raise ValueError(
            "gain_factor/magnitude shape mismatch: "
            f"{gf.shape} vs {mu.shape}"
        )
    denom = gf * mu
    with np.errstate(divide="ignore"):
        z = np.where(denom != 0.0, 1.0 / denom, np.inf)
    return z
