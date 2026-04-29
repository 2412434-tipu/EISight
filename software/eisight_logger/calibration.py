"""calibration.py -- Per-frequency gain factor and system phase
from CAL sweeps (v4.0c §H.2, §I.6).

§H.2 equations implemented:

  M(f)        = sqrt(R^2 + I^2)              (eq:dft_magnitude)
  GF(f)       = 1 / (R_cal * M_cal(f))       (eq:gain_factor)
  |Z_unk|(f)  = 1 / (GF(f) * M_unk(f))       (eq:unknown_impedance)
  phi_sys(f)  = atan2(I_cal, R_cal)          (eq:system_phase)

build_calibration_table turns the §I.5 raw CSV (filtered to
row_type == 'CAL') into the §I.6 calibration CSV: one row per
(module_id, range_setting, load_id, frequency_hz) -- the
range_setting key is part of the grouping so Range 2 and Range 4
sweeps are NEVER averaged together (the AD5933 gain factor is
range-dependent per datasheet Table 17, so pooling across
ranges silently destroys G-LIN's precondition). For each group
the F.10 three-repeat sweeps are pooled by averaging real and
imag across sweep_ids first; magnitude and system phase are
then computed from the pooled values. Pooling DFT samples
before computing features avoids the mean-of-atan2 wraparound
trap that mean(phi_per_repeat) would hit. repeat_cv_percent is
std(M_per_repeat) / mean(M_per_repeat) * 100 with ddof=1,
matching the §H.4 "Within-session CV on resistor magnitude"
metric.

Strict bench mode (`strict=True`, default for the CLI calibrate
subcommand): refuses to silently fall through when evidence is
insufficient. Specifically it raises CalibrationStrictError on
empty CAL slices, missing actuals for any required F.10 load,
or any (module, range, load, freq) group that arrived with
fewer than required_repeats sweeps. Library callers (notebooks,
synthetic-pipeline tests) keep the permissive default so a
partial fixture is not gratuitously rejected; the bench
workflow must always pass strict=True.

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
from typing import Dict, Iterable, Optional, Tuple, Union

import numpy as np
import pandas as pd

from eisight_logger.phase import (
    dft_magnitude,
    phase_to_deg,
    raw_phase_rad,
)

# §I.6 calibration CSV columns. Authoritative order. range_setting
# is part of the row key (and the (module, range, load, freq)
# groupby in build_calibration_table) so Range 2 and Range 4 cannot
# be silently pooled at any pipeline stage.
CAL_CSV_COLUMNS = [
    "session_id",
    "module_id",
    "load_id",
    "range_setting",
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


# F.10 default required loads -- the strict-mode actuals
# completeness check uses these. Anchor (R1k_01) is included.
DEFAULT_REQUIRED_LOAD_IDS: Tuple[str, ...] = (
    "R330_01", "R470_01", "R1k_01", "R4k7_01",
)

# §F.10 specifies 3 repeats per load. Strict mode treats any
# (module, range, load, frequency) group with fewer than this as
# a non-pass condition (raises in strict mode; emits NaN cv in
# permissive mode so trusted_band downstream marks the row
# untrusted).
DEFAULT_REQUIRED_REPEATS = 3


class CalibrationStrictError(ValueError):
    """Raised by build_calibration_table when strict=True and the
    raw CAL slice is missing the evidence required for a bench
    go/no-go decision (empty CAL, missing actuals for a required
    load, or fewer than required_repeats sweeps for an F.10 load).

    Subclass of ValueError so existing except-ValueError handlers
    keep working, but typed so the CLI can surface a structured
    bench-strictness failure separately from generic schema errors.
    """


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
    strict: bool = False,
    required_load_ids: Optional[Iterable[str]] = None,
    required_repeats: int = DEFAULT_REQUIRED_REPEATS,
    drop_error_sweeps: bool = True,
) -> pd.DataFrame:
    """Compute the §I.6 calibration table from §I.5 raw CSV rows.

    raw_df must follow the §I.5 long-format schema (the columns
    raw_writer.RAW_CSV_COLUMNS list). Only rows with
    row_type == 'CAL' contribute. Rows whose ``notes`` carry the
    listener's ``sweep_end_error=`` tag (set by raw_writer when
    firmware reported a non-null sweep_end.error) are excluded
    when drop_error_sweeps is True -- a partial/errored sweep
    must not silently flow into calibration as valid evidence.

    Grouping key is (module_id, range_setting, load_id,
    frequency_hz). range_setting in the key prevents Range 2 and
    Range 4 sweeps from ever being averaged together (the AD5933
    GF is range-dependent per datasheet Table 17).

    actuals maps load_id -> ResistorAnchor; any load_id present
    in raw_df['load_id'] but missing from actuals is skipped
    in permissive mode (no metrology -> no row). In strict mode,
    every required_load_id (default DEFAULT_REQUIRED_LOAD_IDS)
    must be present in actuals or CalibrationStrictError is
    raised before any row is emitted.

    session_id, when supplied, overrides the value carried in
    raw_df. Otherwise raw_df's CAL rows must carry exactly one
    session_id; a mixed-session CAL slice is a pipeline-stage
    error, not silently averaged.

    strict=True (the bench/CLI default) raises
    CalibrationStrictError on:
      - empty CAL slice in raw_df
      - any required load missing from actuals
      - any (module, range, load, freq) group with fewer than
        required_repeats sweeps (F.10 specifies 3)

    Permissive mode keeps the previous behavior: NaN
    repeat_cv_percent for under-replicated groups so trusted_band
    downstream rejects them, and silently skips loads with no
    metrology.

    Returns a DataFrame with the §I.6 columns in declared order.
    trusted_flag is the empty string per the boolean-encoding
    convention; trusted_band.py fills it.
    """
    required_loads = tuple(
        required_load_ids
        if required_load_ids is not None
        else DEFAULT_REQUIRED_LOAD_IDS
    )

    cal = raw_df[raw_df["row_type"] == "CAL"].copy()
    if drop_error_sweeps and not cal.empty and "notes" in cal.columns:
        bad_mask = cal["notes"].astype(str).str.contains(
            "sweep_end_error=", regex=False, na=False
        )
        cal = cal[~bad_mask]

    if cal.empty:
        if strict:
            raise CalibrationStrictError(
                "no CAL rows present (after sweep_end_error filter) "
                "-- bench calibration cannot be evaluated"
            )
        return pd.DataFrame(columns=CAL_CSV_COLUMNS)

    # Permissive mode: blank module_id/load_id rows fall through to
    # the groupby and produce per-blank rows downstream, which is the
    # documented notebook behavior. Strict bench mode rejects them
    # before any cal row is emitted -- a CAL-shaped record with no
    # load identity cannot be calibrated, and silently dropping it
    # would let an unannotated firmware capture pass G-SAT later.
    if strict:
        # range_setting may be absent on legacy fixtures (e.g. tests
        # that hand-build CAL rows without that column). For strict
        # mode the column must exist explicitly.
        for col_name in (
            "module_id", "load_id", "range_setting", "frequency_hz",
        ):
            if col_name not in cal.columns:
                raise CalibrationStrictError(
                    f"strict: required column {col_name!r} missing from "
                    "CAL slice -- raw CSV must follow §I.5 schema"
                )
            blank_mask = _blank_mask(cal[col_name])
            n_blank = int(blank_mask.sum())
            if n_blank:
                raise CalibrationStrictError(
                    f"strict: {n_blank} CAL row(s) have blank/null "
                    f"{col_name!r} -- firmware emission must be "
                    "annotated (m <id>, h<row_type>, l<load_id>, "
                    "range setting) before CAL rows are usable"
                )

        missing_actuals = [
            ld for ld in required_loads if ld not in actuals
        ]
        if missing_actuals:
            raise CalibrationStrictError(
                f"actuals map missing required F.10 load(s): "
                f"{missing_actuals}; populate hardware/resistor_inventory.csv"
            )

        # Every CAL row's load_id must be in inventory; an unknown
        # load has no R_actual and so no GF -- silently skipping it
        # in permissive mode is OK for notebooks, not for the bench.
        cal_loads = set(cal["load_id"].astype(str).unique())
        unknown_loads = sorted(cal_loads - set(actuals))
        if unknown_loads:
            raise CalibrationStrictError(
                f"strict: CAL rows reference load_id(s) not in "
                f"resistor inventory: {unknown_loads}; populate "
                "hardware/resistor_inventory.csv"
            )

        # Per (module, range) required-load completeness. A module
        # that swept only R1k at RANGE_4 cannot pass G-SAT later;
        # surface the missing evidence here, not as a downstream
        # NOT_EVALUATED that an operator might miss.
        by_mod_range = (
            cal.assign(
                _mod=cal["module_id"].astype(str),
                _rng=cal["range_setting"].astype(str),
                _ld=cal["load_id"].astype(str),
            )
            .groupby(["_mod", "_rng"])["_ld"]
            .apply(lambda s: set(s.unique()))
        )
        missing_per = []
        for (mod, rng), loads in by_mod_range.items():
            absent = [ld for ld in required_loads if ld not in loads]
            if absent:
                missing_per.append({
                    "module_id": mod, "range_setting": rng,
                    "missing_loads": absent,
                })
        if missing_per:
            raise CalibrationStrictError(
                "strict: required F.10 load(s) missing per "
                f"(module_id, range_setting): {missing_per}"
            )

    # raw.csv stores everything as strings via DictWriter; coerce
    # the numeric columns we touch so arithmetic does not silently
    # operate on object dtype.
    for col in ("real", "imag", "frequency_hz"):
        cal[col] = pd.to_numeric(cal[col], errors="raise")

    # range_setting may be absent on legacy fixtures; default to ""
    # so the grouping key is well-defined and the column survives
    # the round-trip into CAL_CSV_COLUMNS.
    if "range_setting" not in cal.columns:
        cal["range_setting"] = ""
    cal["range_setting"] = cal["range_setting"].astype(str).fillna("")

    if session_id is None:
        sids = cal["session_id"].unique()
        if len(sids) != 1:
            raise ValueError(
                "raw_df CAL rows span multiple session_ids "
                f"({list(sids)}); pass session_id= to override."
            )
        session_id = str(sids[0])

    out_rows = []
    underreplicated: list = []
    grouped = cal.groupby(
        ["module_id", "range_setting", "load_id", "frequency_hz"], sort=True
    )
    for (module_id, range_setting, load_id, freq), grp in grouped:
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
        n_repeats = int(len(per_sweep))
        if str(load_id) in required_loads and n_repeats < required_repeats:
            underreplicated.append({
                "module_id": str(module_id),
                "range_setting": str(range_setting),
                "load_id": str(load_id),
                "frequency_hz": float(freq),
                "n_repeats": n_repeats,
            })
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
        # magnitude'. ddof=1 is standard sample CV. NaN when fewer
        # than two repeats survived -- trusted_band treats NaN CV on
        # a required band resistor as untrusted (§H.5 / §H.4 cv check
        # cannot pass without an actual replicate variance).
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
            "range_setting": str(range_setting),
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

    if strict and underreplicated:
        # Surface up to the first few offenders so the operator can
        # see which (module, range, load, freq) is short. The full
        # list is not echoed to keep the error readable.
        sample = underreplicated[:5]
        raise CalibrationStrictError(
            f"{len(underreplicated)} (module,range,load,freq) group(s) "
            f"have fewer than {required_repeats} CAL repeats "
            f"(F.10 requires {required_repeats}). First few: {sample}"
        )

    if strict and not out_rows:
        # Pathological tail: blank/required/repeats checks all
        # passed but every group ended up unwritten (m_pooled <= 0
        # or anchor.actual_ohm <= 0 on every CAL row). The bench
        # workflow must not accept an empty cal table -- downstream
        # G-SAT/G-LIN would silently mark NOT_EVALUATED.
        raise CalibrationStrictError(
            "strict: final calibration table is empty (every CAL "
            "group rejected by m_pooled<=0 or actual_ohm<=0); raw "
            "data is unusable -- inspect raw.jsonl for firmware fault"
        )

    return pd.DataFrame(out_rows, columns=CAL_CSV_COLUMNS)


def _blank_mask(series: pd.Series) -> pd.Series:
    """Boolean mask: True where the cell is null, blank, or 'nan'.

    Captures the four ways a §I.5 raw CSV cell encodes 'no value'
    after a round-trip through pandas: native NaN/None, the empty
    string (DictWriter for unannotated firmware emission), the
    literal 'nan' string (str(float('nan')) when a float pipeline
    re-stringifies it), and trailing/leading whitespace.
    """
    s = series.astype(str).str.strip()
    return series.isna() | (s == "") | (s.str.lower() == "nan")


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


def load_inventory(path: Union[Path, str]) -> Dict[str, ResistorAnchor]:
    """Read hardware/resistor_inventory.csv -> {load_id: ResistorAnchor}.

    §F.7 step 4 lists ``nominal_ohm, measured_ohm, T_C, operator,
    timestamp`` and the labelling rule (R100_01 .. R10k_10); the
    spec does not name the label column, so this loader expects
    a 'load_id' column (the canonical key used everywhere else in
    the pipeline). Per §F.7 G-DMMx promotion, ``lab_dmm_ohm`` is
    preferred when present; otherwise ``measured_ohm`` is the
    handheld fallback. Missing accuracy class -> None -> empty
    cell in the §I.6 CSV.
    """
    inv = pd.read_csv(path, dtype=str, keep_default_na=False)
    if "load_id" not in inv.columns:
        raise ValueError(
            f"{path}: inventory CSV must have a 'load_id' column "
            "(R100_01 .. R10k_10 per §F.7 step 4)"
        )
    actuals: Dict[str, ResistorAnchor] = {}
    for _, row in inv.iterrows():
        load_id = str(row["load_id"]).strip()
        if not load_id:
            continue
        lab_ohm = str(row.get("lab_dmm_ohm", "")).strip()
        if lab_ohm:
            actual_str = lab_ohm
            dmm_model = str(row.get("lab_dmm_model", "")).strip()
            acc_str = str(row.get("lab_dmm_accuracy_class_pct", "")).strip()
        else:
            actual_str = str(row.get("measured_ohm", "")).strip()
            dmm_model = str(row.get("dmm_model", "")).strip()
            acc_str = ""
        try:
            actual_ohm = float(actual_str)
        except ValueError:
            continue
        try:
            nominal_ohm = float(row.get("nominal_ohm", "") or "nan")
        except ValueError:
            nominal_ohm = actual_ohm
        try:
            acc = float(acc_str) if acc_str else None
        except ValueError:
            acc = None
        actuals[load_id] = ResistorAnchor(
            nominal_ohm=nominal_ohm,
            actual_ohm=actual_ohm,
            dmm_model=dmm_model,
            dmm_accuracy_class_pct=acc,
        )
    return actuals


def run_calibration(
    raw_path: Union[Path, str],
    inventory_path: Union[Path, str],
    output_path: Optional[Union[Path, str]] = None,
    *,
    session_id: Optional[str] = None,
    strict: bool = False,
    required_load_ids: Optional[Iterable[str]] = None,
    required_repeats: int = DEFAULT_REQUIRED_REPEATS,
) -> pd.DataFrame:
    """Read §I.5 raw + inventory; build §I.6 cal table; optionally write.

    Composes load_inventory + build_calibration_table +
    write_calibration_csv. Returns the cal DataFrame regardless
    of whether output_path is supplied; pass output_path=None to
    use the result in-memory (dashboards, notebooks, paper
    figure scripts).

    strict=True (the bench / CLI default) propagates into
    build_calibration_table and raises CalibrationStrictError on
    empty CAL, missing actuals for required loads, or
    under-replicated F.10 groups. Library callers (synthetic-
    pipeline / notebook fixtures with intentionally partial
    loads) keep the permissive default.
    """
    raw_df = pd.read_csv(raw_path, dtype=str, keep_default_na=False)
    actuals = load_inventory(inventory_path)
    cal_df = build_calibration_table(
        raw_df, actuals,
        session_id=session_id,
        strict=strict,
        required_load_ids=required_load_ids,
        required_repeats=required_repeats,
    )
    if output_path is not None:
        write_calibration_csv(cal_df, output_path)
    return cal_df
