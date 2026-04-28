"""test_calibration.py -- §H.2 / §I.6 calibration end-to-end on synthetic data.

Drives the listener -> raw.csv -> inventory -> run_calibration
chain on an ideal-resistor JSONL trace, then verifies:

  - Gain factor recovers 1/R within 0.5%. The §H.2 identity
    GF*M = 1/R is what the rest of the pipeline relies on, so
    a regression here corrupts every calibrated |Z| downstream.
  - System phase is ~ 0 for an ideal resistor (phase = 0 in
    the fixture model -> phi_system_deg ~ 0 within numerical
    noise).
  - The §I.6 column set round-trips through write_calibration_csv
    + pd.read_csv -- column names and order survive verbatim.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

from eisight_logger.calibration import (
    CAL_CSV_COLUMNS,
    run_calibration,
    write_calibration_csv,
)
from eisight_logger.phase import dft_magnitude
from eisight_logger.serial_listener import replay_file
from tests.synthetic.generate_resistor_jsonl import generate_resistor_jsonl


@pytest.fixture
def synthetic_session(tmp_path: Path):
    """Build a complete synthetic session: jsonl -> raw.csv -> inventory.

    Returns (raw_csv_path, inventory_csv_path) ready for
    run_calibration.
    """
    R = 1000.0
    jsonl_path = tmp_path / "r1k.jsonl"
    generate_resistor_jsonl(
        R, jsonl_path,
        session_id="TEST", module_id="AD5933-A-DIRECT",
        load_id="R1k_01", row_type="CAL", sweep_id="SWP0001",
    )
    output_root = tmp_path / "data"
    replay_file(
        path=jsonl_path, session_id="TEST",
        output_root=output_root, operator="tester",
    )
    raw_csv = output_root / "TEST" / "raw.csv"
    assert raw_csv.is_file()

    inv_csv = tmp_path / "resistor_inventory.csv"
    with inv_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["load_id", "nominal_ohm", "measured_ohm"])
        w.writerow(["R1k_01", "1000", str(R)])

    return raw_csv, inv_csv


def test_gain_factor_recovers_one_over_r(synthetic_session):
    """1/(GF*M) ~ R within 0.5% on synthetic 1k cal sweep."""
    raw_csv, inv_csv = synthetic_session
    cal_df = run_calibration(raw_csv, inv_csv)
    assert not cal_df.empty
    assert (cal_df["load_id"] == "R1k_01").all()

    # GF * M should equal 1/R = 0.001 at every frequency. Pick the
    # raw rows for the same module/load/freq to recompute M.
    raw_df = pd.read_csv(raw_csv, dtype=str, keep_default_na=False)
    raw_df["frequency_hz"] = raw_df["frequency_hz"].astype(float)
    raw_df["real"] = raw_df["real"].astype(float)
    raw_df["imag"] = raw_df["imag"].astype(float)

    expected_inv_r = 1.0 / 1000.0
    for _, cal_row in cal_df.iterrows():
        f = float(cal_row["frequency_hz"])
        gf = float(cal_row["gain_factor"])
        match = raw_df[
            (raw_df["module_id"] == cal_row["module_id"])
            & (raw_df["load_id"] == cal_row["load_id"])
            & (raw_df["frequency_hz"] == f)
        ]
        assert not match.empty
        m = float(dft_magnitude(
            match["real"].to_numpy(), match["imag"].to_numpy()
        )[0])
        gf_times_m = gf * m
        # 0.5% tolerance per §H.2 / runner-contract.
        assert abs(gf_times_m - expected_inv_r) / expected_inv_r < 5e-3, (
            f"GF*M={gf_times_m} differs from 1/R={expected_inv_r} at f={f}"
        )


def test_phase_system_zero_for_ideal_resistor(synthetic_session):
    """phi_system_deg ~ 0 for ideal resistor (phase=0 in fixture)."""
    raw_csv, inv_csv = synthetic_session
    cal_df = run_calibration(raw_csv, inv_csv)
    phi = cal_df["phase_system_deg"].astype(float).abs()
    # imag is exactly 0 in the fixture so atan2 returns exactly 0;
    # any non-zero phase here is a calibration-stage bug.
    assert (phi < 1e-9).all()


def test_calibration_csv_roundtrip_preserves_columns(synthetic_session, tmp_path):
    """§I.6 CAL_CSV_COLUMNS survives write_calibration_csv -> read."""
    raw_csv, inv_csv = synthetic_session
    cal_df = run_calibration(raw_csv, inv_csv)
    out_path = tmp_path / "cal.csv"
    write_calibration_csv(cal_df, out_path)

    round_trip = pd.read_csv(out_path, dtype=str, keep_default_na=False)
    assert list(round_trip.columns) == CAL_CSV_COLUMNS
    # trusted_flag is the empty string at this stage (not yet
    # populated by trusted_band.py); the locked encoding survives.
    assert (round_trip["trusted_flag"] == "").all()


def test_run_calibration_returns_dataframe_when_no_output(synthetic_session):
    """Runner contract: returns the cal DataFrame even when output_path is None."""
    raw_csv, inv_csv = synthetic_session
    cal_df = run_calibration(raw_csv, inv_csv, output_path=None)
    assert isinstance(cal_df, pd.DataFrame)
    assert not cal_df.empty
    assert list(cal_df.columns) == CAL_CSV_COLUMNS


def test_run_calibration_writes_when_output_supplied(synthetic_session, tmp_path):
    """Runner contract: writes to disk only when output_path is supplied."""
    raw_csv, inv_csv = synthetic_session
    out = tmp_path / "cal.csv"
    cal_df = run_calibration(raw_csv, inv_csv, output_path=out)
    assert out.is_file()
    on_disk = pd.read_csv(out, dtype=str, keep_default_na=False)
    assert len(on_disk) == len(cal_df)
