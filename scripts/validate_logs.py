"""validate_logs.py -- hardware/ CSV header conformance check.

Scans hardware/ for the operator-logged CSVs the v4.0c
laptop pipeline depends on, and reports which ones exist
with the §F-cited column set, which are missing, and which
are malformed (file exists but lacks required columns).

For each MISSING file, a header-only CSV template is created
in place so the operator has a starting point to fill in.
MALFORMED files are NOT modified -- the operator presumably
populated the file by hand and a silent rewrite would lose
data. Fix MALFORMED by adding the missing columns manually.

Required files and column sets:

  dc_bias_check.csv (§F.6)
      module_id, range, condition, V_DC_P1_GND_mV,
      V_DC_P2_GND_mV, V_DC_DIFF_mV, V_DD_V, date, operator
      Authoritative; columns enumerated explicitly in §F.6.

  resistor_inventory.csv (§F.7 step 4 + G-DMMx)
      load_id, nominal_ohm, measured_ohm, T_C, operator,
      timestamp, lab_dmm_ohm, lab_dmm_model,
      lab_dmm_accuracy_class_pct
      §F.7 step 4 enumerates nominal_ohm, measured_ohm, T_C,
      operator, timestamp; the labelling rule (R100_01 ..
      R10k_10) implies the load_id key column used by
      calibration.load_inventory. G-DMMx promotion adds the
      three lab_dmm_* columns (§F.7 G-DMMx box).

  jumper_state.csv (§E.1, §E.7 step 11, §F.3)
      module_id, J1, J2, J3, J4, J5, J6, mode, tag, date,
      operator
      §E.1 table column header (Module, J1..J6, Mode) + §E.7
      step 11 'tag with post_rework' + standard date/operator
      audit columns; the .tex does not enumerate this CSV's
      columns explicitly.

  dmm_inventory.csv (§F.7 step 1)
      dmm_model, accuracy_class_pct, date, operator
      §F.7 step 1 says "Record DMM model and accuracy class";
      date and operator are the standard audit pair other
      hardware logs carry.

Exit code: 0 if all required files PASS; 1 if any are
MISSING or MALFORMED. Created templates count as MISSING for
this run (the operator still has to fill them).

Implements: §F.6, §F.7 (with G-DMMx columns), §E.1/§E.7/§F.3
header conformance, §F.7 step 1 DMM inventory.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


@dataclass(frozen=True)
class CsvSpec:
    """One required CSV: filename + required column set + spec cite."""

    filename: str
    columns: Tuple[str, ...]
    cite: str


REQUIRED_CSVS: Tuple[CsvSpec, ...] = (
    CsvSpec(
        filename="dc_bias_check.csv",
        columns=(
            "module_id", "range", "condition",
            "V_DC_P1_GND_mV", "V_DC_P2_GND_mV", "V_DC_DIFF_mV",
            "V_DD_V", "date", "operator",
        ),
        cite="§F.6",
    ),
    CsvSpec(
        filename="resistor_inventory.csv",
        columns=(
            "load_id", "nominal_ohm", "measured_ohm",
            "T_C", "operator", "timestamp",
            "lab_dmm_ohm", "lab_dmm_model",
            "lab_dmm_accuracy_class_pct",
        ),
        cite="§F.7 + G-DMMx",
    ),
    CsvSpec(
        filename="jumper_state.csv",
        columns=(
            "module_id", "J1", "J2", "J3", "J4", "J5", "J6",
            "mode", "tag", "date", "operator",
        ),
        cite="§E.1/§E.7/§F.3",
    ),
    CsvSpec(
        filename="dmm_inventory.csv",
        columns=("dmm_model", "accuracy_class_pct", "date", "operator"),
        cite="§F.7 step 1",
    ),
)


@dataclass
class FileResult:
    spec: CsvSpec
    status: str  # "PASS" / "MISSING" / "MALFORMED"
    missing_columns: Tuple[str, ...] = ()
    template_created: bool = False


def check_one(hardware_dir: Path, spec: CsvSpec) -> FileResult:
    """Check one required CSV against its spec; create header template if absent."""
    path = hardware_dir / spec.filename
    if not path.exists():
        _write_header_template(path, spec.columns)
        return FileResult(
            spec=spec, status="MISSING", template_created=True,
        )
    columns = _read_header(path)
    if columns is None:
        return FileResult(
            spec=spec, status="MALFORMED",
            missing_columns=spec.columns,
        )
    missing = tuple(c for c in spec.columns if c not in columns)
    if missing:
        return FileResult(
            spec=spec, status="MALFORMED", missing_columns=missing,
        )
    return FileResult(spec=spec, status="PASS")


def _read_header(path: Path) -> List[str] | None:
    """Return the header row, or None if the file is empty / unreadable."""
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            try:
                return next(reader)
            except StopIteration:
                return None
    except OSError:
        return None


def _write_header_template(path: Path, columns: Tuple[str, ...]) -> None:
    """Create a header-only CSV at path with the given column set."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)


def render_report(results: List[FileResult]) -> str:
    """Pretty-print the per-file status table for the lab notebook."""
    lines: List[str] = ["hardware/ CSV header conformance"]
    lines.append("-" * len(lines[0]))
    for r in results:
        head = f"  [{r.status:<9}] {r.spec.filename} ({r.spec.cite})"
        lines.append(head)
        if r.template_created:
            lines.append("              -> header-only template created")
        if r.missing_columns:
            lines.append(
                f"              -> missing columns: {list(r.missing_columns)}"
            )
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_logs",
        description=(
            "Validate hardware/ CSV headers against the v4.0c §F-cited "
            "schemas. Creates header-only templates for missing files."
        ),
    )
    parser.add_argument(
        "--hardware-dir", type=Path, default=Path("hardware"),
        help="Directory to scan (default: ./hardware).",
    )
    args = parser.parse_args(argv)
    if not args.hardware_dir.is_dir():
        args.hardware_dir.mkdir(parents=True, exist_ok=True)
    results = [check_one(args.hardware_dir, s) for s in REQUIRED_CSVS]
    print(render_report(results))
    return 0 if all(r.status == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
