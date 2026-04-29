"""plots.py -- static matplotlib plots for EISight logger CSV artifacts.

This module is intentionally a read-only visualization layer over the
pipeline CSV outputs. It validates the columns needed by each plot,
applies optional row filters in memory, and writes one figure per
(module_id, range_setting) group. Raw DFT plots are explicitly labelled
as raw register-space magnitude/phase, not calibrated impedance.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Dict, Iterable, Optional, Sequence, Union

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pandas.errors import EmptyDataError


PLOT_TYPES = ("raw-dft", "calibration", "repeatability", "trusted-band")
OUTPUT_FORMATS = ("png", "pdf", "svg")
FILTER_COLUMNS = (
    "module_id",
    "range_setting",
    "load_id",
    "row_type",
    "sample_id",
)


class PlotValidationError(ValueError):
    """Raised when an input CSV cannot support the requested plot."""


def run_plot(
    plot_type: str,
    *,
    output_dir: Union[Path, str],
    raw_path: Optional[Union[Path, str]] = None,
    cal_path: Optional[Union[Path, str]] = None,
    trusted_csv: Optional[Union[Path, str]] = None,
    module_id: Optional[str] = None,
    range_setting: Optional[str] = None,
    load_id: Optional[str] = None,
    row_type: Optional[str] = None,
    sample_id: Optional[str] = None,
    fmt: str = "png",
    dpi: int = 160,
) -> list[Path]:
    """Create the requested plot type and return generated file paths."""
    if plot_type not in PLOT_TYPES:
        raise PlotValidationError(
            f"unsupported plot type {plot_type!r}; expected one of {PLOT_TYPES}"
        )
    if fmt not in OUTPUT_FORMATS:
        raise PlotValidationError(
            f"unsupported plot format {fmt!r}; expected one of {OUTPUT_FORMATS}"
        )
    if dpi <= 0:
        raise PlotValidationError("dpi must be a positive integer")

    filters = {
        "module_id": module_id,
        "range_setting": range_setting,
        "load_id": load_id,
        "row_type": row_type,
        "sample_id": sample_id,
    }
    out_dir = Path(output_dir)

    if plot_type == "raw-dft":
        if raw_path is None:
            raise PlotValidationError("raw-dft plot requires --raw")
        df = _load_plot_csv(
            Path(raw_path),
            required=("module_id", "range_setting", "frequency_hz", "real", "imag"),
            filters=filters,
        )
        return _plot_raw_dft(df, Path(raw_path), out_dir, fmt, dpi)

    if plot_type == "calibration":
        if cal_path is None:
            raise PlotValidationError("calibration plot requires --cal")
        df = _load_plot_csv(
            Path(cal_path),
            required=(
                "module_id",
                "load_id",
                "range_setting",
                "frequency_hz",
                "gain_factor",
                "phase_system_deg",
            ),
            filters=filters,
        )
        return _plot_calibration(df, Path(cal_path), out_dir, fmt, dpi)

    if plot_type == "repeatability":
        if cal_path is None:
            raise PlotValidationError("repeatability plot requires --cal")
        df = _load_plot_csv(
            Path(cal_path),
            required=(
                "module_id",
                "load_id",
                "range_setting",
                "frequency_hz",
                "repeat_cv_percent",
            ),
            filters=filters,
        )
        _require_any_nonblank(df, "repeat_cv_percent", Path(cal_path))
        return _plot_repeatability(df, Path(cal_path), out_dir, fmt, dpi)

    if trusted_csv is None:
        raise PlotValidationError("trusted-band plot requires --trusted-csv")
    df = _load_plot_csv(
        Path(trusted_csv),
        required=("module_id", "range_setting", "frequency_hz", "trusted_flag"),
        filters=filters,
    )
    _require_any_nonblank(df, "trusted_flag", Path(trusted_csv))
    return _plot_trusted_band(df, Path(trusted_csv), out_dir, fmt, dpi)


def _load_plot_csv(
    path: Path,
    *,
    required: Sequence[str],
    filters: Dict[str, Optional[str]],
) -> pd.DataFrame:
    df = _read_csv(path)
    _require_columns(df, required, path)
    df = _apply_filters(df, filters, path)
    _require_nonblank_range_setting(df, path)
    return df


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except EmptyDataError as exc:
        raise PlotValidationError(f"{path}: empty CSV") from exc
    if df.empty:
        raise PlotValidationError(f"{path}: empty CSV has no data rows")
    return df


def _require_columns(df: pd.DataFrame, required: Iterable[str], path: Path) -> None:
    missing = [col for col in required if col not in df.columns]
    if missing:
        joined = ", ".join(repr(col) for col in missing)
        raise PlotValidationError(f"{path}: missing required column(s): {joined}")


def _apply_filters(
    df: pd.DataFrame,
    filters: Dict[str, Optional[str]],
    path: Path,
) -> pd.DataFrame:
    mask = pd.Series(True, index=df.index)
    for col in FILTER_COLUMNS:
        value = filters.get(col)
        if value is None or col not in df.columns:
            continue
        mask &= df[col].astype(str) == str(value)
    filtered = df.loc[mask].copy()
    if filtered.empty:
        raise PlotValidationError(f"{path}: no rows match plot filters")
    return filtered


def _blank_mask(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip() == ""


def _require_nonblank_range_setting(df: pd.DataFrame, path: Path) -> None:
    blank = _blank_mask(df["range_setting"])
    if blank.any():
        raise PlotValidationError(
            f"{path}: range_setting is blank in {int(blank.sum())} row(s)"
        )


def _require_any_nonblank(df: pd.DataFrame, col: str, path: Path) -> None:
    if _blank_mask(df[col]).all():
        raise PlotValidationError(f"{path}: {col} is all blank")


def _numeric_column(
    df: pd.DataFrame,
    col: str,
    path: Path,
    *,
    require_any: bool = True,
) -> pd.Series:
    raw = df[col].astype(str).str.strip()
    values = pd.to_numeric(raw.replace("", np.nan), errors="coerce")
    bad = raw.ne("") & values.isna()
    if bad.any():
        raise PlotValidationError(
            f"{path}: column {col!r} has non-numeric value(s)"
        )
    if require_any and values.notna().sum() == 0:
        raise PlotValidationError(f"{path}: column {col!r} is all blank")
    return values


def _plot_raw_dft(
    df: pd.DataFrame,
    input_path: Path,
    output_dir: Path,
    fmt: str,
    dpi: int,
) -> list[Path]:
    work = df.copy()
    work["_frequency_hz"] = _numeric_column(work, "frequency_hz", input_path)
    real = _numeric_column(work, "real", input_path)
    imag = _numeric_column(work, "imag", input_path)
    work["_raw_dft_magnitude"] = np.sqrt(real * real + imag * imag)
    work["_raw_dft_phase_deg"] = np.arctan2(imag, real) * (180.0 / np.pi)

    outputs: list[Path] = []
    for (module_id, range_setting), group in _module_range_groups(work):
        fig, axes = plt.subplots(
            2, 1, figsize=(8, 6), sharex=True, constrained_layout=True
        )
        try:
            axes[0].set_title(f"Raw DFT - {module_id} / {range_setting}")
            plotted = False
            trace_cols = _available_trace_cols(group, ("row_type", "load_id", "sample_id"))
            for label, trace in _trace_groups(group, trace_cols, default_label="raw DFT"):
                trace = trace.sort_values("_frequency_hz")
                valid = trace[
                    ["_frequency_hz", "_raw_dft_magnitude", "_raw_dft_phase_deg"]
                ].notna().all(axis=1)
                if not valid.any():
                    continue
                x = trace.loc[valid, "_frequency_hz"]
                axes[0].plot(
                    x,
                    trace.loc[valid, "_raw_dft_magnitude"],
                    marker="o",
                    markersize=3,
                    linewidth=1.2,
                    label=label,
                )
                axes[1].plot(
                    x,
                    trace.loc[valid, "_raw_dft_phase_deg"],
                    marker="o",
                    markersize=3,
                    linewidth=1.2,
                    label=label,
                )
                plotted = True
            if not plotted:
                raise PlotValidationError(
                    f"{input_path}: no numeric raw DFT rows for "
                    f"module_id={module_id!r}, range_setting={range_setting!r}"
                )
            axes[0].set_ylabel("Raw DFT magnitude (register counts)")
            axes[1].set_ylabel("Raw DFT phase (deg)")
            axes[1].set_xlabel("Frequency (Hz)")
            _finish_axes(axes)
            outputs.append(
                _save_figure(fig, output_dir, "raw-dft", module_id, range_setting, fmt, dpi)
            )
        finally:
            plt.close(fig)
    return outputs


def _plot_calibration(
    df: pd.DataFrame,
    input_path: Path,
    output_dir: Path,
    fmt: str,
    dpi: int,
) -> list[Path]:
    work = df.copy()
    work["_frequency_hz"] = _numeric_column(work, "frequency_hz", input_path)
    work["_gain_factor"] = _numeric_column(work, "gain_factor", input_path)
    work["_phase_system_deg"] = _numeric_column(work, "phase_system_deg", input_path)

    outputs: list[Path] = []
    for (module_id, range_setting), group in _module_range_groups(work):
        fig, axes = plt.subplots(
            2, 1, figsize=(8, 6), sharex=True, constrained_layout=True
        )
        try:
            axes[0].set_title(
                f"Calibration diagnostic - {module_id} / {range_setting}"
            )
            plotted = False
            for load_id, trace in _single_column_groups(group, "load_id"):
                trace = trace.sort_values("_frequency_hz")
                gain_valid = trace[["_frequency_hz", "_gain_factor"]].notna().all(axis=1)
                phase_valid = trace[
                    ["_frequency_hz", "_phase_system_deg"]
                ].notna().all(axis=1)
                if gain_valid.any():
                    axes[0].plot(
                        trace.loc[gain_valid, "_frequency_hz"],
                        trace.loc[gain_valid, "_gain_factor"],
                        marker="o",
                        markersize=3,
                        linewidth=1.2,
                        label=load_id,
                    )
                    plotted = True
                if phase_valid.any():
                    axes[1].plot(
                        trace.loc[phase_valid, "_frequency_hz"],
                        trace.loc[phase_valid, "_phase_system_deg"],
                        marker="o",
                        markersize=3,
                        linewidth=1.2,
                        label=load_id,
                    )
                    plotted = True
            if not plotted:
                raise PlotValidationError(
                    f"{input_path}: no numeric calibration rows for "
                    f"module_id={module_id!r}, range_setting={range_setting!r}"
                )
            axes[0].set_ylabel("gain_factor")
            axes[1].set_ylabel("phase_system_deg")
            axes[1].set_xlabel("Frequency (Hz)")
            _finish_axes(axes)
            outputs.append(
                _save_figure(
                    fig, output_dir, "calibration", module_id, range_setting, fmt, dpi
                )
            )
        finally:
            plt.close(fig)
    return outputs


def _plot_repeatability(
    df: pd.DataFrame,
    input_path: Path,
    output_dir: Path,
    fmt: str,
    dpi: int,
) -> list[Path]:
    work = df.copy()
    work["_frequency_hz"] = _numeric_column(work, "frequency_hz", input_path)
    work["_repeat_cv_percent"] = _numeric_column(
        work, "repeat_cv_percent", input_path
    )

    outputs: list[Path] = []
    for (module_id, range_setting), group in _module_range_groups(work):
        fig, ax = plt.subplots(figsize=(8, 4), constrained_layout=True)
        try:
            ax.set_title(f"Repeatability CV - {module_id} / {range_setting}")
            plotted = False
            for load_id, trace in _single_column_groups(group, "load_id"):
                trace = trace.sort_values("_frequency_hz")
                valid = trace[["_frequency_hz", "_repeat_cv_percent"]].notna().all(axis=1)
                if not valid.any():
                    continue
                ax.plot(
                    trace.loc[valid, "_frequency_hz"],
                    trace.loc[valid, "_repeat_cv_percent"],
                    marker="o",
                    markersize=3,
                    linewidth=1.2,
                    label=load_id,
                )
                plotted = True
            if not plotted:
                raise PlotValidationError(
                    f"{input_path}: no numeric repeatability rows for "
                    f"module_id={module_id!r}, range_setting={range_setting!r}"
                )
            ax.set_ylabel("repeat_cv_percent (%)")
            ax.set_xlabel("Frequency (Hz)")
            _finish_axes((ax,))
            outputs.append(
                _save_figure(
                    fig, output_dir, "repeatability", module_id, range_setting, fmt, dpi
                )
            )
        finally:
            plt.close(fig)
    return outputs


def _plot_trusted_band(
    df: pd.DataFrame,
    input_path: Path,
    output_dir: Path,
    fmt: str,
    dpi: int,
) -> list[Path]:
    work = df.copy()
    work["_frequency_hz"] = _numeric_column(work, "frequency_hz", input_path)
    flags = work["trusted_flag"].astype(str).str.strip()
    invalid = flags.ne("") & ~flags.isin(("True", "False"))
    if invalid.any():
        raise PlotValidationError(
            f"{input_path}: trusted_flag values must be 'True' or 'False' "
            "when nonblank"
        )
    work["_trusted_value"] = flags.map({"False": 0.0, "True": 1.0})

    outputs: list[Path] = []
    for (module_id, range_setting), group in _module_range_groups(work):
        fig, ax = plt.subplots(figsize=(8, 4), constrained_layout=True)
        try:
            ax.set_title(f"Trusted band - {module_id} / {range_setting}")
            plotted = False
            trace_cols = _available_trace_cols(group, ("load_id", "row_type", "sample_id"))
            for label, trace in _trace_groups(
                group, trace_cols, default_label="trusted_flag"
            ):
                trace = trace.sort_values("_frequency_hz")
                valid = trace[["_frequency_hz", "_trusted_value"]].notna().all(axis=1)
                if not valid.any():
                    continue
                x = trace.loc[valid, "_frequency_hz"]
                y = trace.loc[valid, "_trusted_value"]
                ax.plot(x, y, marker="o", markersize=4, linewidth=1.0, label=label)
                plotted = True
            if not plotted:
                raise PlotValidationError(
                    f"{input_path}: no trusted_flag values for "
                    f"module_id={module_id!r}, range_setting={range_setting!r}"
                )
            ax.set_ylabel("trusted_flag")
            ax.set_xlabel("Frequency (Hz)")
            ax.set_yticks([0.0, 1.0])
            ax.set_yticklabels(["False", "True"])
            ax.set_ylim(-0.2, 1.2)
            _finish_axes((ax,))
            outputs.append(
                _save_figure(
                    fig, output_dir, "trusted-band", module_id, range_setting, fmt, dpi
                )
            )
        finally:
            plt.close(fig)
    return outputs


def _module_range_groups(df: pd.DataFrame):
    return df.groupby(["module_id", "range_setting"], sort=True, dropna=False)


def _available_trace_cols(df: pd.DataFrame, candidates: Sequence[str]) -> list[str]:
    return [
        col
        for col in candidates
        if col in df.columns and not _blank_mask(df[col]).all()
    ]


def _trace_groups(
    df: pd.DataFrame,
    cols: Sequence[str],
    *,
    default_label: str,
):
    if not cols:
        yield default_label, df
        return
    grouped = df.groupby(list(cols), sort=True, dropna=False)
    for key, trace in grouped:
        if len(cols) == 1:
            key = (key,)
        parts = [
            f"{col}={_display_value(value)}"
            for col, value in zip(cols, key)
        ]
        yield ", ".join(parts), trace


def _single_column_groups(df: pd.DataFrame, col: str):
    for value, trace in df.groupby(col, sort=True, dropna=False):
        yield _display_value(value), trace


def _display_value(value) -> str:
    text = str(value).strip()
    return text if text else "(blank)"


def _finish_axes(axes: Iterable) -> None:
    for ax in axes:
        ax.grid(True, alpha=0.3)
        handles, labels = ax.get_legend_handles_labels()
        if handles and len(set(labels)) > 1:
            ax.legend(fontsize="small")


def _save_figure(
    fig,
    output_dir: Path,
    plot_type: str,
    module_id: str,
    range_setting: str,
    fmt: str,
    dpi: int,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{_sanitize_filename_part(plot_type)}_"
        f"{_sanitize_filename_part(module_id)}_"
        f"{_sanitize_filename_part(range_setting)}.{fmt}"
    )
    path = output_dir / filename
    fig.savefig(path, dpi=dpi)
    return path


def _sanitize_filename_part(value) -> str:
    text = str(value).strip() or "blank"
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    text = text.strip("._-")
    return text or "blank"
