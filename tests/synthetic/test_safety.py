"""test_safety.py -- regression tests for the Batch 1 fail-closed
safety fixes (NOT_EVALUATED verdict, required-evidence checks,
range_setting in cal contract, NaN-CV trusted-band rejection,
listener sequence safety, CLI exit-code policy).

Each test pins one specific behavior named in the Batch 1 brief
so a regression points at the rule, not at the aggregate.

Coverage:
  - Empty / missing inputs do not silently PASS.
  - G-DC3 missing NOLOAD or R470 condition -> NOT_EVALUATED.
  - G-SAT missing anchor or any primary load -> NOT_EVALUATED.
  - G-LIN no module / load / freq overlap -> NOT_EVALUATED.
  - Range 2 and Range 4 calibration rows do not pool.
  - Required-band-resistor with NaN CV -> untrusted.
  - Absolute |phase_system_deg| alone does not reject a
    frequency (the legacy criterion is removed).
  - Firmware-like JSONL with blank row_type / load_id requires
    annotation before calibration emits a row.
  - CLI gate subcommand returns nonzero for FAIL / WARN /
    NOT_EVALUATED.
  - Incomplete / error sweep does not silently produce valid
    calibration input.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from eisight_logger.calibration import (
    CAL_CSV_COLUMNS,
    CalibrationStrictError,
    ResistorAnchor,
    build_calibration_table,
)
from eisight_logger.cli import main as cli_main
from eisight_logger.gates import (
    GateVerdict,
    evaluate_g_dc3,
    evaluate_g_lin,
    evaluate_g_sat,
)
from eisight_logger.gates.g_dc3 import DC_BIAS_CSV_COLUMNS
from eisight_logger.raw_writer import RAW_CSV_COLUMNS, RawCsvWriter
from eisight_logger.serial_listener import replay_file
from eisight_logger.trusted_band import (
    TRUSTED_BAND_RESISTORS,
    evaluate_trusted_band,
)
from tests.synthetic.generate_resistor_jsonl import (
    SYNTHETIC_GF_K,
    generate_resistor_jsonl,
)


# ---------------------------------------------------------------
# Shared helpers (kept local; the module-level production helpers
# are intentionally not extended for test scaffolding).
# ---------------------------------------------------------------

_FREQS = [5000.0 + 1000.0 * i for i in range(96)]
_NOMINAL = {
    "R100_01": 100.0, "R330_01": 330.0, "R470_01": 470.0,
    "R1k_01": 1000.0, "R4k7_01": 4700.0, "R10k_01": 10000.0,
}


def _cal_row(
    *, module_id: str = "M1", load_id: str = "R1k_01",
    range_setting: str = "RANGE_4", frequency_hz: float = 10000.0,
    gain_factor: float = 1.0e-6, phase_system_deg: float = 0.0,
    repeat_cv_percent=0.1, actual_ohm: float | None = None,
) -> dict:
    base = {col: "" for col in CAL_CSV_COLUMNS}
    base.update({
        "session_id": "TEST", "module_id": module_id,
        "load_id": load_id,
        "range_setting": range_setting,
        "nominal_ohm": _NOMINAL.get(load_id, 1000.0),
        "actual_ohm": (
            actual_ohm if actual_ohm is not None
            else _NOMINAL.get(load_id, 1000.0)
        ),
        "dmm_model": "", "dmm_accuracy_class_pct": "",
        "frequency_hz": frequency_hz,
        "gain_factor": gain_factor,
        "phase_system_deg": phase_system_deg,
        "repeat_cv_percent": (
            "" if repeat_cv_percent is None else repeat_cv_percent
        ),
        "trusted_flag": "",
    })
    return base


def _full_cal_table(
    module_id: str = "M1",
    *, range_setting: str = "RANGE_4",
    gf_value: float = 1.0e-6,
    overrides: dict | None = None,
) -> pd.DataFrame:
    """All six F.10 loads at constant GF; baseline for G-SAT/G-LIN tests."""
    overrides = overrides or {}
    rows: list = []
    for load_id in ("R100_01", "R330_01", "R470_01",
                    "R1k_01", "R4k7_01", "R10k_01"):
        mult = overrides.get(load_id, 1.0)
        for f in _FREQS:
            rows.append(_cal_row(
                module_id=module_id, load_id=load_id,
                range_setting=range_setting, frequency_hz=f,
                gain_factor=gf_value * mult,
            ))
    return pd.DataFrame(rows, columns=CAL_CSV_COLUMNS)


# ---------------------------------------------------------------
# 1. Empty gate inputs do not PASS.
# ---------------------------------------------------------------


def test_g_dc3_empty_input_is_not_evaluated():
    df = pd.DataFrame(columns=DC_BIAS_CSV_COLUMNS)
    report = evaluate_g_dc3(df)
    assert report.verdict == GateVerdict.NOT_EVALUATED


def test_g_sat_empty_cal_is_not_evaluated():
    df = pd.DataFrame(columns=CAL_CSV_COLUMNS)
    report = evaluate_g_sat(df)
    assert report.verdict == GateVerdict.NOT_EVALUATED


def test_g_lin_empty_cal_is_not_evaluated():
    df = pd.DataFrame(columns=CAL_CSV_COLUMNS)
    report = evaluate_g_lin(df, df)
    assert report.verdict == GateVerdict.NOT_EVALUATED


# ---------------------------------------------------------------
# 2. G-DC3 missing required condition.
# ---------------------------------------------------------------


def test_g_dc3_missing_r470_at_gating_range_is_not_evaluated():
    df = pd.DataFrame([{
        "module_id": "M1", "range": "RANGE_4", "condition": "NOLOAD",
        "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 5.0,
        "V_DC_DIFF_mV": 5.0, "V_DD_V": 5.0,
        "date": "2026-04-29", "operator": "T",
    }], columns=DC_BIAS_CSV_COLUMNS)
    report = evaluate_g_dc3(df)
    assert report.verdict == GateVerdict.NOT_EVALUATED


def test_g_dc3_missing_noload_at_gating_range_is_not_evaluated():
    df = pd.DataFrame([{
        "module_id": "M1", "range": "RANGE_4", "condition": "R470",
        "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 5.0,
        "V_DC_DIFF_mV": 5.0, "V_DD_V": 5.0,
        "date": "2026-04-29", "operator": "T",
    }], columns=DC_BIAS_CSV_COLUMNS)
    report = evaluate_g_dc3(df)
    assert report.verdict == GateVerdict.NOT_EVALUATED


# ---------------------------------------------------------------
# 3. G-SAT missing anchor or primary loads.
# ---------------------------------------------------------------


def test_g_sat_missing_anchor_is_not_evaluated():
    cal = _full_cal_table()
    cal = cal[cal["load_id"] != "R1k_01"]
    report = evaluate_g_sat(cal)
    assert report.verdict == GateVerdict.NOT_EVALUATED


def test_g_sat_missing_primary_load_is_not_evaluated():
    cal = _full_cal_table()
    cal = cal[cal["load_id"] != "R470_01"]
    report = evaluate_g_sat(cal)
    assert report.verdict == GateVerdict.NOT_EVALUATED


# ---------------------------------------------------------------
# 4. G-LIN no overlap.
# ---------------------------------------------------------------


def test_g_lin_no_module_overlap_is_not_evaluated():
    r4 = _full_cal_table(module_id="M_A", range_setting="RANGE_4")
    r2 = _full_cal_table(module_id="M_B", range_setting="RANGE_2")
    report = evaluate_g_lin(r4, r2)
    assert report.verdict == GateVerdict.NOT_EVALUATED


def test_g_lin_missing_required_load_in_one_range_is_not_evaluated():
    r4 = _full_cal_table()
    r2 = _full_cal_table(range_setting="RANGE_2")
    r2 = r2[r2["load_id"] != "R470_01"]
    report = evaluate_g_lin(r4, r2)
    assert report.verdict == GateVerdict.NOT_EVALUATED


def test_g_lin_no_overlapping_trusted_freq_is_not_evaluated():
    r4 = _full_cal_table()
    r2 = _full_cal_table(range_setting="RANGE_2")
    # An empty trusted-band restriction means the intersection of
    # trusted freqs with each range's freqs is empty.
    report = evaluate_g_lin(r4, r2, trusted_band_freqs=[])
    assert report.verdict == GateVerdict.NOT_EVALUATED


# ---------------------------------------------------------------
# 5. Range 2 and Range 4 calibration rows do not pool.
# ---------------------------------------------------------------


def test_calibration_does_not_pool_range_2_and_range_4(tmp_path: Path):
    # Build a raw frame containing the same module+load+freq at two
    # ranges with different (real, imag). If grouping pooled them,
    # the result would have one cal row; with range_setting in the
    # key we get two distinct rows with distinct GF.
    rows = []
    base = {col: "" for col in RAW_CSV_COLUMNS}
    base.update({
        "session_id": "TEST", "row_type": "CAL",
        "module_id": "M1", "load_id": "R1k_01",
        "frequency_hz": "10000.0", "imag": "0", "status": "2",
        "pga_setting": "X1", "settling_cycles": "15",
    })
    for sweep_id, range_setting, real in [
        ("SWP_R4_1", "RANGE_4", "1000"),
        ("SWP_R4_2", "RANGE_4", "1001"),
        ("SWP_R4_3", "RANGE_4", "999"),
        ("SWP_R2_1", "RANGE_2", "200"),
        ("SWP_R2_2", "RANGE_2", "201"),
        ("SWP_R2_3", "RANGE_2", "199"),
    ]:
        r = dict(base)
        r["sweep_id"] = sweep_id
        r["range_setting"] = range_setting
        r["real"] = real
        rows.append(r)
    raw = pd.DataFrame(rows, columns=RAW_CSV_COLUMNS)
    actuals = {"R1k_01": ResistorAnchor(nominal_ohm=1000.0, actual_ohm=1000.0)}
    cal = build_calibration_table(raw, actuals)
    # One row per range, NOT one pooled row.
    ranges = sorted(cal["range_setting"].astype(str).unique().tolist())
    assert ranges == ["RANGE_2", "RANGE_4"]
    assert len(cal) == 2
    # GFs differ between ranges (different real means different M).
    gfs = sorted(cal["gain_factor"].astype(float).round(12).unique().tolist())
    assert len(gfs) == 2


# ---------------------------------------------------------------
# 6. NaN CV does not pass trusted-band on a required band resistor.
# ---------------------------------------------------------------


def test_nan_cv_on_required_band_resistor_is_untrusted():
    # Build a cal frame at one frequency for all three required band
    # resistors. R1k_01 anchor is healthy; R470_01 has NaN CV.
    f = 10000.0
    rows = []
    for load_id in TRUSTED_BAND_RESISTORS:
        rows.append(_cal_row(
            module_id="M1", load_id=load_id,
            range_setting="RANGE_4", frequency_hz=f,
            gain_factor=1.0e-6,
            repeat_cv_percent=(None if load_id == "R470_01" else 0.1),
        ))
    cal = pd.DataFrame(rows, columns=CAL_CSV_COLUMNS)
    # Matching raw row -- presence + status + saturation must pass
    # so the NaN-CV reason is the only one left to surface.
    raw_rows = [{col: "" for col in RAW_CSV_COLUMNS} for _ in range(3)]
    for raw_row, load_id in zip(raw_rows, TRUSTED_BAND_RESISTORS):
        raw_row.update({
            "session_id": "TEST", "sweep_id": f"SWP_{load_id}",
            "row_type": "CAL", "module_id": "M1",
            "load_id": load_id, "frequency_hz": str(f),
            "real": "1000", "imag": "0", "status": "2",
            "range_setting": "RANGE_4", "pga_setting": "X1",
            "settling_cycles": "15",
        })
    raw = pd.DataFrame(raw_rows, columns=RAW_CSV_COLUMNS)
    flags = evaluate_trusted_band(cal, raw)
    row = flags.iloc[0]
    assert bool(row["trusted"]) is False
    assert "CV NaN" in row["reasons"]


# ---------------------------------------------------------------
# 7. Absolute phase_system_deg alone does not reject a frequency.
# ---------------------------------------------------------------


def test_absolute_phase_system_offset_alone_does_not_untrust_freq():
    # Same setup as the NaN-CV test but with a large constant
    # |phase_system_deg| on every load (the legacy >5deg rejection
    # would have flipped trusted to False; the new logic keeps it
    # trusted since system phase is not a residual).
    f = 10000.0
    rows = []
    for load_id in TRUSTED_BAND_RESISTORS:
        rows.append(_cal_row(
            module_id="M1", load_id=load_id,
            range_setting="RANGE_4", frequency_hz=f,
            gain_factor=1.0e-6,
            phase_system_deg=45.0,
            repeat_cv_percent=0.1,
        ))
    cal = pd.DataFrame(rows, columns=CAL_CSV_COLUMNS)
    raw_rows = []
    for load_id in TRUSTED_BAND_RESISTORS:
        r = {col: "" for col in RAW_CSV_COLUMNS}
        r.update({
            "session_id": "TEST", "sweep_id": f"SWP_{load_id}",
            "row_type": "CAL", "module_id": "M1",
            "load_id": load_id, "frequency_hz": str(f),
            "real": "1000", "imag": "0", "status": "2",
            "range_setting": "RANGE_4", "pga_setting": "X1",
            "settling_cycles": "15",
        })
        raw_rows.append(r)
    raw = pd.DataFrame(raw_rows, columns=RAW_CSV_COLUMNS)
    flags = evaluate_trusted_band(cal, raw)
    row = flags.iloc[0]
    assert bool(row["trusted"]) is True, (
        f"phase offset alone should not untrust the frequency; "
        f"reasons={row['reasons']!r}"
    )
    assert "phi_system" not in row["reasons"]


# ---------------------------------------------------------------
# 8. Firmware-like JSONL with blank row_type / load_id requires
#    annotation before calibration accepts it as evidence.
# ---------------------------------------------------------------


def test_calibration_skips_blank_row_type_load_id(tmp_path: Path):
    # Mirror the firmware's actual blank emission (row_type="",
    # load_id="") -- those rows must NOT contribute to a cal table
    # since they cannot be joined to a load identity.
    R = 1000.0
    jsonl_path = tmp_path / "blank.jsonl"
    generate_resistor_jsonl(
        R, jsonl_path,
        session_id="TEST", module_id="AD5933-A-DIRECT",
        load_id="", row_type="", sweep_id="SWP0001",
    )
    output_root = tmp_path / "data"
    replay_file(
        path=jsonl_path, session_id="TEST", output_root=output_root,
    )
    raw_csv = output_root / "TEST" / "raw.csv"
    raw = pd.read_csv(raw_csv, dtype=str, keep_default_na=False)
    actuals = {"R1k_01": ResistorAnchor(nominal_ohm=1000.0, actual_ohm=R)}
    # row_type is "" -> filter excludes everything -> empty cal in
    # permissive mode, CalibrationStrictError in strict mode.
    cal = build_calibration_table(raw, actuals, strict=False)
    assert cal.empty
    with pytest.raises(CalibrationStrictError):
        build_calibration_table(raw, actuals, strict=True)


def test_strict_calibration_raises_on_missing_required_actuals(tmp_path: Path):
    # Generate a clean CAL trace for R1k only, but call strict mode
    # which requires R330/R470/R1k/R4k7 in actuals.
    jsonl_path = tmp_path / "r1k.jsonl"
    generate_resistor_jsonl(
        1000.0, jsonl_path,
        session_id="TEST", load_id="R1k_01", row_type="CAL",
    )
    output_root = tmp_path / "data"
    replay_file(
        path=jsonl_path, session_id="TEST", output_root=output_root,
    )
    raw = pd.read_csv(
        output_root / "TEST" / "raw.csv", dtype=str, keep_default_na=False
    )
    actuals = {"R1k_01": ResistorAnchor(nominal_ohm=1000.0, actual_ohm=1000.0)}
    with pytest.raises(CalibrationStrictError):
        build_calibration_table(raw, actuals, strict=True)


# ---------------------------------------------------------------
# 9. CLI gate subcommand: nonzero for FAIL / WARN / NOT_EVALUATED.
# ---------------------------------------------------------------


def _write_cal_csv(path: Path, df: pd.DataFrame) -> None:
    df.to_csv(path, index=False)


def test_cli_gate_g_sat_returns_nonzero_for_not_evaluated(tmp_path: Path):
    cal = _full_cal_table()
    cal = cal[cal["load_id"] != "R1k_01"]  # missing anchor -> NOT_EVALUATED
    cal_path = tmp_path / "cal.csv"
    _write_cal_csv(cal_path, cal)
    rc = cli_main([
        "gate", "--type", "g_sat", "--cal", str(cal_path),
        "--output-dir", str(tmp_path / "reports"),
    ])
    assert rc == 1


def test_cli_gate_g_sat_returns_nonzero_for_fail(tmp_path: Path):
    cal = _full_cal_table(overrides={"R330_01": 1.20})
    cal_path = tmp_path / "cal.csv"
    _write_cal_csv(cal_path, cal)
    rc = cli_main([
        "gate", "--type", "g_sat", "--cal", str(cal_path),
        "--output-dir", str(tmp_path / "reports"),
    ])
    assert rc == 1


def test_cli_gate_g_dc3_returns_nonzero_for_warn(tmp_path: Path):
    rows = [
        {
            "module_id": "M1", "range": "RANGE_4", "condition": "NOLOAD",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 70.0,
            "V_DC_DIFF_mV": 70.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
        {
            "module_id": "M1", "range": "RANGE_4", "condition": "R470",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 0.0,
            "V_DC_DIFF_mV": 0.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
    ]
    csv_path = tmp_path / "dc.csv"
    pd.DataFrame(rows, columns=DC_BIAS_CSV_COLUMNS).to_csv(csv_path, index=False)
    rc = cli_main([
        "gate", "--type", "g_dc3", "--csv", str(csv_path),
        "--output-dir", str(tmp_path / "reports"),
    ])
    # Worst verdict is WARN -> nonzero per the new policy.
    assert rc == 1


def test_cli_gate_g_dc3_returns_zero_for_pass(tmp_path: Path):
    rows = [
        {
            "module_id": "M1", "range": "RANGE_4", "condition": "NOLOAD",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 5.0,
            "V_DC_DIFF_mV": 5.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
        {
            "module_id": "M1", "range": "RANGE_4", "condition": "R470",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 0.0,
            "V_DC_DIFF_mV": 0.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
    ]
    csv_path = tmp_path / "dc.csv"
    pd.DataFrame(rows, columns=DC_BIAS_CSV_COLUMNS).to_csv(csv_path, index=False)
    rc = cli_main([
        "gate", "--type", "g_dc3", "--csv", str(csv_path),
        "--output-dir", str(tmp_path / "reports"),
    ])
    assert rc == 0


# ---------------------------------------------------------------
# 10. Incomplete / error sweep does not silently produce valid
#     calibration input.
# ---------------------------------------------------------------


def test_listener_flags_sweep_end_error_and_does_not_clean_exit(tmp_path: Path):
    # Build a JSONL trace whose sweep_end carries a non-null error.
    # The listener still flushes the rows (per the §I.5
    # self-contained-CSV invariant) but tags them, and the
    # ListenerStats.is_clean() predicate flips to False.
    R = 1000.0
    M = int(round(SYNTHETIC_GF_K / R))
    sweep_id = "SWP0001"
    records = [
        {"type": "hello", "fw": "test-fw", "module_id": "AD5933-SYNTH"},
        {
            "type": "sweep_begin",
            "session_id": "TEST", "sweep_id": sweep_id,
            "module_id": "AD5933-SYNTH", "cell_id": "",
            "row_type": "CAL", "load_id": "R1k_01",
            "start_hz": 5000, "stop_hz": 100000, "points": 96,
            "range": "RANGE_4", "pga": "X1", "settling_cycles": 15,
            "ds18b20_pre_c": 25.0, "ad5933_pre_c": 31.0,
        },
    ]
    for idx, f in enumerate([5000.0 + 1000.0 * i for i in range(96)]):
        records.append({
            "type": "data", "sweep_id": sweep_id, "idx": idx,
            "frequency_hz": f, "real": M, "imag": 0, "status": 2,
        })
    records.append({
        "type": "sweep_end", "sweep_id": sweep_id,
        "ds18b20_post_c": 25.0, "ad5933_post_c": 31.0,
        "elapsed_ms": 1820, "error": "watchdog timeout",
    })
    jsonl_path = tmp_path / "errored.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")

    output_root = tmp_path / "data"
    stats = replay_file(
        path=jsonl_path, session_id="TEST", output_root=output_root,
    )
    assert stats.sweep_end_error == 1
    assert not stats.is_clean()

    # Calibration in strict mode refuses to consume rows tagged with
    # sweep_end_error= (drop_error_sweeps=True default).
    raw = pd.read_csv(
        output_root / "TEST" / "raw.csv",
        dtype=str, keep_default_na=False,
    )
    actuals = {"R1k_01": ResistorAnchor(nominal_ohm=1000.0, actual_ohm=R)}
    with pytest.raises(CalibrationStrictError):
        build_calibration_table(raw, actuals, strict=True)


def test_listener_flags_duplicate_idx(tmp_path: Path):
    # Build a JSONL trace with two data records at the same idx.
    R = 1000.0
    M = int(round(SYNTHETIC_GF_K / R))
    sweep_id = "SWP0001"
    records = [
        {"type": "hello", "fw": "test-fw", "module_id": "AD5933-SYNTH"},
        {
            "type": "sweep_begin",
            "session_id": "TEST", "sweep_id": sweep_id,
            "module_id": "AD5933-SYNTH", "cell_id": "",
            "row_type": "CAL", "load_id": "R1k_01",
            "start_hz": 5000, "stop_hz": 5000, "points": 1,
            "range": "RANGE_4", "pga": "X1", "settling_cycles": 15,
            "ds18b20_pre_c": 25.0, "ad5933_pre_c": 31.0,
        },
        {"type": "data", "sweep_id": sweep_id, "idx": 0,
         "frequency_hz": 5000.0, "real": M, "imag": 0, "status": 2},
        {"type": "data", "sweep_id": sweep_id, "idx": 0,
         "frequency_hz": 5000.0, "real": M, "imag": 0, "status": 2},
        {"type": "sweep_end", "sweep_id": sweep_id,
         "ds18b20_post_c": 25.0, "ad5933_post_c": 31.0,
         "elapsed_ms": 100, "error": None},
    ]
    jsonl_path = tmp_path / "dup.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    stats = replay_file(
        path=jsonl_path, session_id="TEST",
        output_root=tmp_path / "data",
    )
    assert stats.duplicate_idx == 1
    assert not stats.is_clean()


def test_listener_flags_missing_sweep_end(tmp_path: Path):
    # No sweep_end at all -> the open buffer is dropped at close.
    R = 1000.0
    M = int(round(SYNTHETIC_GF_K / R))
    sweep_id = "SWP0001"
    records = [
        {"type": "hello", "fw": "test-fw", "module_id": "AD5933-SYNTH"},
        {
            "type": "sweep_begin",
            "session_id": "TEST", "sweep_id": sweep_id,
            "module_id": "AD5933-SYNTH", "cell_id": "",
            "row_type": "CAL", "load_id": "R1k_01",
            "start_hz": 5000, "stop_hz": 5000, "points": 1,
            "range": "RANGE_4", "pga": "X1", "settling_cycles": 15,
            "ds18b20_pre_c": 25.0, "ad5933_pre_c": 31.0,
        },
        {"type": "data", "sweep_id": sweep_id, "idx": 0,
         "frequency_hz": 5000.0, "real": M, "imag": 0, "status": 2},
    ]
    jsonl_path = tmp_path / "nosweepend.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    stats = replay_file(
        path=jsonl_path, session_id="TEST",
        output_root=tmp_path / "data",
    )
    assert stats.missing_sweep_end == 1
    assert not stats.is_clean()


def test_listener_flags_point_count_mismatch(tmp_path: Path):
    # Declared 96 points, only 1 emitted.
    R = 1000.0
    M = int(round(SYNTHETIC_GF_K / R))
    sweep_id = "SWP0001"
    records = [
        {"type": "hello", "fw": "test-fw", "module_id": "AD5933-SYNTH"},
        {
            "type": "sweep_begin",
            "session_id": "TEST", "sweep_id": sweep_id,
            "module_id": "AD5933-SYNTH", "cell_id": "",
            "row_type": "CAL", "load_id": "R1k_01",
            "start_hz": 5000, "stop_hz": 100000, "points": 96,
            "range": "RANGE_4", "pga": "X1", "settling_cycles": 15,
            "ds18b20_pre_c": 25.0, "ad5933_pre_c": 31.0,
        },
        {"type": "data", "sweep_id": sweep_id, "idx": 0,
         "frequency_hz": 5000.0, "real": M, "imag": 0, "status": 2},
        {"type": "sweep_end", "sweep_id": sweep_id,
         "ds18b20_post_c": 25.0, "ad5933_post_c": 31.0,
         "elapsed_ms": 100, "error": None},
    ]
    jsonl_path = tmp_path / "short.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    stats = replay_file(
        path=jsonl_path, session_id="TEST",
        output_root=tmp_path / "data",
    )
    assert stats.point_count_mismatch == 1
    assert not stats.is_clean()


# ---------------------------------------------------------------
# 11. Pure-library aggregate_verdict empty=PASS contract is
#     preserved (so existing math users do not break), but the
#     gate runners enforce NOT_EVALUATED at the bench layer.
# ---------------------------------------------------------------


def test_library_aggregate_verdict_empty_still_pass():
    """Library-level pure aggregator keeps empty=PASS semantics.

    The bench-layer NOT_EVALUATED enforcement happens *above*
    aggregate_verdict (in the gate evaluators); the aggregator
    itself is a math primitive used elsewhere and changing its
    empty-iter contract would silently shift other library
    consumers.
    """
    from eisight_logger.gates.common import (
        GateVerdict as GV,
        aggregate_verdict,
    )
    assert aggregate_verdict([]) == GV.PASS


# ---------------------------------------------------------------
# Batch 2 — strict calibration: blank fields and per-(module,range)
# required loads.
# ---------------------------------------------------------------


def _raw_cal_row(
    *, sweep_id: str, module_id: str = "M1",
    load_id: str = "R1k_01", range_setting: str = "RANGE_4",
    frequency_hz: str = "10000.0", real: str = "1000",
    imag: str = "0", status: str = "2", session_id: str = "TEST",
) -> dict:
    base = {col: "" for col in RAW_CSV_COLUMNS}
    base.update({
        "session_id": session_id, "sweep_id": sweep_id, "row_type": "CAL",
        "module_id": module_id, "load_id": load_id,
        "range_setting": range_setting, "frequency_hz": frequency_hz,
        "real": real, "imag": imag, "status": status,
        "pga_setting": "X1", "settling_cycles": "15",
    })
    return base


def _full_required_actuals() -> dict:
    return {
        ld: ResistorAnchor(
            nominal_ohm=_NOMINAL[ld], actual_ohm=_NOMINAL[ld],
        )
        for ld in ("R330_01", "R470_01", "R1k_01", "R4k7_01")
    }


def _three_repeats_per_load(
    *, freq: str = "10000.0", module_id: str = "M1",
    range_setting: str = "RANGE_4",
    loads: tuple = ("R330_01", "R470_01", "R1k_01", "R4k7_01"),
) -> list:
    """Build a minimal raw frame: 3 sweeps for each required load."""
    rows: list = []
    for ld in loads:
        for k in range(3):
            rows.append(_raw_cal_row(
                sweep_id=f"SWP_{ld}_{k}", module_id=module_id,
                load_id=ld, range_setting=range_setting, frequency_hz=freq,
            ))
    return rows


def test_strict_calibration_rejects_blank_module_id():
    rows = _three_repeats_per_load()
    rows[0]["module_id"] = ""  # poison one row
    raw = pd.DataFrame(rows, columns=RAW_CSV_COLUMNS)
    with pytest.raises(CalibrationStrictError, match="blank/null 'module_id'"):
        build_calibration_table(raw, _full_required_actuals(), strict=True)


def test_strict_calibration_rejects_blank_load_id():
    rows = _three_repeats_per_load()
    rows[0]["load_id"] = ""
    raw = pd.DataFrame(rows, columns=RAW_CSV_COLUMNS)
    with pytest.raises(CalibrationStrictError, match="blank/null 'load_id'"):
        build_calibration_table(raw, _full_required_actuals(), strict=True)


def test_strict_calibration_rejects_blank_range_setting():
    rows = _three_repeats_per_load()
    rows[0]["range_setting"] = ""
    raw = pd.DataFrame(rows, columns=RAW_CSV_COLUMNS)
    with pytest.raises(CalibrationStrictError, match="blank/null 'range_setting'"):
        build_calibration_table(raw, _full_required_actuals(), strict=True)


def test_strict_calibration_rejects_blank_frequency_hz():
    rows = _three_repeats_per_load()
    rows[0]["frequency_hz"] = ""
    raw = pd.DataFrame(rows, columns=RAW_CSV_COLUMNS)
    with pytest.raises(CalibrationStrictError, match="blank/null 'frequency_hz'"):
        build_calibration_table(raw, _full_required_actuals(), strict=True)


def test_strict_calibration_rejects_unknown_load_id():
    # All 4 required loads are present + 3 reps; an extra load_id
    # ('R999_01') is not in inventory -- strict mode rejects.
    rows = _three_repeats_per_load() + [
        _raw_cal_row(sweep_id="SWP_R999", load_id="R999_01"),
        _raw_cal_row(sweep_id="SWP_R999_b", load_id="R999_01"),
        _raw_cal_row(sweep_id="SWP_R999_c", load_id="R999_01"),
    ]
    raw = pd.DataFrame(rows, columns=RAW_CSV_COLUMNS)
    with pytest.raises(CalibrationStrictError, match="not in resistor inventory"):
        build_calibration_table(raw, _full_required_actuals(), strict=True)


def test_strict_calibration_rejects_per_module_missing_required_load():
    # M1 has R1k+R330+R470 but not R4k7 at RANGE_4. Inventory covers
    # all four. The per-(module, range) completeness check fires.
    rows = _three_repeats_per_load(
        loads=("R330_01", "R470_01", "R1k_01"),
    )
    raw = pd.DataFrame(rows, columns=RAW_CSV_COLUMNS)
    with pytest.raises(
        CalibrationStrictError,
        match=r"required F.10 load\(s\) missing per",
    ):
        build_calibration_table(raw, _full_required_actuals(), strict=True)


def test_strict_calibration_rejects_per_module_missing_at_one_range_only():
    # M1 has all 4 loads at RANGE_4 but only R1k at RANGE_2 -> the
    # (M1, RANGE_2) row of the per-(module, range) check fires.
    rows = _three_repeats_per_load(range_setting="RANGE_4") + \
        _three_repeats_per_load(range_setting="RANGE_2", loads=("R1k_01",))
    raw = pd.DataFrame(rows, columns=RAW_CSV_COLUMNS)
    with pytest.raises(
        CalibrationStrictError,
        match=r"required F.10 load\(s\) missing per",
    ):
        build_calibration_table(raw, _full_required_actuals(), strict=True)


def test_strict_calibration_rejects_empty_final_cal_table():
    # All blanks/required pass; every CAL row has real=imag=0 so
    # m_pooled is 0 for every group -> out_rows stays empty -> the
    # tail strict check raises rather than returning an empty table.
    rows = _three_repeats_per_load()
    for r in rows:
        r["real"] = "0"
        r["imag"] = "0"
    raw = pd.DataFrame(rows, columns=RAW_CSV_COLUMNS)
    with pytest.raises(
        CalibrationStrictError, match="final calibration table is empty",
    ):
        build_calibration_table(raw, _full_required_actuals(), strict=True)


def test_strict_calibration_accepts_clean_complete_input():
    # Positive control: complete required loads x 3 repeats, all
    # cells populated, real>0 -> strict mode produces a non-empty
    # cal table without raising.
    rows = _three_repeats_per_load()
    raw = pd.DataFrame(rows, columns=RAW_CSV_COLUMNS)
    cal = build_calibration_table(raw, _full_required_actuals(), strict=True)
    assert not cal.empty
    assert sorted(cal["load_id"].astype(str).unique()) == [
        "R1k_01", "R330_01", "R470_01", "R4k7_01",
    ]


# ---------------------------------------------------------------
# Batch 2 — G-DC3 multi-module fail-closed.
# ---------------------------------------------------------------


def test_g_dc3_module_with_only_range2_is_not_evaluated():
    df = pd.DataFrame([{
        "module_id": "M1", "range": "RANGE_2", "condition": "NOLOAD",
        "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 5.0,
        "V_DC_DIFF_mV": 5.0, "V_DD_V": 5.0,
        "date": "2026-04-29", "operator": "T",
    }], columns=DC_BIAS_CSV_COLUMNS)
    report = evaluate_g_dc3(df)
    assert report.verdict == GateVerdict.NOT_EVALUATED
    assert "M1" in report.details["modules_not_evaluated_at_gating_range"]


def test_g_dc3_one_module_passing_other_only_range2_is_not_evaluated():
    # M1: complete RANGE_4 NOLOAD+R470 (PASS).
    # M2: only RANGE_2 row -> NOT_EVALUATED at module level.
    # Overall must NOT be PASS even though M1 passed.
    df = pd.DataFrame([
        {
            "module_id": "M1", "range": "RANGE_4", "condition": "NOLOAD",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 5.0,
            "V_DC_DIFF_mV": 5.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
        {
            "module_id": "M1", "range": "RANGE_4", "condition": "R470",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 0.0,
            "V_DC_DIFF_mV": 0.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
        {
            "module_id": "M2", "range": "RANGE_2", "condition": "NOLOAD",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 5.0,
            "V_DC_DIFF_mV": 5.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
    ], columns=DC_BIAS_CSV_COLUMNS)
    report = evaluate_g_dc3(df)
    assert report.verdict == GateVerdict.NOT_EVALUATED
    assert "M2" in report.details["modules_not_evaluated_at_gating_range"]
    assert "M1" in report.details["modules_evaluated_at_gating_range"]


def test_g_dc3_two_modules_both_pass_overall_pass():
    df = pd.DataFrame([
        {
            "module_id": "M1", "range": "RANGE_4", "condition": "NOLOAD",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 5.0,
            "V_DC_DIFF_mV": 5.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
        {
            "module_id": "M1", "range": "RANGE_4", "condition": "R470",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 0.0,
            "V_DC_DIFF_mV": 0.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
        {
            "module_id": "M2", "range": "RANGE_4", "condition": "NOLOAD",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 5.0,
            "V_DC_DIFF_mV": 5.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
        {
            "module_id": "M2", "range": "RANGE_4", "condition": "R470",
            "V_DC_P1_GND_mV": 0.0, "V_DC_P2_GND_mV": 0.0,
            "V_DC_DIFF_mV": 0.0, "V_DD_V": 5.0,
            "date": "2026-04-29", "operator": "T",
        },
    ], columns=DC_BIAS_CSV_COLUMNS)
    report = evaluate_g_dc3(df)
    assert report.verdict == GateVerdict.PASS


# ---------------------------------------------------------------
# Batch 2 — G-LIN multi-module + per-module trusted frequencies.
# ---------------------------------------------------------------


def test_g_lin_module_present_in_only_one_range_is_not_evaluated():
    # Both M_A and M_B exist on RANGE_4; only M_A on RANGE_2.
    # Module universe = {M_A, M_B}; M_B has no Range-2 cal -> NE.
    r4 = pd.concat([
        _full_cal_table(module_id="M_A", range_setting="RANGE_4"),
        _full_cal_table(module_id="M_B", range_setting="RANGE_4"),
    ], ignore_index=True)
    r2 = _full_cal_table(module_id="M_A", range_setting="RANGE_2")
    report = evaluate_g_lin(r4, r2)
    assert report.verdict == GateVerdict.NOT_EVALUATED
    not_eval_ids = [d["module_id"] for d in report.details["modules_not_evaluated"]]
    assert "M_B" in not_eval_ids


def test_g_lin_does_not_use_module_a_trusted_freqs_for_module_b():
    # Both modules in both ranges. M_A is trusted only at _FREQS[0];
    # M_B is trusted only at _FREQS[1]. With the legacy global-set
    # behavior, M_A's trusted freq would have authorized M_B -- the
    # per-module isolation prevents that. M_B is restricted to its
    # own one trusted freq (which is in both ranges' grid).
    r4 = pd.concat([
        _full_cal_table(module_id="M_A", range_setting="RANGE_4"),
        _full_cal_table(module_id="M_B", range_setting="RANGE_4"),
    ], ignore_index=True)
    r2 = pd.concat([
        _full_cal_table(module_id="M_A", range_setting="RANGE_2"),
        _full_cal_table(module_id="M_B", range_setting="RANGE_2"),
    ], ignore_index=True)
    per_module = {
        "M_A": {_FREQS[0]},
        "M_B": {_FREQS[1]},
    }
    report = evaluate_g_lin(r4, r2, trusted_band_freqs=per_module)
    assert report.verdict == GateVerdict.PASS
    # Each module evaluated only at its OWN trusted frequency.
    per_item = report.per_item
    a_freqs = sorted(per_item.loc[per_item["module_id"] == "M_A", "frequency_hz"].tolist())
    b_freqs = sorted(per_item.loc[per_item["module_id"] == "M_B", "frequency_hz"].tolist())
    assert a_freqs == [_FREQS[0]]
    assert b_freqs == [_FREQS[1]]


def test_g_lin_module_with_no_per_module_trusted_entry_is_not_evaluated():
    # Per-module mapping has only M_A; M_B is in both ranges' cals
    # but has no entry in the trusted dict. Per-module isolation
    # treats 'no entry' as 'no trusted freq for this module',
    # NOT as 'unrestricted'. M_B -> NOT_EVALUATED at module level;
    # overall flips to NOT_EVALUATED.
    r4 = pd.concat([
        _full_cal_table(module_id="M_A", range_setting="RANGE_4"),
        _full_cal_table(module_id="M_B", range_setting="RANGE_4"),
    ], ignore_index=True)
    r2 = pd.concat([
        _full_cal_table(module_id="M_A", range_setting="RANGE_2"),
        _full_cal_table(module_id="M_B", range_setting="RANGE_2"),
    ], ignore_index=True)
    per_module = {"M_A": {_FREQS[0]}}
    report = evaluate_g_lin(r4, r2, trusted_band_freqs=per_module)
    assert report.verdict == GateVerdict.NOT_EVALUATED
    not_eval_ids = [d["module_id"] for d in report.details["modules_not_evaluated"]]
    assert "M_B" in not_eval_ids


def test_g_lin_trusted_band_csv_without_module_id_fails_closed(tmp_path: Path):
    # A trusted-band CSV that lacks module_id cannot be safely
    # consumed under per-module isolation -- _trusted_freqs_from_csv
    # must raise rather than fall back to a global set.
    from eisight_logger.gates.g_lin import _trusted_freqs_from_csv
    df = pd.DataFrame({
        "frequency_hz": [1000.0, 2000.0],
        "trusted_flag": ["True", "True"],
    })
    p = tmp_path / "no_module.csv"
    df.to_csv(p, index=False)
    with pytest.raises(ValueError, match="lacks 'module_id'"):
        _trusted_freqs_from_csv(p)


# ---------------------------------------------------------------
# Batch 2 — listener sequence safety: non-monotonic idx and
# mismatched sweep_id.
# ---------------------------------------------------------------


def _emit_jsonl(path: Path, records: list) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return path


def _sweep_begin_record(
    sweep_id: str, points: int = 3, module_id: str = "AD5933-SYNTH",
    load_id: str = "R1k_01", row_type: str = "CAL",
) -> dict:
    return {
        "type": "sweep_begin",
        "session_id": "TEST", "sweep_id": sweep_id,
        "module_id": module_id, "cell_id": "",
        "row_type": row_type, "load_id": load_id,
        "start_hz": 5000, "stop_hz": 7000, "points": points,
        "range": "RANGE_4", "pga": "X1", "settling_cycles": 15,
        "ds18b20_pre_c": 25.0, "ad5933_pre_c": 31.0,
    }


def _sweep_end_record(sweep_id: str) -> dict:
    return {
        "type": "sweep_end", "sweep_id": sweep_id,
        "ds18b20_post_c": 25.0, "ad5933_post_c": 31.0,
        "elapsed_ms": 100, "error": None,
    }


def test_listener_flags_nonmonotonic_idx_regression(tmp_path: Path):
    R = 1000.0
    M = int(round(SYNTHETIC_GF_K / R))
    sweep_id = "SWP_NM"
    records = [
        {"type": "hello", "fw": "test-fw", "module_id": "AD5933-SYNTH"},
        _sweep_begin_record(sweep_id, points=3),
        # idx sequence 0, 2, 1 -- the 1 is non-monotonic (1 <= 2).
        {"type": "data", "sweep_id": sweep_id, "idx": 0,
         "frequency_hz": 5000.0, "real": M, "imag": 0, "status": 2},
        {"type": "data", "sweep_id": sweep_id, "idx": 2,
         "frequency_hz": 7000.0, "real": M, "imag": 0, "status": 2},
        {"type": "data", "sweep_id": sweep_id, "idx": 1,
         "frequency_hz": 6000.0, "real": M, "imag": 0, "status": 2},
        _sweep_end_record(sweep_id),
    ]
    jsonl = _emit_jsonl(tmp_path / "nonmono.jsonl", records)
    output_root = tmp_path / "data"
    stats = replay_file(
        path=jsonl, session_id="TEST", output_root=output_root,
    )
    assert stats.nonmonotonic_idx == 1
    assert stats.duplicate_idx == 0
    assert not stats.is_clean()
    # The dropped non-monotonic record must NOT appear in raw.csv,
    # so calibration-ready rows for sweep_id reflect only idx 0 + 2.
    raw = pd.read_csv(
        output_root / "TEST" / "raw.csv",
        dtype=str, keep_default_na=False,
    )
    sweep_rows = raw[raw["sweep_id"] == sweep_id]
    assert len(sweep_rows) == 2  # idx 1 dropped
    # Listener exit-code policy: non-clean stats -> nonzero CLI exit
    # via _cmd_listen, so a downstream calibration cannot mistake a
    # corrupted sweep for valid evidence.
    rc = cli_main([
        "listen", "--session-id", "TEST_RC",
        "--replay", str(jsonl),
        "--output-root", str(tmp_path / "rc_out"),
    ])
    assert rc == 1


def test_listener_flags_mismatched_sweep_id_regression(tmp_path: Path):
    R = 1000.0
    M = int(round(SYNTHETIC_GF_K / R))
    open_id = "SWP_OPEN"
    other_id = "SWP_OTHER"
    records = [
        {"type": "hello", "fw": "test-fw", "module_id": "AD5933-SYNTH"},
        _sweep_begin_record(open_id, points=2),
        {"type": "data", "sweep_id": open_id, "idx": 0,
         "frequency_hz": 5000.0, "real": M, "imag": 0, "status": 2},
        # Mismatched sweep_id -- must not enter the open buffer.
        {"type": "data", "sweep_id": other_id, "idx": 0,
         "frequency_hz": 5000.0, "real": M, "imag": 0, "status": 2},
        {"type": "data", "sweep_id": open_id, "idx": 1,
         "frequency_hz": 6000.0, "real": M, "imag": 0, "status": 2},
        # Stray sweep_end that does not match the open sweep id.
        _sweep_end_record(other_id),
        # Real sweep_end for the open sweep.
        _sweep_end_record(open_id),
    ]
    jsonl = _emit_jsonl(tmp_path / "mismatch.jsonl", records)
    output_root = tmp_path / "data"
    stats = replay_file(
        path=jsonl, session_id="TEST", output_root=output_root,
    )
    # One mismatched data record + one stray sweep_end.
    assert stats.mismatched_sweep_id == 2
    assert not stats.is_clean()
    raw = pd.read_csv(
        output_root / "TEST" / "raw.csv",
        dtype=str, keep_default_na=False,
    )
    # Only the open sweep's rows should be on disk; the other_id
    # data record was dropped, never landed in any sweep buffer.
    assert (raw["sweep_id"] == open_id).all()
    assert other_id not in set(raw["sweep_id"].tolist())
    rc = cli_main([
        "listen", "--session-id", "TEST_RC2",
        "--replay", str(jsonl),
        "--output-root", str(tmp_path / "rc_out"),
    ])
    assert rc == 1
