"""g_dc3.py -- §E.11 DC-bias gate (G-DC3) evaluator.

§E.11 logs DC bias measurements to hardware/dc_bias_check.csv
(§F.6 schema) with one row per (module_id, range, condition)
triple. The gate evaluates |V_DC(P1-P2)| against three bands:

  PASS:  < 50 mV
  WARN:  [50, 100) mV
  FAIL:  >= 100 mV

§E.11's primary pass criterion is "|V_DC(P1-P2)| < 50 mV in
BOTH the no-load and R470-loaded conditions" -- the per-row
tri-state evaluator implements this naturally: a module that
fails either condition gets FAIL via aggregate_verdict.

§E.11 step 8: "Repeat the full procedure at Range 2 and
Range 1 for completeness only AFTER the Range 4 result is
acceptable." Range 4 is therefore the gating range; rows at
Range 2 / Range 1 are reported in the per-item table for
completeness but do not promote the module verdict.

Implements: §E.11 (DC-BIAS GATE G-DC3), §F.6 (CSV schema).
Consumes: hardware/dc_bias_check.csv.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

from eisight_logger.gates.common import (
    GateReport,
    GateVerdict,
    aggregate_verdict,
    write_report_artifacts,
)

# §E.11 thresholds.
G_DC3_PASS_THRESHOLD_MV = 50.0
G_DC3_FAIL_THRESHOLD_MV = 100.0

# §E.11 step 8: Range 4 is the gating range. Operator override
# permitted; the per-item table lists every range either way.
G_DC3_GATING_RANGE = "RANGE_4"

# §E.11 primary criterion: BOTH conditions must be present at the
# gating range for a module to be evaluated. Either one alone is
# insufficient evidence -- a missing condition row is unsafe-as-PASS,
# so the module verdict is NOT_EVALUATED instead.
G_DC3_REQUIRED_CONDITIONS: List[str] = ["NOLOAD", "R470"]

# §F.6 column list for hardware/dc_bias_check.csv.
DC_BIAS_CSV_COLUMNS: List[str] = [
    "module_id", "range", "condition",
    "V_DC_P1_GND_mV", "V_DC_P2_GND_mV", "V_DC_DIFF_mV",
    "V_DD_V", "date", "operator",
]


def evaluate_g_dc3(
    df: pd.DataFrame,
    *,
    pass_threshold_mv: float = G_DC3_PASS_THRESHOLD_MV,
    fail_threshold_mv: float = G_DC3_FAIL_THRESHOLD_MV,
    gating_range: str = G_DC3_GATING_RANGE,
    required_conditions: Optional[List[str]] = None,
) -> GateReport:
    """Evaluate §E.11 DC-bias gate from a dc_bias_check.csv frame.

    df must follow DC_BIAS_CSV_COLUMNS (call load_dc_bias_csv to
    enforce). Returns a GateReport whose verdict is rolled up
    over the gating-range rows only; the per-item table covers
    every input row regardless of range.

    Per-row verdict on |V_DC_DIFF_mV|:
        |x| <  pass_threshold_mv          -> PASS
        pass_threshold <= |x| < fail_threshold -> WARN
        |x| >= fail_threshold_mv          -> FAIL

    Module verdict (the rolled-up overall) requires evidence on
    BOTH §E.11 primary criterion conditions at the gating range:
    NOLOAD AND R470. A module that is missing either condition
    row at the gating range produces NOT_EVALUATED for that
    module, and the overall verdict is NOT_EVALUATED if any
    evaluable module is short on evidence (or if no module has
    any gating-range row at all). Empty input -> NOT_EVALUATED.

    The per-item table still carries every row regardless of
    range or condition for traceability.
    """
    required = list(
        required_conditions
        if required_conditions is not None
        else G_DC3_REQUIRED_CONDITIONS
    )

    if df.empty:
        return GateReport(
            gate_id="G-DC3",
            verdict=GateVerdict.NOT_EVALUATED,
            summary=(
                "G-DC3: no DC-bias rows supplied -- "
                "gate NOT_EVALUATED (unsafe-as-PASS)"
            ),
            details={
                "row_count": 0, "gating_range": gating_range,
                "required_conditions": required,
            },
            per_item=pd.DataFrame(),
        )

    work = df.copy()
    work["V_DC_DIFF_mV"] = pd.to_numeric(
        work["V_DC_DIFF_mV"], errors="raise"
    )
    abs_diff = work["V_DC_DIFF_mV"].abs()

    def _verdict_for(v: float) -> str:
        if v < pass_threshold_mv:
            return GateVerdict.PASS.value
        if v < fail_threshold_mv:
            return GateVerdict.WARN.value
        return GateVerdict.FAIL.value

    work["abs_diff_mv"] = abs_diff
    work["verdict"] = abs_diff.map(_verdict_for)

    # Module universe = every module_id that appears ANYWHERE in the
    # input file, not just rows at the gating range. A module that
    # was logged only at RANGE_2 has zero gating-range evidence and
    # must surface as NOT_EVALUATED -- silently dropping it would
    # let an incomplete dc_bias_check.csv pass G-DC3 by omission.
    all_modules = sorted(work["module_id"].astype(str).unique().tolist())
    gating_rows = work[work["range"] == gating_range]

    # Per-module evidence + verdict roll-up. A module that is missing
    # any required condition at the gating range cannot pass; the
    # module-level verdict is NOT_EVALUATED, distinct from FAIL.
    module_verdicts: List[GateVerdict] = []
    modules_not_evaluated: List[str] = []
    modules_evaluated: List[str] = []
    for module_id in all_modules:
        mod_gating = gating_rows[
            gating_rows["module_id"].astype(str) == module_id
        ]
        present = set(mod_gating["condition"].astype(str).unique().tolist())
        missing = [c for c in required if c not in present]
        if missing or mod_gating.empty:
            modules_not_evaluated.append(module_id)
            continue
        modules_evaluated.append(module_id)
        module_verdicts.append(aggregate_verdict(mod_gating["verdict"]))

    # Severity policy: overall is PASS only when every module is PASS.
    # Otherwise FAIL beats WARN beats NOT_EVALUATED (NOT_EVALUATED is
    # a missing-evidence state, less severe than data that explicitly
    # failed but still non-pass).
    if not modules_evaluated and not modules_not_evaluated:
        overall = GateVerdict.NOT_EVALUATED
    else:
        rolled = (
            aggregate_verdict(module_verdicts)
            if module_verdicts
            else GateVerdict.NOT_EVALUATED
        )
        if rolled == GateVerdict.PASS and modules_not_evaluated:
            overall = GateVerdict.NOT_EVALUATED
        elif not module_verdicts:
            overall = GateVerdict.NOT_EVALUATED
        else:
            overall = rolled

    if not all_modules:
        summary = (
            "G-DC3: NOT_EVALUATED -- no module rows in input "
            "(empty dc_bias_check.csv)"
        )
    elif overall == GateVerdict.NOT_EVALUATED:
        summary = (
            f"G-DC3: NOT_EVALUATED -- modules lacking required "
            f"condition row(s) {required} at {gating_range}: "
            f"{sorted(modules_not_evaluated)}"
        )
    else:
        summary = (
            f"G-DC3: {overall.value} on {gating_range} "
            f"({len(gating_rows)} row(s); max |V_DC_DIFF| = "
            f"{float(gating_rows['abs_diff_mv'].max()):.2f} mV)"
        )

    details = {
        "row_count": int(len(work)),
        "gating_range": gating_range,
        "pass_threshold_mv": pass_threshold_mv,
        "fail_threshold_mv": fail_threshold_mv,
        "required_conditions": required,
        "all_modules_in_input": all_modules,
        "modules_evaluated_at_gating_range": sorted(modules_evaluated),
        "modules_not_evaluated_at_gating_range": sorted(
            modules_not_evaluated
        ),
    }
    if not gating_rows.empty:
        details["max_abs_diff_mv_at_gating_range"] = float(
            gating_rows["abs_diff_mv"].max()
        )

    per_item = work[[
        "module_id", "range", "condition",
        "V_DC_P1_GND_mV", "V_DC_P2_GND_mV", "V_DC_DIFF_mV",
        "abs_diff_mv", "verdict",
    ]].copy()

    return GateReport(
        gate_id="G-DC3",
        verdict=overall,
        summary=summary,
        details=details,
        per_item=per_item,
    )


def load_dc_bias_csv(path: Union[Path, str]) -> pd.DataFrame:
    """Load hardware/dc_bias_check.csv into a §F.6 DataFrame.

    Validates §F.6 column presence (raises on missing). Does
    not coerce types -- evaluate_g_dc3 handles numeric coercion
    on the columns it actually uses.
    """
    p = Path(path)
    df = pd.read_csv(p)
    missing = set(DC_BIAS_CSV_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(
            f"{p}: missing §F.6 columns {sorted(missing)}"
        )
    return df


def run_g_dc3(
    csv_path: Union[Path, str],
    output_dir: Optional[Union[Path, str]] = None,
    *,
    pass_threshold_mv: float = G_DC3_PASS_THRESHOLD_MV,
    fail_threshold_mv: float = G_DC3_FAIL_THRESHOLD_MV,
    gating_range: str = G_DC3_GATING_RANGE,
    fmt: str = "both",
) -> GateReport:
    """Read §F.6 dc_bias_check.csv; evaluate G-DC3; optionally write.

    Composes load_dc_bias_csv + evaluate_g_dc3 +
    write_report_artifacts. Returns the GateReport regardless
    of whether output_dir is supplied; pass output_dir=None to
    use the result in-memory (dashboards, notebooks, paper
    figure scripts). When output_dir is supplied, writes
    g_dc3.txt and/or g_dc3.json under it per fmt.
    """
    df = load_dc_bias_csv(csv_path)
    report = evaluate_g_dc3(
        df,
        pass_threshold_mv=pass_threshold_mv,
        fail_threshold_mv=fail_threshold_mv,
        gating_range=gating_range,
    )
    if output_dir is not None:
        write_report_artifacts(report, output_dir, "g_dc3", fmt=fmt)
    return report
