"""create_session.py scaffold tests.

The tests execute the script in subprocesses and always direct output to
tmp_path, so they never touch data/real.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "create_session.py"
SESSION_DIRS = {
    "captures",
    "listen",
    "combined",
    "reports",
    "plots",
    "metadata",
}

RESISTOR_TEMPLATES = {
    "hardware/resistor_inventory.csv": "metadata/resistor_inventory.csv",
    "hardware/dmm_inventory.csv": "metadata/dmm_inventory.csv",
    "hardware/jumper_state.csv": "metadata/jumper_state.csv",
    "hardware/dc_bias_check.csv": "metadata/dc_bias_check.csv",
    "hardware/bench_session_log.csv": "metadata/bench_session_log.csv",
    "hardware/rfb_inventory.csv": "metadata/rfb_inventory.csv",
    "hardware/power_rail_check.csv": "metadata/power_rail_check.csv",
    "hardware/i2c_sanity_log.csv": "metadata/i2c_sanity_log.csv",
}

MILK_TEMPLATES = {
    "metadata/sample_inventory.csv": "metadata/sample_inventory.csv",
    "metadata/milk_session_log.csv": "metadata/milk_session_log.csv",
    "metadata/dilution_plan.csv": "metadata/dilution_plan.csv",
    "metadata/lactometer_log.csv": "metadata/lactometer_log.csv",
    "metadata/ph_log.csv": "metadata/ph_log.csv",
    "metadata/temperature_log.csv": "metadata/temperature_log.csv",
    "metadata/dataset_split_plan.csv": "metadata/dataset_split_plan.csv",
    "hardware/cell_geometry.csv": "metadata/cell_geometry.csv",
    "hardware/dc_bias_check.csv": "metadata/dc_bias_check.csv",
    "hardware/bench_session_log.csv": "metadata/bench_session_log.csv",
}


def _run_create(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _source_header(source_rel: str) -> bytes:
    with (REPO_ROOT / source_rel).open("rb") as handle:
        return handle.readline()


def _assert_header_only_files(session_dir: Path, templates: dict[str, str]) -> None:
    for source_rel, dest_rel in templates.items():
        dest_path = session_dir / dest_rel
        assert dest_path.is_file()
        assert dest_path.read_bytes() == _source_header(source_rel)
        assert len(dest_path.read_bytes().splitlines()) == 1


def _assert_session_dirs(session_dir: Path) -> None:
    assert session_dir.is_dir()
    for dirname in SESSION_DIRS:
        assert (session_dir / dirname).is_dir()


def test_resistor_cal_creates_expected_folders_and_header_only_metadata(
    tmp_path: Path,
):
    output_root = tmp_path / "real"
    result = _run_create(
        "--session-id",
        "RC_001",
        "--kind",
        "resistor-cal",
        "--output-root",
        str(output_root),
    )
    assert result.returncode == 0, result.stderr
    session_dir = output_root / "RC_001"
    _assert_session_dirs(session_dir)
    _assert_header_only_files(session_dir, RESISTOR_TEMPLATES)
    assert not (session_dir / "raw.csv").exists()
    assert not (session_dir / "cal.csv").exists()


def test_milk_water_creates_expected_folders_and_header_only_metadata(
    tmp_path: Path,
):
    output_root = tmp_path / "real"
    result = _run_create(
        "--session-id",
        "MILK-001",
        "--kind",
        "milk-water",
        "--output-root",
        str(output_root),
    )
    assert result.returncode == 0, result.stderr
    session_dir = output_root / "MILK-001"
    _assert_session_dirs(session_dir)
    _assert_header_only_files(session_dir, MILK_TEMPLATES)
    assert not (session_dir / "qc").exists()
    assert not (session_dir / "trusted_band.csv").exists()


@pytest.mark.parametrize(
    "session_id",
    [
        "",
        "bad space",
        "bad/slash",
        "bad\\slash",
        "../bad",
        "..",
        "/absolute",
        "C:\\absolute",
        "x" * 65,
    ],
)
def test_invalid_session_ids_fail(tmp_path: Path, session_id: str):
    result = _run_create(
        "--session-id",
        session_id,
        "--kind",
        "resistor-cal",
        "--output-root",
        str(tmp_path / "real"),
    )
    assert result.returncode != 0
    assert "invalid session_id" in result.stderr


def test_existing_non_empty_session_fails_without_force(tmp_path: Path):
    output_root = tmp_path / "real"
    session_dir = output_root / "RC_EXISTS"
    session_dir.mkdir(parents=True)
    sentinel = session_dir / "operator_notes.txt"
    sentinel.write_text("already here\n", encoding="utf-8")

    result = _run_create(
        "--session-id",
        "RC_EXISTS",
        "--kind",
        "resistor-cal",
        "--output-root",
        str(output_root),
    )
    assert result.returncode != 0
    assert "non-empty" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "already here\n"
    assert not (session_dir / "metadata" / "resistor_inventory.csv").exists()


def test_force_preserves_existing_non_empty_files(tmp_path: Path):
    output_root = tmp_path / "real"
    session_dir = output_root / "RC_FORCE"
    template_path = session_dir / "metadata" / "resistor_inventory.csv"
    template_path.parent.mkdir(parents=True)
    template_path.write_text("custom,header\ncustom,value\n", encoding="utf-8")

    result = _run_create(
        "--session-id",
        "RC_FORCE",
        "--kind",
        "resistor-cal",
        "--output-root",
        str(output_root),
        "--force",
    )
    assert result.returncode == 0, result.stderr
    _assert_session_dirs(session_dir)
    assert template_path.read_text(encoding="utf-8") == "custom,header\ncustom,value\n"
    assert (session_dir / "metadata" / "dmm_inventory.csv").is_file()


def test_force_overwrite_templates_overwrites_templates_only(tmp_path: Path):
    output_root = tmp_path / "real"
    session_dir = output_root / "RC_OVERWRITE"
    template_path = session_dir / "metadata" / "resistor_inventory.csv"
    unrelated_path = session_dir / "metadata" / "operator_notes.csv"
    unrelated_path.parent.mkdir(parents=True)
    template_path.write_text("custom,header\ncustom,value\n", encoding="utf-8")
    unrelated_path.write_text("do,not,touch\n1,2,3\n", encoding="utf-8")

    result = _run_create(
        "--session-id",
        "RC_OVERWRITE",
        "--kind",
        "resistor-cal",
        "--output-root",
        str(output_root),
        "--force",
        "--overwrite-templates",
    )
    assert result.returncode == 0, result.stderr
    assert template_path.read_bytes() == _source_header("hardware/resistor_inventory.csv")
    assert unrelated_path.read_text(encoding="utf-8") == "do,not,touch\n1,2,3\n"


def test_missing_source_template_fails_clearly(tmp_path: Path):
    fake_root = tmp_path / "fake_repo"
    fake_script = fake_root / "scripts" / "create_session.py"
    fake_script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, fake_script)

    result = subprocess.run(
        [
            sys.executable,
            str(fake_script),
            "--session-id",
            "RC_MISSING",
            "--kind",
            "resistor-cal",
            "--output-root",
            str(tmp_path / "real"),
        ],
        cwd=fake_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "missing source template" in result.stderr
    assert "resistor_inventory.csv" in result.stderr


def test_session_info_json_records_copied_and_skipped_files(tmp_path: Path):
    output_root = tmp_path / "real"
    session_dir = output_root / "RC_INFO"
    skipped_template = session_dir / "metadata" / "resistor_inventory.csv"
    skipped_template.parent.mkdir(parents=True)
    skipped_template.write_text("custom,header\ncustom,value\n", encoding="utf-8")

    result = _run_create(
        "--session-id",
        "RC_INFO",
        "--kind",
        "resistor-cal",
        "--output-root",
        str(output_root),
        "--operator",
        "TEST_OPERATOR",
        "--force",
    )
    assert result.returncode == 0, result.stderr

    info = json.loads((session_dir / "SESSION_INFO.json").read_text(encoding="utf-8"))
    assert info["session_id"] == "RC_INFO"
    assert info["kind"] == "resistor-cal"
    assert info["created_by_operator"] == "TEST_OPERATOR"
    assert info["created_utc"].endswith("Z")
    assert info["tool_version"]
    assert info["templates_skipped"] == ["metadata/resistor_inventory.csv"]
    assert set(info["templates_copied"]) == set(RESISTOR_TEMPLATES.values()) - {
        "metadata/resistor_inventory.csv"
    }
    assert set(info["template_header_sha256"]) == set(RESISTOR_TEMPLATES.values())
    assert "no measurement values" in info["notes"]
