"""test_gates.py -- §E.11 / §F.10.a / §F.10.b gate evaluators on synthetic input.

Builds synthetic §I.6 cal tables and §F.6 dc_bias tables and
asserts each gate's PASS / WARN / FAIL outcomes match the spec
thresholds.

Coverage:
  - G-DC3: tri-state per §E.11 (<50 mV PASS, 50-100 WARN,
    >=100 FAIL) on the gating Range 4.
  - G-SAT: clean synthetic cal -> PASS; deliberately-bumped
    primary load residual > 5% -> FAIL. Also exercises the
    failures_output CSV sidecar (cross-module schema with
    trusted_band.py).
  - G-LIN: matched Range 4 / Range 2 cal tables -> PASS;
    forced 5% divergence -> FAIL.
  - Runner contract: run_g_dc3 writes both .txt and .json
    artifacts under output_dir when fmt='both'.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from eisight_logger.calibration import CAL_CSV_COLUMNS
from eisight_logger.gates import (
    GateVerdict,
    evaluate_g_dc3,
    evaluate_g_lin,
    evaluate_g_sat,
    run_g_dc3,
    run_g_lin,
    run_g_sat,
)
from eisight_logger.gates.g_dc3 import DC_BIAS_CSV_COLUMNS
from eisight_logger.gates.g_sat import G_SAT_FAILURE_COLUMNS

_FREQS = [5000.0 + 1000.0 * i for i in range(96)]
_CAL_LOADS = ("R100_01", "R330_01", "R470_01", "R1k_01", "R4k7_01", "R10k_01")
_NOMINAL = {
    "R100_01": 100.0, "R330_01": 330.0, "R470_01": 470.0,
    "R1k_01": 1000.0, "R4k7_01": 4700.0, "R10k_01": 10000.0,
}


def _cal_table(
    module_id: str = "AD5933-A-DIRECT",
    *,
    gf_value: float = 1.0e-6,
    overrides: dict | None = None,
) -> pd.DataFrame:
    """Build a §I.6 cal table with constant GF across all loads/freqs.

    overrides maps load_id -> per-load GF multiplier (default 1.0
    means use gf_value as-is). A load not in overrides uses
    gf_value directly. cv_percent is 0.1 (passes the 1% trusted-
    band cv ceiling), phase_system_deg is 0.0 (ideal resistor).
    """
    overrides = overrides or {}
    rows: list[dict] = []
    for load_id in _CAL_LOADS:
        mult = overrides.get(load_id, 1.0)
        for f in _FREQS:
            rows.append({
                "session_id": "TEST", "module_id": module_id,
                "load_id": load_id, "nominal_ohm": _NOMINAL[load_id],
                "actual_ohm": _NOMINAL[load_id], "dmm_model": "",
                "dmm_accuracy_class_pct": "",
                "frequency_hz": f, "gain_factor": gf_value * mult,
                "phase_system_deg": 0.0, "repeat_cv_percent": 0.1,
                "trusted_flag": "",
            })
    return pd.DataFrame(rows, columns=CAL_CSV_COLUMNS)


def _dc_bias_csv(tmp_path: Path, rows: list[dict]) -> Path:
    """Write a §F.6 dc_bias_check.csv to tmp_path; return the path."""
    df = pd.DataFrame(rows, columns=DC_BIAS_CSV_COLUMNS)
    out = tmp_path / "dc_bias_check.csv"
    df.to_csv(out, index=False)
    return out


# ---------------------------------------------------------------
# G-DC3 tri-state.
# ---------------------------------------------------------------


@pytest.mark.parametrize("vdc_mv,expected", [
    (30.0, GateVerdict.PASS),
    (70.0, GateVerdict.WARN),
    (150.0, GateVerdict.FAIL),
])
def test_g_dc3_pass_warn_fail_thresholds(vdc_mv, expected):
    df = pd.DataFrame([{
        "module_id": "AD5933-A-DIRECT", "range": "RANGE_4",
        "condition": "NOLOAD",
        "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": vdc_mv,
        "V_DC_DIFF_mV": vdc_mv, "V_DD_V": 5.0,
        "date": "2026-04-29", "operator": "T",
    }], columns=DC_BIAS_CSV_COLUMNS)
    report = evaluate_g_dc3(df)
    assert report.verdict == expected


def test_g_dc3_module_fail_if_any_gating_row_fails():
    # Module is FAIL if any gating-range row is FAIL, even when
    # other gating-range rows PASS (per §E.11's primary criterion).
    df = pd.DataFrame([
        {
            "module_id": "M1", "range": "RANGE_4", "condition": "NOLOAD",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 30.0,
            "V_DC_DIFF_mV": 30.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
        {
            "module_id": "M1", "range": "RANGE_4", "condition": "R470",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 150.0,
            "V_DC_DIFF_mV": 150.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
    ], columns=DC_BIAS_CSV_COLUMNS)
    report = evaluate_g_dc3(df)
    assert report.verdict == GateVerdict.FAIL


def test_g_dc3_non_gating_range_does_not_promote_module_verdict():
    # §E.11 step 8: only Range 4 gates; Range 2 / 1 rows are
    # informational. A fail at Range 2 with PASS at Range 4 -> PASS.
    df = pd.DataFrame([
        {
            "module_id": "M1", "range": "RANGE_4", "condition": "NOLOAD",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 30.0,
            "V_DC_DIFF_mV": 30.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
        {
            "module_id": "M1", "range": "RANGE_2", "condition": "NOLOAD",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 200.0,
            "V_DC_DIFF_mV": 200.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
    ], columns=DC_BIAS_CSV_COLUMNS)
    report = evaluate_g_dc3(df)
    assert report.verdict == GateVerdict.PASS


# ---------------------------------------------------------------
# G-SAT.
# ---------------------------------------------------------------


def test_g_sat_passes_on_clean_synthetic_cal():
    cal = _cal_table()
    report = evaluate_g_sat(cal)
    assert report.verdict == GateVerdict.PASS


def test_g_sat_fails_on_deliberately_bad_primary_residual():
    # 20% offset on R330 -> residual 20% > 5% threshold across
    # the entire band -> contiguous-pass-band-width = 0 -> FAIL.
    cal = _cal_table(overrides={"R330_01": 1.20})
    report = evaluate_g_sat(cal)
    assert report.verdict == GateVerdict.FAIL


def test_g_sat_informational_load_does_not_promote_failure():
    # Per §F.10.a step 4: R100 / R10k are informational; failures
    # there do not promote the module verdict.
    cal = _cal_table(overrides={"R100_01": 1.50, "R10k_01": 1.50})
    report = evaluate_g_sat(cal)
    assert report.verdict == GateVerdict.PASS


# ---------------------------------------------------------------
# G-LIN.
# ---------------------------------------------------------------


def test_g_lin_passes_when_ranges_agree():
    r4 = _cal_table()
    r2 = _cal_table()
    report = evaluate_g_lin(r4, r2)
    assert report.verdict == GateVerdict.PASS


def test_g_lin_fails_when_ranges_diverge():
    # Bump R470 GF by 5% on Range 2 only; |Z|_R2 = R * GF_R2 / GF_anchor
    # differs from |Z|_R4 by ~5% > 2% threshold -> FAIL.
    r4 = _cal_table()
    r2 = _cal_table(overrides={"R470_01": 1.05})
    report = evaluate_g_lin(r4, r2)
    assert report.verdict == GateVerdict.FAIL


def test_g_lin_trusted_band_freqs_restricts_evaluation():
    # Force a single bad frequency on R2; without restriction -> FAIL.
    r4 = _cal_table()
    r2 = _cal_table()
    # Bump only the first frequency on R2 R470 by 5%.
    mask = (r2["load_id"] == "R470_01") & (r2["frequency_hz"] == _FREQS[0])
    r2.loc[mask, "gain_factor"] = float(r2.loc[mask, "gain_factor"].iloc[0]) * 1.05

    full = evaluate_g_lin(r4, r2)
    assert full.verdict == GateVerdict.FAIL

    # Restrict to frequencies that exclude the bad one -> PASS.
    restricted = evaluate_g_lin(r4, r2, trusted_band_freqs=_FREQS[1:])
    assert restricted.verdict == GateVerdict.PASS


# ---------------------------------------------------------------
# Runner contract.
# ---------------------------------------------------------------


def test_run_g_dc3_writes_both_text_and_json(tmp_path: Path):
    csv_path = _dc_bias_csv(tmp_path, [{
        "module_id": "M1", "range": "RANGE_4", "condition": "NOLOAD",
        "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 30.0,
        "V_DC_DIFF_mV": 30.0, "V_DD_V": 5.0,
        "date": "2026-04-29", "operator": "T",
    }])
    out_dir = tmp_path / "reports"
    report = run_g_dc3(csv_path, out_dir, fmt="both")
    assert report.verdict == GateVerdict.PASS
    assert (out_dir / "g_dc3.txt").is_file()
    assert (out_dir / "g_dc3.json").is_file()


def test_run_g_dc3_returns_report_when_no_output_dir(tmp_path: Path):
    csv_path = _dc_bias_csv(tmp_path, [{
        "module_id": "M1", "range": "RANGE_4", "condition": "NOLOAD",
        "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 30.0,
        "V_DC_DIFF_mV": 30.0, "V_DD_V": 5.0,
        "date": "2026-04-29", "operator": "T",
    }])
    report = run_g_dc3(csv_path)
    assert report.verdict == GateVerdict.PASS


def test_run_g_sat_writes_failures_csv_with_locked_columns(tmp_path: Path):
    cal = _cal_table(overrides={"R330_01": 1.20})
    cal_path = tmp_path / "cal.csv"
    cal.to_csv(cal_path, index=False)
    failures_path = tmp_path / "g_sat_failures.csv"
    run_g_sat(
        cal_path, output_dir=tmp_path / "reports",
        failures_output=failures_path,
    )
    assert failures_path.is_file()
    failures = pd.read_csv(failures_path)
    assert list(failures.columns) == G_SAT_FAILURE_COLUMNS
    # Every failure row is on R330 (the only deliberately-bumped load).
    assert (failures["load_id"] == "R330_01").all()


def test_run_g_lin_via_trusted_band_csv(tmp_path: Path):
    """run_g_lin reads the merged §I.5/§I.6 CSV's trusted_flag=='True' rows."""
    r4 = _cal_table()
    r2 = _cal_table()
    r4_path = tmp_path / "cal_r4.csv"
    r2_path = tmp_path / "cal_r2.csv"
    r4.to_csv(r4_path, index=False)
    r2.to_csv(r2_path, index=False)

    # Build a merged-style CSV that marks every frequency 'True'.
    merged = r4.copy()
    merged["trusted_flag"] = "True"
    tb_path = tmp_path / "trusted_band.csv"
    merged.to_csv(tb_path, index=False)

    report = run_g_lin(r4_path, r2_path, trusted_band_csv=tb_path)
    assert report.verdict == GateVerdict.PASS
