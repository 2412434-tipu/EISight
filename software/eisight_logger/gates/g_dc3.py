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

    Empty input or no rows at the gating range -> overall PASS
    (nothing evaluated; matches aggregate_verdict's empty-iter
    semantics).
    """
    if df.empty:
        return GateReport(
            gate_id="G-DC3",
            verdict=GateVerdict.PASS,
            summary="G-DC3: no DC-bias rows supplied -- gate not evaluated",
            details={"row_count": 0, "gating_range": gating_range},
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

    gating_rows = work[work["range"] == gating_range]
    overall = (
        aggregate_verdict(gating_rows["verdict"])
        if not gating_rows.empty
        else GateVerdict.PASS
    )

    if gating_rows.empty:
        summary = (
            f"G-DC3: no rows at gating range {gating_range!r} -- "
            f"{len(work)} non-gating row(s) logged for reference"
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
    }
    if not gating_rows.empty:
        details["max_abs_diff_mv_at_gating_range"] = float(
            gating_rows["abs_diff_mv"].max()
        )
        details["modules_evaluated_at_gating_range"] = sorted(
            gating_rows["module_id"].astype(str).unique().tolist()
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
