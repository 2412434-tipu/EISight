"""cli.py -- argparse routing for the EISight v4.0c laptop pipeline.

Thin routing layer over the eisight_logger module surface. Each
subcommand maps to a single run_* function in its owning module
(serial_listener.listen_serial / replay_file,
validate_jsonl.validate_file, calibration.run_calibration,
qc.run_qc, trusted_band.run_trusted_band, gates.run_g_*) and
does no orchestration of its own. Adding a new gate or stage
means adding a runner in the relevant module and wiring it here
in ~10 lines; the CLI surface is intentionally a translator,
not a control plane.

Subcommands:

  listen      Ingest live serial / replay JSONL into raw.{jsonl,csv}.
  validate    Validate a captured JSONL file against the §I.4 schema.
  calibrate   Build the §I.6 calibration table from raw + inventory.
  qc          Run §H.8 per-row QC on the §I.5 raw CSV.
  trust       Run §H.5 trusted-band selection over raw + cal.
  gate        Evaluate one of G-DC3 / G-SAT / G-LIN.
  plot        Create static matplotlib CSV diagnostic plots.

Implements: §I.4/§I.5 ingest, §I.6/§H.5/§H.8 batch processing,
§E.11/§F.10.a/§F.10.b gate evaluation -- all by routing to the
modules that own each spec section.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from eisight_logger.calibration import (
    CalibrationStrictError,
    run_calibration,
)
from eisight_logger.gates import (
    run_g_dc3,
    run_g_lin,
    run_g_sat,
    verdict_is_pass,
)
from eisight_logger.plots import run_plot
from eisight_logger.qc import (
    QC_PHASE_JUMP_DEG,
    QC_TEMP_DRIFT_C,
    run_qc,
)
from eisight_logger.serial_listener import (
    DEFAULT_BAUD,
    LISTEN_ROW_TYPES,
    listen_serial,
    replay_file,
)
from eisight_logger.trusted_band import (
    TRUSTED_BAND_CV_PCT,
    TRUSTED_BAND_MAG_RESIDUAL_PCT,
    TRUSTED_BAND_PHASE_JUMP_DEG,
    TRUSTED_BAND_PHASE_RESIDUAL_DEG,
    run_trusted_band,
)
from eisight_logger.validate_jsonl import validate_file


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="eisight-logger",
        description=(
            "EISight v4.0c laptop pipeline: ingest firmware JSONL, "
            "build calibration / QC / trusted-band artifacts, and "
            "evaluate the E.11 / F.10 quality gates."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    _add_listen(sub)
    _add_validate(sub)
    _add_calibrate(sub)
    _add_qc(sub)
    _add_trust(sub)
    _add_gate(sub)
    _add_plot(sub)
    return parser


# ---------------------------------------------------------------
# Subparser builders. One per subcommand; each registers a handler
# via set_defaults(handler=...) so main() is a single dispatch.
# ---------------------------------------------------------------


def _add_listen(sub) -> None:
    p = sub.add_parser("listen", help="Ingest serial or replay JSONL")
    p.add_argument("--session-id", required=True)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--port", help="Serial port (e.g. COM5)")
    src.add_argument("--replay", type=Path, help="Captured .jsonl file")
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    p.add_argument("--output-root", type=Path, default=Path("data/real"))
    p.add_argument("--operator", default="")
    p.add_argument("--sample-id", default="")
    p.add_argument("--notes", default="")
    p.add_argument(
        "--row-type", choices=LISTEN_ROW_TYPES, default=None,
        help=(
            "Optional CSV row_type annotation override. Accepted values: "
            f"{', '.join(LISTEN_ROW_TYPES)}."
        ),
    )
    p.add_argument(
        "--load-id", default=None,
        help="Optional CSV load_id annotation override.",
    )
    p.set_defaults(handler=_cmd_listen)


def _add_validate(sub) -> None:
    p = sub.add_parser("validate", help="Validate JSONL against §I.4")
    p.add_argument("file", type=Path)
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(handler=_cmd_validate)


def _add_calibrate(sub) -> None:
    p = sub.add_parser("calibrate", help="Build §I.6 cal table")
    p.add_argument("raw_path", type=Path)
    p.add_argument("inventory_path", type=Path)
    p.add_argument("output_path", type=Path)
    p.add_argument("--session-id", default=None)
    # Bench-CLI default is strict (fail-closed); callers that need
    # the permissive library behavior (notebooks, partial fixtures)
    # opt out explicitly with --no-strict.
    p.add_argument(
        "--no-strict", dest="strict", action="store_false",
        help=(
            "Disable bench strictness: empty CAL / missing actuals "
            "/ under-replicated F.10 groups become silent skips "
            "rather than CalibrationStrictError. Library/notebook use only."
        ),
    )
    p.set_defaults(handler=_cmd_calibrate, strict=True)


def _add_qc(sub) -> None:
    p = sub.add_parser("qc", help="Run §H.8 per-row QC on §I.5 raw")
    p.add_argument("raw_path", type=Path)
    p.add_argument("output_path", type=Path)
    p.add_argument(
        "--phase-jump-deg-max", type=float, default=QC_PHASE_JUMP_DEG,
    )
    p.add_argument(
        "--temp-drift-c-max", type=float, default=QC_TEMP_DRIFT_C,
    )
    p.set_defaults(handler=_cmd_qc)


def _add_trust(sub) -> None:
    p = sub.add_parser("trust", help="Run §H.5 trusted-band")
    p.add_argument("raw_path", type=Path)
    p.add_argument("cal_path", type=Path)
    p.add_argument("--raw-output", type=Path, default=None)
    p.add_argument("--cal-output", type=Path, default=None)
    p.add_argument("--g-sat-failures", type=Path, default=None)
    p.add_argument(
        "--mag-residual-pct-max", type=float,
        default=TRUSTED_BAND_MAG_RESIDUAL_PCT,
    )
    p.add_argument(
        "--phase-residual-deg-max", type=float,
        default=TRUSTED_BAND_PHASE_RESIDUAL_DEG,
    )
    p.add_argument(
        "--cv-pct-max", type=float, default=TRUSTED_BAND_CV_PCT,
    )
    p.add_argument(
        "--phase-jump-deg-max", type=float,
        default=TRUSTED_BAND_PHASE_JUMP_DEG,
    )
    p.set_defaults(handler=_cmd_trust)


def _add_gate(sub) -> None:
    p = sub.add_parser("gate", help="Evaluate G-DC3 / G-SAT / G-LIN")
    p.add_argument(
        "--type", required=True, dest="gate_type",
        choices=("g_dc3", "g_sat", "g_lin"),
    )
    p.add_argument("--output-dir", type=Path, default=None)
    p.add_argument(
        "--fmt", choices=("text", "json", "both"), default="both",
    )
    p.add_argument("--csv", type=Path, help="g_dc3: dc_bias_check.csv")
    p.add_argument("--cal", type=Path, help="g_sat: §I.6 cal table")
    p.add_argument(
        "--failures-output", type=Path, default=None,
        help="g_sat: write G-SAT failures triple CSV here",
    )
    p.add_argument("--cal-r4", type=Path, help="g_lin: Range-4 cal")
    p.add_argument("--cal-r2", type=Path, help="g_lin: Range-2 cal")
    p.add_argument(
        "--trusted-band-csv", type=Path, default=None,
        help="g_lin: trusted-band-merged §I.5 or §I.6 CSV",
    )
    p.set_defaults(handler=_cmd_gate)


def _add_plot(sub) -> None:
    p = sub.add_parser("plot", help="Create static matplotlib CSV plots")
    p.add_argument(
        "--type", required=True, dest="plot_type",
        choices=("raw-dft", "calibration", "repeatability", "trusted-band"),
    )
    p.add_argument("--raw", type=Path, default=None, help="raw.csv input")
    p.add_argument("--cal", type=Path, default=None, help="cal.csv input")
    p.add_argument(
        "--trusted-csv", type=Path, default=None,
        help="trusted-band-merged raw or calibration CSV",
    )
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--module-id", default=None)
    p.add_argument("--range-setting", default=None)
    p.add_argument("--load-id", default=None)
    p.add_argument("--row-type", default=None)
    p.add_argument("--sample-id", default=None)
    p.add_argument("--fmt", choices=("png", "pdf", "svg"), default="png")
    p.add_argument("--dpi", type=int, default=160)
    p.set_defaults(handler=_cmd_plot)


# ---------------------------------------------------------------
# Subcommand handlers. Each is a thin call into a run_* function;
# orchestration lives in the runners, not here.
# ---------------------------------------------------------------


def _cmd_listen(args) -> int:
    kw = dict(
        session_id=args.session_id, output_root=args.output_root,
        operator=args.operator, sample_id=args.sample_id, notes=args.notes,
        row_type=args.row_type, load_id=args.load_id,
    )
    if args.port is not None:
        stats = listen_serial(port=args.port, baud=args.baud, **kw)
    else:
        if not args.replay.is_file():
            raise SystemExit(f"not a file: {args.replay}")
        stats = replay_file(path=args.replay, **kw)
    # Sequence-safety: dropped/duplicate/non-monotonic data records,
    # missing sweep_end, sweep_end.error, or point-count mismatches
    # all flip exit to non-zero so the bench workflow does not let
    # partial / error sweeps flow into calibration as valid evidence.
    return 0 if stats.is_clean() else 1


def _cmd_validate(args) -> int:
    if not args.file.is_file():
        raise SystemExit(f"not a file: {args.file}")
    _, failed = validate_file(args.file, quiet=args.quiet)
    return 1 if failed > 0 else 0


def _cmd_calibrate(args) -> int:
    try:
        run_calibration(
            args.raw_path, args.inventory_path, args.output_path,
            session_id=args.session_id,
            strict=args.strict,
        )
    except CalibrationStrictError as exc:
        # Fail-closed bench behavior: surface the strictness violation
        # to stderr and exit non-zero so the operator sees the missing
        # evidence instead of an empty / misleading cal table.
        print(f"calibration strict failure: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_qc(args) -> int:
    run_qc(
        args.raw_path, args.output_path,
        phase_jump_deg_max=args.phase_jump_deg_max,
        temp_drift_c_max=args.temp_drift_c_max,
    )
    return 0


def _cmd_trust(args) -> int:
    run_trusted_band(
        args.raw_path, args.cal_path,
        raw_output=args.raw_output, cal_output=args.cal_output,
        g_sat_failures_path=args.g_sat_failures,
        mag_residual_pct_max=args.mag_residual_pct_max,
        phase_residual_deg_max=args.phase_residual_deg_max,
        cv_pct_max=args.cv_pct_max,
        phase_jump_deg_max=args.phase_jump_deg_max,
    )
    return 0


def _cmd_gate(args) -> int:
    if args.gate_type == "g_dc3":
        report = run_g_dc3(args.csv, args.output_dir, fmt=args.fmt)
    elif args.gate_type == "g_sat":
        report = run_g_sat(
            args.cal, args.output_dir, args.failures_output, fmt=args.fmt,
        )
    else:
        report = run_g_lin(
            args.cal_r4, args.cal_r2, args.output_dir,
            args.trusted_band_csv, fmt=args.fmt,
        )
    # Bench exit-code: PASS -> 0; WARN, FAIL, NOT_EVALUATED -> 1.
    # NOT_EVALUATED is intentionally non-zero so missing evidence
    # cannot be misread as "gate succeeded with no data" in CI.
    # Report artifacts on disk preserve the four-way distinction.
    print(f"{report.gate_id}: {report.verdict.value}")
    return 0 if verdict_is_pass(report.verdict) else 1


def _cmd_plot(args) -> int:
    paths = run_plot(
        args.plot_type,
        raw_path=args.raw,
        cal_path=args.cal,
        trusted_csv=args.trusted_csv,
        output_dir=args.output_dir,
        module_id=args.module_id,
        range_setting=args.range_setting,
        load_id=args.load_id,
        row_type=args.row_type,
        sample_id=args.sample_id,
        fmt=args.fmt,
        dpi=args.dpi,
    )
    for path in paths:
        print(path)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
