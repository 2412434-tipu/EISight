"""test_plots.py -- static matplotlib plot runners for synthetic CSVs."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from eisight_logger.cli import main as cli_main
from eisight_logger.plots import PlotValidationError, run_plot


def _write_csv(tmp_path: Path, name: str, rows: list[dict], columns: list[str]) -> Path:
    path = tmp_path / name
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)
    return path


def _raw_csv(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    columns = [
        "module_id",
        "range_setting",
        "frequency_hz",
        "real",
        "imag",
        "load_id",
        "row_type",
        "sample_id",
    ]
    rows = rows or [
        {
            "module_id": "M1",
            "range_setting": "RANGE_4",
            "frequency_hz": "5000",
            "real": "1000",
            "imag": "0",
            "load_id": "R1k_01",
            "row_type": "CAL",
            "sample_id": "",
        },
        {
            "module_id": "M1",
            "range_setting": "RANGE_4",
            "frequency_hz": "6000",
            "real": "800",
            "imag": "600",
            "load_id": "R1k_01",
            "row_type": "CAL",
            "sample_id": "",
        },
    ]
    return _write_csv(tmp_path, "raw.csv", rows, columns)


def _cal_csv(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    columns = [
        "module_id",
        "load_id",
        "range_setting",
        "frequency_hz",
        "gain_factor",
        "phase_system_deg",
        "repeat_cv_percent",
        "trusted_flag",
    ]
    rows = rows or [
        {
            "module_id": "M1",
            "load_id": "R1k_01",
            "range_setting": "RANGE_4",
            "frequency_hz": "5000",
            "gain_factor": "0.001",
            "phase_system_deg": "0.0",
            "repeat_cv_percent": "0.2",
            "trusted_flag": "True",
        },
        {
            "module_id": "M1",
            "load_id": "R1k_01",
            "range_setting": "RANGE_4",
            "frequency_hz": "6000",
            "gain_factor": "0.0011",
            "phase_system_deg": "1.0",
            "repeat_cv_percent": "0.3",
            "trusted_flag": "False",
        },
        {
            "module_id": "M1",
            "load_id": "R470_01",
            "range_setting": "RANGE_4",
            "frequency_hz": "5000",
            "gain_factor": "0.002",
            "phase_system_deg": "0.5",
            "repeat_cv_percent": "0.4",
            "trusted_flag": "True",
        },
    ]
    return _write_csv(tmp_path, "cal.csv", rows, columns)


def _assert_nonempty_files(paths: list[Path]) -> None:
    assert paths
    for path in paths:
        assert path.is_file()
        assert path.stat().st_size > 0


def test_raw_dft_plot_creates_nonempty_figure(tmp_path: Path):
    raw = _raw_csv(tmp_path)
    paths = run_plot("raw-dft", raw_path=raw, output_dir=tmp_path / "plots")
    _assert_nonempty_files(paths)


def test_calibration_plot_creates_nonempty_figure(tmp_path: Path):
    cal = _cal_csv(tmp_path)
    paths = run_plot("calibration", cal_path=cal, output_dir=tmp_path / "plots")
    _assert_nonempty_files(paths)


def test_repeatability_plot_creates_nonempty_figure(tmp_path: Path):
    cal = _cal_csv(tmp_path)
    paths = run_plot("repeatability", cal_path=cal, output_dir=tmp_path / "plots")
    _assert_nonempty_files(paths)


def test_trusted_band_plot_creates_nonempty_figure(tmp_path: Path):
    cal = _cal_csv(tmp_path)
    paths = run_plot("trusted-band", trusted_csv=cal, output_dir=tmp_path / "plots")
    _assert_nonempty_files(paths)


def test_missing_required_columns_raise_clear_exception(tmp_path: Path):
    path = _write_csv(
        tmp_path,
        "missing.csv",
        [{"module_id": "M1", "range_setting": "RANGE_4", "frequency_hz": "5000"}],
        ["module_id", "range_setting", "frequency_hz"],
    )
    with pytest.raises(PlotValidationError, match="missing required column"):
        run_plot("raw-dft", raw_path=path, output_dir=tmp_path / "plots")


def test_empty_csv_fails(tmp_path: Path):
    path = _write_csv(
        tmp_path,
        "empty.csv",
        [],
        ["module_id", "range_setting", "frequency_hz", "real", "imag"],
    )
    with pytest.raises(PlotValidationError, match="empty CSV"):
        run_plot("raw-dft", raw_path=path, output_dir=tmp_path / "plots")


def test_missing_blank_range_setting_fails(tmp_path: Path):
    raw = _raw_csv(tmp_path, rows=[
        {
            "module_id": "M1",
            "range_setting": "",
            "frequency_hz": "5000",
            "real": "1000",
            "imag": "0",
            "load_id": "R1k_01",
            "row_type": "CAL",
            "sample_id": "",
        }
    ])
    with pytest.raises(PlotValidationError, match="range_setting is blank"):
        run_plot("raw-dft", raw_path=raw, output_dir=tmp_path / "plots")


def test_trusted_band_missing_or_all_blank_trusted_flag_fails(tmp_path: Path):
    missing = _write_csv(
        tmp_path,
        "trusted_missing.csv",
        [{"module_id": "M1", "range_setting": "RANGE_4", "frequency_hz": "5000"}],
        ["module_id", "range_setting", "frequency_hz"],
    )
    with pytest.raises(PlotValidationError, match="missing required column"):
        run_plot("trusted-band", trusted_csv=missing, output_dir=tmp_path / "plots")

    blank = _write_csv(
        tmp_path,
        "trusted_blank.csv",
        [{
            "module_id": "M1",
            "range_setting": "RANGE_4",
            "frequency_hz": "5000",
            "trusted_flag": "",
        }],
        ["module_id", "range_setting", "frequency_hz", "trusted_flag"],
    )
    with pytest.raises(PlotValidationError, match="trusted_flag is all blank"):
        run_plot("trusted-band", trusted_csv=blank, output_dir=tmp_path / "plots")


def test_same_module_two_ranges_produces_separate_output_files(tmp_path: Path):
    raw = _raw_csv(tmp_path, rows=[
        {
            "module_id": "M1",
            "range_setting": "RANGE_2",
            "frequency_hz": "5000",
            "real": "500",
            "imag": "0",
            "load_id": "R1k_01",
            "row_type": "CAL",
            "sample_id": "",
        },
        {
            "module_id": "M1",
            "range_setting": "RANGE_4",
            "frequency_hz": "5000",
            "real": "1000",
            "imag": "0",
            "load_id": "R1k_01",
            "row_type": "CAL",
            "sample_id": "",
        },
    ])
    paths = run_plot("raw-dft", raw_path=raw, output_dir=tmp_path / "plots")
    _assert_nonempty_files(paths)
    assert len(paths) == 2
    names = {path.name for path in paths}
    assert any("RANGE_2" in name for name in names)
    assert any("RANGE_4" in name for name in names)


def test_cli_plot_command_returns_success_and_prints_paths(
    tmp_path: Path, capsys,
):
    raw = _raw_csv(tmp_path)
    out_dir = tmp_path / "plots"
    rc = cli_main([
        "plot",
        "--type",
        "raw-dft",
        "--raw",
        str(raw),
        "--output-dir",
        str(out_dir),
    ])
    captured = capsys.readouterr()
    assert rc == 0
    paths = list(out_dir.glob("*.png"))
    _assert_nonempty_files(paths)
    assert str(paths[0]) in captured.out
