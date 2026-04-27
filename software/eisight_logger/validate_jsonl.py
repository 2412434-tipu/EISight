"""validate_jsonl.py -- gatekeeper CLI for captured EISight JSONL logs.

Reads a .jsonl file one line at a time and validates each non-empty
line against the v4.0c discriminated union (schemas.JsonlRecord).
Exits 1 if any line fails -- per the §I.2.a int16 range guard, an
out-of-range real/imag means the firmware (or a hand-edited replay
file) drifted away from signed 16-bit semantics, and downstream
calibration would silently produce garbage.

Implements no v4.0c calculation; this is the line-by-line §I.4
compliance check that should run before any calibration / QC /
plot stage. Sequence-level checks (hello-first, sweep_begin
before data, matching sweep_id on sweep_end, etc.) are intentionally
out of scope here -- they belong with the listener / pipeline,
not with the wire-format validator.

Usage
-----
    python -m eisight_logger.validate_jsonl path/to/raw.jsonl
    python -m eisight_logger.validate_jsonl --quiet path/to/raw.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from pydantic import ValidationError

from eisight_logger.schemas import parse_line


def _iter_lines(path: Path) -> Iterable[Tuple[int, str]]:
    """Yield (1-based line number, stripped non-empty line) tuples."""
    with path.open("r", encoding="utf-8") as fh:
        for n, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            yield n, line


def _format_error(exc: ValidationError) -> str:
    """Render the first pydantic error as 'loc: msg', best-effort."""
    errs = exc.errors()
    if not errs:
        return str(exc)
    first = errs[0]
    loc = ".".join(str(x) for x in first.get("loc", ()))
    msg = first.get("msg", "validation error")
    return f"{loc}: {msg}" if loc else msg


def validate_file(path: Path, quiet: bool = False) -> Tuple[int, int]:
    """Validate every non-empty line in path.

    Returns (total_records_seen, failed_count). Failures are
    streamed to stderr unless quiet=True. The summary line is
    always printed to stdout.
    """
    total = 0
    failed = 0
    for n, line in _iter_lines(path):
        total += 1
        try:
            parse_line(line)
        except ValidationError as exc:
            failed += 1
            if not quiet:
                print(f"line {n}: {_format_error(exc)}", file=sys.stderr)
    print(f"validated {total} records, {failed} failed")
    return total, failed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eisight_logger.validate_jsonl",
        description=(
            "Validate every line in a captured EISight .jsonl file "
            "against the v4.0c schemas (sec. I.4) including the I.2.a "
            "int16 range guard. Exits 1 if any line fails."
        ),
    )
    parser.add_argument(
        "file", type=Path,
        help="Path to a .jsonl file (raw firmware capture or replay fixture).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-failure output; only print the summary line.",
    )
    args = parser.parse_args(argv)

    if not args.file.is_file():
        parser.error(f"not a file: {args.file}")

    _, failed = validate_file(args.file, quiet=args.quiet)
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
