"""Focused tests for listen-time ingest annotations."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from eisight_logger.calibration import ResistorAnchor, build_calibration_table
from eisight_logger.cli import main as cli_main
from eisight_logger.serial_listener import replay_file
from tests.synthetic.generate_resistor_jsonl import generate_resistor_jsonl


def _read_raw(output_root: Path, session_id: str) -> pd.DataFrame:
    return pd.read_csv(
        output_root / session_id / "raw.csv",
        dtype=str,
        keep_default_na=False,
    )


def test_cli_replay_annotations_fill_csv_without_changing_payload(tmp_path: Path):
    jsonl = tmp_path / "real_blank.jsonl"
    generate_resistor_jsonl(
        1000.0,
        jsonl,
        session_id="",
        module_id="AD5933-A-DIRECT",
        row_type="",
        load_id="",
        num_points=3,
    )
    output_root = tmp_path / "data"

    rc = cli_main([
        "listen",
        "--session-id", "CAL_R1K_01",
        "--replay", str(jsonl),
        "--output-root", str(output_root),
        "--row-type", "CAL",
        "--load-id", "R1k_01",
    ])

    assert rc == 0
    raw = _read_raw(output_root, "CAL_R1K_01")
    assert set(raw["row_type"]) == {"CAL"}
    assert set(raw["load_id"]) == {"R1k_01"}
    assert set(raw["module_id"]) == {"AD5933-A-DIRECT"}
    assert set(raw["range_setting"]) == {"RANGE_4"}
    assert raw["frequency_hz"].tolist() == ["5000.0", "52500.0", "100000.0"]
    assert raw["real"].tolist() == ["1000", "1000", "1000"]
    assert raw["imag"].tolist() == ["0", "0", "0"]

    copied_jsonl = output_root / "CAL_R1K_01" / "raw.jsonl"
    records = [
        json.loads(line)
        for line in copied_jsonl.read_text(encoding="utf-8").splitlines()
    ]
    begin = next(rec for rec in records if rec["type"] == "sweep_begin")
    assert begin["row_type"] == ""
    assert begin["load_id"] == ""


def test_replay_preserves_firmware_metadata_without_override(tmp_path: Path):
    jsonl = tmp_path / "firmware_metadata.jsonl"
    generate_resistor_jsonl(
        1000.0,
        jsonl,
        row_type="SAMPLE",
        load_id="FW_LOAD_01",
        num_points=2,
    )
    output_root = tmp_path / "data"

    stats = replay_file(
        path=jsonl,
        session_id="TEST",
        output_root=output_root,
    )

    assert stats.is_clean()
    raw = _read_raw(output_root, "TEST")
    assert set(raw["row_type"]) == {"SAMPLE"}
    assert set(raw["load_id"]) == {"FW_LOAD_01"}


def test_explicit_replay_annotation_overrides_firmware_metadata(tmp_path: Path):
    jsonl = tmp_path / "firmware_metadata.jsonl"
    generate_resistor_jsonl(
        1000.0,
        jsonl,
        row_type="SAMPLE",
        load_id="FW_LOAD_01",
        num_points=2,
    )
    output_root = tmp_path / "data"

    stats = replay_file(
        path=jsonl,
        session_id="TEST",
        output_root=output_root,
        row_type="BLANK",
        load_id="BENCH_BLANK_01",
    )

    assert stats.is_clean()
    raw = _read_raw(output_root, "TEST")
    assert set(raw["row_type"]) == {"BLANK"}
    assert set(raw["load_id"]) == {"BENCH_BLANK_01"}


def test_calibration_consumes_annotated_replay_raw_csv(tmp_path: Path):
    jsonl = tmp_path / "blank_cal.jsonl"
    generate_resistor_jsonl(
        1000.0,
        jsonl,
        module_id="AD5933-A-DIRECT",
        row_type="",
        load_id="",
        num_points=3,
    )
    output_root = tmp_path / "data"

    stats = replay_file(
        path=jsonl,
        session_id="CAL_R1K_01",
        output_root=output_root,
        row_type="CAL",
        load_id="R1k_01",
    )

    assert stats.is_clean()
    raw = _read_raw(output_root, "CAL_R1K_01")
    cal = build_calibration_table(
        raw,
        {"R1k_01": ResistorAnchor(nominal_ohm=1000.0, actual_ohm=1000.0)},
        strict=True,
        required_load_ids=("R1k_01",),
        required_repeats=1,
    )
    assert len(cal) == 3
    assert set(cal["load_id"]) == {"R1k_01"}
    assert set(cal["module_id"]) == {"AD5933-A-DIRECT"}
    assert set(cal["range_setting"]) == {"RANGE_4"}
