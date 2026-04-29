"""g_sat.py -- §F.10.a saturation/clipping gate (G-SAT) evaluator.

G-SAT detects AD5933 receive-stage clipping by checking that
the calibrated impedance of each non-anchor resistor matches
its lab-DMM value across a contiguous trusted band. A clipped
sinusoid produces reduced fundamental DFT magnitude, so |Z|_meas
reads HIGH for the smallest resistors -- a monotonic upward
residual as R decreases is the saturation fingerprint
(§F.10.a step 5).

Math (§H.2 identity, the same one trusted_band.py uses for its
magnitude-residual check):

  GF_X(f)        = 1 / (R_X_actual * M_X(f))
  |Z_X|(f)       = R_X_actual * GF_X(f) / GF_anchor(f)
  epsilon_R(f)   = GF_X(f) / GF_anchor(f) - 1     (signed)

|epsilon_R| in percent collapses to (GF_X - GF_anchor) /
GF_anchor in absolute value -- bit-identical to
trusted_band._check_mag_residual. G-SAT is the gate verdict;
trusted_band is the per-frequency band membership; they agree
by construction.

Per-(module, primary-load) verdict:

  PASS: there exists a contiguous run of frequencies where
        |epsilon_R(f)| <= pass_threshold (default 5 %) spanning
        at least min_band_width (default 20 kHz) of frequency.
  FAIL: no such band.

§F.10.a does not define a warning band, so G-SAT is binary
(PASS / FAIL) under the GateVerdict tri-state.

Module verdict: aggregate_verdict over the primary-load
verdicts.

R100 and R10k are 'informational' loads -- per §F.10.a step 4,
they "may show larger residuals at the band edges, especially
at low frequencies (R100 is below the AD5933's stated native
range)". They are logged in the per-item table but do not
promote the module verdict.

g_sat_failures DataFrame: cross-module contract with
trusted_band._g_sat_freqs_for. Schema is locked in
G_SAT_FAILURE_COLUMNS so a future change is one edit, not a
silent divergence between this module and trusted_band.py.
build_g_sat_failures emits one row per primary-load FAIL;
informational-load failures are not included (the spec gates
only on primaries).

Implements: §F.10.a (G-SAT). Consumes: §I.6 calibration CSV.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from eisight_logger.gates.common import (
    GateReport,
    GateVerdict,
    aggregate_verdict,
    write_report_artifacts,
)

# §F.10.a thresholds.
G_SAT_PASS_THRESHOLD_PCT = 5.0
G_SAT_MIN_BAND_WIDTH_HZ = 20_000.0

# §F.10.a primary loads (gating) and informational loads (logged
# only). F.10 anchors on R1k.
G_SAT_PRIMARY_LOADS: Tuple[str, ...] = ("R330_01", "R470_01", "R4k7_01")
G_SAT_INFORMATIONAL_LOADS: Tuple[str, ...] = ("R100_01", "R10k_01")
G_SAT_ANCHOR_LOAD_ID = "R1k_01"

# Cross-module schema contract with trusted_band._g_sat_freqs_for.
# Defined here so a column change is one edit; trusted_band.py
# carries a comment pointer back to this constant.
G_SAT_FAILURE_COLUMNS: List[str] = [
    "module_id", "range_setting", "frequency_hz", "load_id",
]


def evaluate_g_sat(
    cal_df: pd.DataFrame,
    *,
    pass_threshold_pct: float = G_SAT_PASS_THRESHOLD_PCT,
    min_band_width_hz: float = G_SAT_MIN_BAND_WIDTH_HZ,
    primary_loads: Tuple[str, ...] = G_SAT_PRIMARY_LOADS,
    informational_loads: Tuple[str, ...] = G_SAT_INFORMATIONAL_LOADS,
    anchor_load_id: str = G_SAT_ANCHOR_LOAD_ID,
) -> GateReport:
    """Evaluate §F.10.a G-SAT from a §I.6 calibration table.

    cal_df must be the §I.6 calibration table (calibration.
    CAL_CSV_COLUMNS). Returns a GateReport with one per-item
    row per (module, load, frequency) triple across primary +
    informational loads, plus contiguous-band widths in
    details for each (module, primary_load) pair.

    Module verdict aggregates only primary-load verdicts; loads
    in informational_loads contribute per-item rows but do not
    affect the verdict.

    Required-evidence contract: each (module, range) present in
    the calibration table must carry the anchor load AND every
    primary load to be evaluable. Missing the anchor or any
    primary load yields range-level NOT_EVALUATED rather than
    silent PASS. Empty cal table or zero evaluable ranges yields
    overall NOT_EVALUATED.
    """
    if cal_df.empty:
        return GateReport(
            gate_id="G-SAT",
            verdict=GateVerdict.NOT_EVALUATED,
            summary=(
                "G-SAT: empty calibration table -- "
                "gate NOT_EVALUATED (unsafe-as-PASS)"
            ),
            details={
                "row_count": 0,
                "anchor_load_id": anchor_load_id,
                "primary_loads": list(primary_loads),
            },
            per_item=pd.DataFrame(),
        )

    cal = cal_df.copy()
    if "range_setting" not in cal.columns:
        cal["range_setting"] = ""
    cal["range_setting"] = cal["range_setting"].fillna("").astype(str)
    for col in ("frequency_hz", "gain_factor"):
        cal[col] = pd.to_numeric(cal[col], errors="raise")

    all_loads = tuple(primary_loads) + tuple(informational_loads)
    per_item_rows: List[dict] = []
    modules_not_evaluated: List[Tuple[str, str, List[str]]] = []
    modules_evaluated: List[Tuple[str, str]] = []

    for (module_id, range_setting), mod in cal.groupby(
        ["module_id", "range_setting"], sort=True
    ):
        loads_present = set(mod["load_id"].astype(str).unique().tolist())
        required = (anchor_load_id,) + tuple(primary_loads)
        missing = [r for r in required if r not in loads_present]
        if missing:
            modules_not_evaluated.append((
                str(module_id), str(range_setting), sorted(missing)
            ))
            continue
        modules_evaluated.append((str(module_id), str(range_setting)))

        anchor_gf = (
            mod[mod["load_id"] == anchor_load_id]
            .set_index("frequency_hz")["gain_factor"]
            .astype(float)
            .to_dict()
        )
        for load_id in all_loads:
            load_rows = (
                mod[mod["load_id"] == load_id]
                .sort_values("frequency_hz")
            )
            if load_rows.empty:
                continue
            for _, row in load_rows.iterrows():
                f = float(row["frequency_hz"])
                gf_x = float(row["gain_factor"])
                gf_anchor = anchor_gf.get(f)
                if gf_anchor is None or gf_anchor == 0.0:
                    residual_pct = float("nan")
                    verdict = GateVerdict.FAIL.value
                else:
                    residual_pct = (gf_x / float(gf_anchor) - 1.0) * 100.0
                    verdict = (
                        GateVerdict.PASS.value
                        if abs(residual_pct) <= pass_threshold_pct
                        else GateVerdict.FAIL.value
                    )
                per_item_rows.append({
                    "module_id": module_id,
                    "range_setting": range_setting,
                    "load_id": load_id,
                    "frequency_hz": f,
                    "residual_pct": residual_pct,
                    "verdict": verdict,
                    "is_primary": load_id in primary_loads,
                })

    per_item = pd.DataFrame(per_item_rows)

    if not modules_evaluated:
        # Either zero modules in cal_df or every module was missing
        # required loads; either way we have no evidence to pass on.
        return GateReport(
            gate_id="G-SAT",
            verdict=GateVerdict.NOT_EVALUATED,
            summary=(
                "G-SAT: NOT_EVALUATED -- no module carries both anchor "
                f"{anchor_load_id!r} and primary loads "
                f"{list(primary_loads)}"
            ),
            details={
                "anchor_load_id": anchor_load_id,
                "primary_loads": list(primary_loads),
                "informational_loads": list(informational_loads),
                "modules_not_evaluated": [
                    {
                        "module_id": m,
                        "range_setting": r,
                        "missing_loads": ls,
                    }
                    for m, r, ls in modules_not_evaluated
                ],
            },
            per_item=per_item,
        )

    primary_verdicts: List[str] = []
    band_widths: dict = {}
    for (module_id, range_setting, load_id), grp in per_item[
        per_item["is_primary"]
    ].groupby(
        ["module_id", "range_setting", "load_id"], sort=True
    ):
        band_hz = _max_contiguous_pass_band_hz(
            grp.sort_values("frequency_hz"), pass_threshold_pct
        )
        band_widths[
            f"{module_id}/{range_setting}/{load_id}_max_pass_band_hz"
        ] = band_hz
        primary_verdicts.append(
            GateVerdict.PASS.value
            if band_hz >= min_band_width_hz
            else GateVerdict.FAIL.value
        )

    if not primary_verdicts:
        # Defensive: every evaluable module produced zero primary-load
        # verdict rows (e.g. all primary loads had no matching anchor
        # frequency). Treat as NOT_EVALUATED rather than silent PASS.
        rolled = GateVerdict.NOT_EVALUATED
    else:
        rolled = aggregate_verdict(primary_verdicts)

    if rolled == GateVerdict.PASS and modules_not_evaluated:
        # Some modules passed, but others lacked required evidence.
        # The aggregate is still NOT_EVALUATED because not every
        # module that was supposed to ship has an actual verdict.
        overall = GateVerdict.NOT_EVALUATED
    else:
        overall = rolled

    summary = (
        f"G-SAT: {overall.value} on primary loads "
        f"{list(primary_loads)} (anchor {anchor_load_id}; "
        f"min contiguous band {min_band_width_hz/1000:g} kHz at "
        f"|epsilon_R|<={pass_threshold_pct:g}%)"
    )
    if modules_not_evaluated:
        summary += (
            f" -- modules NOT_EVALUATED: "
            f"{[f'{m}/{r}' for m, r, _ in modules_not_evaluated]}"
        )

    details = {
        "anchor_load_id": anchor_load_id,
        "primary_loads": list(primary_loads),
        "informational_loads": list(informational_loads),
        "pass_threshold_pct": pass_threshold_pct,
        "min_band_width_hz": min_band_width_hz,
        "modules_evaluated": [
            {"module_id": m, "range_setting": r}
            for m, r in sorted(modules_evaluated)
        ],
        "modules_not_evaluated": [
            {"module_id": m, "range_setting": r, "missing_loads": ls}
            for m, r, ls in modules_not_evaluated
        ],
        **band_widths,
    }

    return GateReport(
        gate_id="G-SAT",
        verdict=overall,
        summary=summary,
        details=details,
        per_item=per_item,
    )


def _max_contiguous_pass_band_hz(
    grp: pd.DataFrame, pass_threshold_pct: float
) -> float:
    """Largest (f_end - f_start) span where every frequency passes.

    A 'pass' frequency has |residual_pct| <= threshold. Spans
    are between consecutive frequency samples in grp; the band
    width counts the frequency *span* (Hz), not point count, so
    a sparse-but-wide trusted band is rewarded for actual
    coverage rather than density.
    """
    f = grp["frequency_hz"].to_numpy(dtype=np.float64)
    r = grp["residual_pct"].to_numpy(dtype=np.float64)
    passes = np.where(np.isfinite(r), np.abs(r) <= pass_threshold_pct, False)
    best = 0.0
    i = 0
    n = len(f)
    while i < n:
        if not passes[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and passes[j + 1]:
            j += 1
        if j > i:
            best = max(best, float(f[j] - f[i]))
        i = j + 1
    return best


def build_g_sat_failures(
    report_or_per_item: Union[GateReport, pd.DataFrame],
    *,
    primary_only: bool = True,
) -> pd.DataFrame:
    """Extract the (module, freq, load) failures triple table.

    Accepts either a GateReport from evaluate_g_sat or its
    per_item DataFrame directly. Returns a DataFrame with
    columns G_SAT_FAILURE_COLUMNS, one row per FAIL verdict.

    primary_only (default True) restricts the result to primary-
    load failures, matching the spec's gating scope and
    trusted_band.py's consumer expectation. Set False to include
    informational-load failures as well.

    Schema is G_SAT_FAILURE_COLUMNS; consumed by
    trusted_band._g_sat_freqs_for. The range_setting column is part
    of the contract so a Range-4 G-SAT failure cannot taint Range 2.
    """
    if isinstance(report_or_per_item, GateReport):
        per_item = report_or_per_item.per_item
    else:
        per_item = report_or_per_item
    if per_item.empty:
        return pd.DataFrame(columns=G_SAT_FAILURE_COLUMNS)
    fails = per_item[per_item["verdict"] == GateVerdict.FAIL.value]
    if primary_only and "is_primary" in fails.columns:
        fails = fails[fails["is_primary"]]
    return fails[G_SAT_FAILURE_COLUMNS].reset_index(drop=True)


def run_g_sat(
    cal_path: Union[Path, str],
    output_dir: Optional[Union[Path, str]] = None,
    failures_output: Optional[Union[Path, str]] = None,
    *,
    pass_threshold_pct: float = G_SAT_PASS_THRESHOLD_PCT,
    min_band_width_hz: float = G_SAT_MIN_BAND_WIDTH_HZ,
    primary_loads: Tuple[str, ...] = G_SAT_PRIMARY_LOADS,
    informational_loads: Tuple[str, ...] = G_SAT_INFORMATIONAL_LOADS,
    anchor_load_id: str = G_SAT_ANCHOR_LOAD_ID,
    fmt: str = "both",
) -> GateReport:
    """Read §I.6 cal table; evaluate G-SAT; optionally write report + failures.

    Composes pd.read_csv + evaluate_g_sat + write_report_artifacts +
    build_g_sat_failures. Returns the GateReport regardless of
    whether output_dir is supplied; pass output_dir=None to use
    the result in-memory.

    failures_output, when supplied, writes the primary-load
    failures triple (G_SAT_FAILURE_COLUMNS) to that CSV path.
    Schema is the cross-module contract with
    trusted_band._g_sat_freqs_for; pass the same path through
    run_trusted_band's g_sat_failures_path to wire the result.
    """
    cal_df = pd.read_csv(cal_path, dtype=str, keep_default_na=False)
    report = evaluate_g_sat(
        cal_df,
        pass_threshold_pct=pass_threshold_pct,
        min_band_width_hz=min_band_width_hz,
        primary_loads=primary_loads,
        informational_loads=informational_loads,
        anchor_load_id=anchor_load_id,
    )
    if output_dir is not None:
        write_report_artifacts(report, output_dir, "g_sat", fmt=fmt)
    if failures_output is not None:
        failures_df = build_g_sat_failures(report, primary_only=True)
        out = Path(failures_output)
        out.parent.mkdir(parents=True, exist_ok=True)
        failures_df[G_SAT_FAILURE_COLUMNS].to_csv(
            out, index=False, na_rep=""
        )
    return report
