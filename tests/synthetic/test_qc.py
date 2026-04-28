"""test_qc.py -- §H.8 / §H.7 per-row QC rules on synthetic raw frames.

Builds §I.5 raw long-format DataFrames in-memory and pushes
them through qc.evaluate_qc, then asserts which rows pass and
which row carries which reason. Each test isolates one rule so
a regression points at the specific rule, not at the aggregate.

Rules covered:
  1. AD5933 STATUS D1 = 0 -> "status invalid-data flag".
  2. real/imag at +/-32767 or +/-32768 -> "saturated at int16
     endpoint".
  3. sqrt(R^2+I^2) = 0 -> "non-physical magnitude".
  4. Phase jump > 10 deg between adjacent CAL points -> both
     endpoints flagged.
  5. DS18B20 |post - pre| > 0.5 C -> every row of the sweep
     flagged.

Also covers the locked boolean encoding for run_qc on disk:
qc_pass becomes "True"/"False" (capitalized strings) per the
§I.5/§I.6 round-trip convention.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from eisight_logger.qc import (
    QC_PHASE_JUMP_DEG,
    QC_TEMP_DRIFT_C,
    evaluate_qc,
    run_qc,
)
from eisight_logger.raw_writer import RAW_CSV_COLUMNS
from eisight_logger.serial_listener import replay_file
from tests.synthetic.generate_resistor_jsonl import generate_resistor_jsonl


def _row(
    *,
    sweep_id: str = "SWP0001",
    module_id: str = "AD5933-A-DIRECT",
    load_id: str = "R1k_01",
    row_type: str = "CAL",
    frequency_hz: float = 10000.0,
    real: int = 1000,
    imag: int = 0,
    status: int = 2,
    ds18b20_pre_c: str = "25.0",
    ds18b20_post_c: str = "25.0",
) -> dict:
    """Build a §I.5 raw row dict with sane defaults; override per-test."""
    base = {col: "" for col in RAW_CSV_COLUMNS}
    base.update({
        "session_id": "TEST", "sweep_id": sweep_id,
        "row_type": row_type, "module_id": module_id,
        "load_id": load_id, "frequency_hz": str(frequency_hz),
        "real": str(real), "imag": str(imag), "status": str(status),
        "range_setting": "RANGE_4", "pga_setting": "X1",
        "settling_cycles": "15",
        "ds18b20_pre_c": ds18b20_pre_c, "ds18b20_post_c": ds18b20_post_c,
        "ad5933_pre_c": "31.0", "ad5933_post_c": "31.0",
    })
    return base


def _df(rows: list) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=RAW_CSV_COLUMNS)


def test_clean_sweep_passes():
    df = _df([_row()])
    qc = evaluate_qc(df)
    assert bool(qc.iloc[0]["qc_pass"]) is True
    assert qc.iloc[0]["qc_reasons"] == ""


def test_status_invalid_flag_fails():
    # status=0 -> D1 bit unset -> AD5933 reports invalid data.
    df = _df([_row(status=0)])
    qc = evaluate_qc(df)
    assert bool(qc.iloc[0]["qc_pass"]) is False
    assert "status invalid-data flag" in qc.iloc[0]["qc_reasons"]


@pytest.mark.parametrize("real,imag", [
    (32767, 0), (-32768, 0), (0, 32767), (0, -32768),
])
def test_saturation_at_int16_endpoint_fails(real, imag):
    df = _df([_row(real=real, imag=imag)])
    qc = evaluate_qc(df)
    assert bool(qc.iloc[0]["qc_pass"]) is False
    assert "saturated" in qc.iloc[0]["qc_reasons"]


def test_zero_magnitude_fails():
    df = _df([_row(real=0, imag=0)])
    qc = evaluate_qc(df)
    assert bool(qc.iloc[0]["qc_pass"]) is False
    assert "non-physical magnitude" in qc.iloc[0]["qc_reasons"]


def test_temp_drift_exceeded_fails_every_row_of_sweep():
    # 0.6 C drift > QC_TEMP_DRIFT_C (0.5) -> all rows of the sweep
    # are flagged via raw_writer's back-fill of pre/post on every row.
    rows = [
        _row(frequency_hz=5000.0, ds18b20_pre_c="25.0", ds18b20_post_c="25.6"),
        _row(frequency_hz=6000.0, ds18b20_pre_c="25.0", ds18b20_post_c="25.6"),
    ]
    df = _df(rows)
    qc = evaluate_qc(df)
    assert (qc["qc_pass"] == False).all()  # noqa: E712
    assert all("DS18B20 drift" in r for r in qc["qc_reasons"])


def test_temp_drift_within_threshold_passes():
    rows = [
        _row(ds18b20_pre_c="25.0", ds18b20_post_c="25.4"),
    ]
    df = _df(rows)
    qc = evaluate_qc(df)
    assert bool(qc.iloc[0]["qc_pass"]) is True


def test_phase_jump_fails_both_endpoints_on_cal_rows():
    # Two adjacent CAL points where the second's atan2 differs by
    # > 10 deg from the first. Use small real with non-zero imag to
    # produce a clean atan2 step.
    rows = [
        # idx 0: phase = atan2(0, 1000) = 0 deg
        _row(frequency_hz=5000.0, real=1000, imag=0),
        # idx 1: phase = atan2(1000, 0) = 90 deg -> jump > 10
        _row(frequency_hz=6000.0, real=0, imag=1000),
    ]
    df = _df(rows)
    qc = evaluate_qc(df)
    assert (qc["qc_pass"] == False).all()  # noqa: E712
    for r in qc["qc_reasons"]:
        assert "phase jump" in r


def test_phase_jump_only_evaluated_on_cal_rows():
    # Same large adjacency on row_type != CAL -> NOT flagged for
    # phase jump. This is the documented CAL-only design (sample
    # sweeps have legitimate frequency-dependent phase).
    rows = [
        _row(row_type="SAMPLE", frequency_hz=5000.0, real=1000, imag=0),
        _row(row_type="SAMPLE", frequency_hz=6000.0, real=0, imag=1000),
    ]
    df = _df(rows)
    qc = evaluate_qc(df)
    for r in qc["qc_reasons"]:
        assert "phase jump" not in r


def test_run_qc_writes_locked_boolean_encoding(tmp_path: Path):
    """run_qc -> raw.csv: qc_pass becomes 'True'/'False' on disk.

    The §I.5 round-trip convention: capitalized boolean strings
    when evaluated, empty when not. trusted_band.py and
    calibration.py share this; a regression here breaks every
    downstream stage that round-trips raw.csv.
    """
    jsonl_path = tmp_path / "r1k.jsonl"
    generate_resistor_jsonl(
        1000.0, jsonl_path,
        session_id="TEST", load_id="R1k_01", row_type="CAL",
    )
    output_root = tmp_path / "data"
    replay_file(
        path=jsonl_path, session_id="TEST", output_root=output_root,
    )
    raw_csv = output_root / "TEST" / "raw.csv"
    qc_csv = tmp_path / "raw_with_qc.csv"
    run_qc(raw_csv, qc_csv)
    on_disk = pd.read_csv(qc_csv, dtype=str, keep_default_na=False)
    # Synthetic data is clean -> every row 'True'.
    assert (on_disk["qc_pass"] == "True").all()
    assert (on_disk["qc_reasons"] == "").all()


def test_evaluate_qc_returns_index_aligned_to_input():
    # The runner contract for merge_qc_columns relies on index
    # alignment; a re-indexed return value would silently break
    # the merge.
    df = _df([_row(frequency_hz=5000.0), _row(frequency_hz=6000.0)])
    df.index = [42, 99]
    qc = evaluate_qc(df)
    assert list(qc.index) == [42, 99]


def test_qc_thresholds_default_to_module_constants():
    # Defensive: verifies no caller's mistake later silently
    # tightens or loosens the QC bar.
    assert QC_PHASE_JUMP_DEG == 10.0
    assert QC_TEMP_DRIFT_C == 0.5
