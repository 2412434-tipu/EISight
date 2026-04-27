"""serial_listener.py -- ingest JSONL from the firmware (live or replay).

Implements §I.4 (serial JSONL packet format) on the laptop side
and the §I.2.a int16 range guard (enforced via schemas.py).

Two mutually exclusive sources:
  --port <COMx>     live mode; opens the firmware's USB-serial
                    bridge (default 921600 baud to match the
                    firmware README).
  --replay <path>   file mode; reads a previously captured JSONL
                    file. Used by the synthetic walkthrough and
                    by tests so the entire pipeline can run
                    without hardware.

Per-line behavior:
  1. Append the raw line verbatim to data/real/<session_id>/raw.jsonl.
     This file is the source of truth and is preserved even when
     validation fails -- so an operator can investigate, and the
     pipeline can be re-run from raw.jsonl after fixing schemas.
  2. Validate against schemas.JsonlRecord (the §I.2.a int16 guard
     lives there). On ValidationError, log to stderr and continue;
     the malformed line is still on disk in raw.jsonl.
  3. Dispatch validated records to RawCsvWriter, which buffers
     per-sweep and writes the §I.5 long-format CSV at
     data/real/<session_id>/raw.csv.

Resumption is intentionally out of scope: each invocation
truncates raw.jsonl and raw.csv. To re-run, pick a new
--session-id, or delete the existing session directory.
Ctrl-C flushes both files cleanly and prints a summary.

Implements: §I.4 (JSONL packet format) ingest, §I.5 (raw CSV)
fan-out, §I.2.a (int16 range guard) at the validation step.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

from pydantic import ValidationError

from eisight_logger.raw_writer import RawCsvWriter
from eisight_logger.schemas import parse_line

# Matches the firmware README's USB-serial bridge rate.
# Single source of truth for both listen_serial()'s default and
# the --baud argparse default.
DEFAULT_BAUD = 921600


class ListenerStats:
    """Counters surfaced at shutdown.

    lines_seen      -- non-empty lines pulled from the source.
    lines_failed    -- lines that did not validate against JsonlRecord.
    records_written -- validated records dispatched to the CSV writer.
    dropped_data    -- data records the CSV writer could not place
                       (no open sweep, or sweep_id mismatch). Read
                       from RawCsvWriter at shutdown.
    """

    def __init__(self) -> None:
        self.lines_seen = 0
        self.lines_failed = 0
        self.records_written = 0
        self.dropped_data = 0


def listen_serial(
    port: str,
    session_id: str,
    baud: int = DEFAULT_BAUD,
    output_root: Path = Path("data/real"),
    operator: str = "",
    sample_id: str = "",
    notes: str = "",
) -> ListenerStats:
    """Open `port` at `baud`, write data/real/<session_id>/raw.{jsonl,csv}.

    pyserial is imported lazily so replay-only environments
    (and tests for non-listener modules) don't need the dep
    on the import path.
    """
    try:
        import serial as pyserial  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "pyserial is required for --port. Install it via "
            "'pip install pyserial' or use --replay instead."
        ) from exc

    with pyserial.Serial(port, baudrate=baud, timeout=0.1) as ser:
        return _run(
            _serial_lines(ser),
            session_id=session_id,
            output_root=output_root,
            operator=operator,
            sample_id=sample_id,
            notes=notes,
        )


def replay_file(
    path: Path,
    session_id: str,
    output_root: Path = Path("data/real"),
    operator: str = "",
    sample_id: str = "",
    notes: str = "",
) -> ListenerStats:
    """Read a captured JSONL file and emit raw.{jsonl,csv} as if from the wire."""
    return _run(
        _replay_lines(path),
        session_id=session_id,
        output_root=output_root,
        operator=operator,
        sample_id=sample_id,
        notes=notes,
    )


def _serial_lines(ser) -> Iterator[str]:
    """Yield decoded, stripped, non-empty lines from a pyserial port.

    readline() returns empty bytes on the 0.1 s timeout; we
    spin in that case so KeyboardInterrupt remains responsive.
    """
    while True:
        raw = ser.readline()
        if not raw:
            continue
        text = raw.decode("utf-8", errors="replace").strip()
        if text:
            yield text


def _replay_lines(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line:
                yield line


def _run(
    lines: Iterable[str],
    session_id: str,
    output_root: Path,
    operator: str,
    sample_id: str,
    notes: str,
) -> ListenerStats:
    session_dir = output_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    raw_path = session_dir / "raw.jsonl"
    csv_path = session_dir / "raw.csv"

    stats = ListenerStats()

    # Nested context managers ensure both files close cleanly on
    # any path out of the loop (normal end, KeyboardInterrupt,
    # or unexpected exception). Counter read happens AFTER the
    # with-blocks exit so RawCsvWriter.close()'s buffer-drop
    # bookkeeping is reflected in dropped_data.
    raw_fh = raw_path.open("w", encoding="utf-8")
    csv_writer = RawCsvWriter(
        csv_path,
        operator=operator,
        sample_id=sample_id,
        notes=notes,
        session_id=session_id,
    )
    try:
        for line in lines:
            stats.lines_seen += 1
            raw_fh.write(line + "\n")
            raw_fh.flush()
            try:
                rec = parse_line(line)
            except ValidationError as exc:
                stats.lines_failed += 1
                _print_failure(exc, stats.lines_seen)
                continue
            csv_writer.on_record(rec)
            stats.records_written += 1
    except KeyboardInterrupt:
        print("interrupted; flushing files...", file=sys.stderr)
    finally:
        csv_writer.close()
        raw_fh.flush()
        raw_fh.close()

    stats.dropped_data = csv_writer.dropped_data_count
    _print_summary(stats, raw_path, csv_path)
    return stats


def _print_failure(exc: ValidationError, lineno: int) -> None:
    errs = exc.errors()
    if errs:
        first = errs[0]
        loc = ".".join(str(x) for x in first.get("loc", ()))
        msg = first.get("msg", "validation error")
        print(f"line {lineno}: {loc}: {msg}", file=sys.stderr)
    else:
        print(f"line {lineno}: {exc}", file=sys.stderr)


def _print_summary(
    stats: ListenerStats, raw_path: Path, csv_path: Path
) -> None:
    print(
        f"listener summary: lines={stats.lines_seen} "
        f"failed={stats.lines_failed} "
        f"records={stats.records_written} "
        f"dropped_data={stats.dropped_data}"
    )
    print(f"raw.jsonl: {raw_path}")
    print(f"raw.csv:   {csv_path}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eisight_logger.serial_listener",
        description=(
            "Ingest the firmware's JSONL stream from a serial port "
            "(or a captured replay file) and write data/real/"
            "<session_id>/raw.{jsonl,csv} per the v4.0c sec. I.4 / I.5 "
            "schemas."
        ),
    )
    parser.add_argument(
        "--session-id", required=True,
        help=(
            "Session identifier; used as the output directory name "
            "and as the session_id column value in the CSV."
        ),
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--port",
        help="Serial port (e.g. COM5, /dev/ttyUSB0). Live mode.",
    )
    src.add_argument(
        "--replay", type=Path,
        help="Path to a captured .jsonl file. File-replay mode.",
    )
    parser.add_argument(
        "--baud", type=int, default=DEFAULT_BAUD,
        help=f"Serial baud (default {DEFAULT_BAUD} to match firmware README).",
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("data/real"),
        help="Root directory for session outputs (default data/real).",
    )
    parser.add_argument(
        "--operator", default="",
        help="Operator initials, written to every CSV row's operator column.",
    )
    parser.add_argument(
        "--sample-id", default="",
        help="Sample identifier for this session (one CSV = one sample).",
    )
    parser.add_argument(
        "--notes", default="",
        help="Free-form notes, written to every CSV row's notes column.",
    )
    args = parser.parse_args(argv)

    if args.port is not None:
        stats = listen_serial(
            port=args.port,
            session_id=args.session_id,
            baud=args.baud,
            output_root=args.output_root,
            operator=args.operator,
            sample_id=args.sample_id,
            notes=args.notes,
        )
    else:
        if not args.replay.is_file():
            parser.error(f"not a file: {args.replay}")
        stats = replay_file(
            path=args.replay,
            session_id=args.session_id,
            output_root=args.output_root,
            operator=args.operator,
            sample_id=args.sample_id,
            notes=args.notes,
        )
    return 1 if stats.lines_failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
