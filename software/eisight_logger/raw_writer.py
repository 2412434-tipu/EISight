"""raw_writer.py -- Long-format CSV writer for the EISight v4.0c raw log.

Implements the §I.5 raw CSV schema. One CSV row per `data` packet,
with the §I.5 firmware-provided columns followed by reserved
columns that downstream layers (calibration, QC, trusted-band)
populate later. The listener leaves those reserved columns empty,
so the file remains a single self-contained artifact analyzable
in one read after every stage has run -- which is the §I.5
invariant.

Spec deviation (v4.0c -> v4.0d patch pending): §I.5 of the .tex
source lists the DFT result columns as `real_raw` and `imag_raw`.
The firmware emits them on the wire as `real` / `imag`
(jsonl.cpp `write_data`), and this CSV preserves the wire-format
names verbatim to avoid silent drift between the two
representations.

State machine
-------------
sweep_begin -> capture per-sweep metadata into _SweepBuffer.
data        -> append a CSV-row dict to the current buffer
               (post-temps left empty for now).
sweep_end   -> back-fill ds18b20_post_c, ad5933_post_c, and
               (if sweep_end.error is non-null) append
               'sweep_end_error=<msg>' to each row's notes.
               Then flush the buffer to disk in arrival order.

Records of type hello, module_id_set, error, self_test_fail,
i2c_scan, reg_sanity, temp_only contribute no CSV row -- they
have no per-frequency point. The listener persists those into
raw.jsonl unchanged.

Conventions for the reserved (downstream-populated) columns
-----------------------------------------------------------
Boolean cells (qc_pass, trusted_flag) follow the pandas default
str-of-bool encoding: the literal strings "True" or "False"
(capitalized) when evaluated, the empty string when not yet
evaluated. Numeric cells (gain_factor, phase_system_deg,
magnitude_calibrated, phase_calibrated_deg) are formatted as
floats; empty when not yet evaluated. qc_reasons is a
semicolon-joined string ("" when no failures). qc.py,
trusted_band.py, and calibration.py must preserve these
conventions so a single read of the CSV produces a
self-consistent dataframe across rows from different stages.

Implements: §I.5 (raw CSV schema).
Mirrors firmware writers: write_sweep_begin, write_data,
write_sweep_end (jsonl.cpp).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from eisight_logger.schemas import (
    DataRecord,
    JsonlRecord,
    SweepBeginRecord,
    SweepEndRecord,
)

# Canonical §I.5 long-format column order. Authoritative for every
# module that reads or writes this CSV; preserve the order on rewrite.
RAW_CSV_COLUMNS: List[str] = [
    # --- §I.5 firmware-provided ---
    "session_id",
    "sweep_id",
    "row_type",
    "module_id",
    "cell_id",
    "sample_id",
    "load_id",
    "frequency_hz",
    "real",          # was real_raw in §I.5; tracks firmware wire format
    "imag",          # was imag_raw in §I.5; tracks firmware wire format
    "status",
    "range_setting",
    "pga_setting",
    "settling_cycles",
    "ds18b20_pre_c",
    "ds18b20_post_c",
    "ad5933_pre_c",
    "ad5933_post_c",
    "operator",
    "notes",
    # --- reserved for downstream layers (empty until populated) ---
    "gain_factor",
    "phase_system_deg",
    "magnitude_calibrated",
    "phase_calibrated_deg",
    "qc_pass",
    "qc_reasons",
    "trusted_flag",
]


@dataclass
class _SweepBuffer:
    """Per-sweep metadata + buffered data rows, flushed on sweep_end.

    seen_indices and last_idx back the duplicate / non-monotonic
    idx checks in RawCsvWriter.on_record. seen_indices is the
    fast membership test; last_idx is the running monotonicity
    cursor. Both are reset implicitly when the buffer is replaced
    on a new sweep_begin.
    """

    meta: SweepBeginRecord
    rows: List[Dict[str, str]] = field(default_factory=list)
    seen_indices: Set[int] = field(default_factory=set)
    last_idx: Optional[int] = None


class RawCsvWriter:
    """Writes the §I.5 long-format CSV one sweep at a time.

    Constructor opens the file in 'w' mode, writing a fresh
    header row -- one listener invocation produces one CSV.
    Resumption after a crash is intentionally out of scope:
    the raw .jsonl on disk is the recovery source of truth.

    data records that arrive without a preceding sweep_begin,
    or whose sweep_id mismatches the current buffer, are
    counted in dropped_data_count and not written. A
    sweep_begin arriving while a previous sweep is still
    open also discards the previous buffer (bumping the
    same counter), since unannotated rows would be unjoinable
    downstream.

    Sequence safety counters (surfaced via ListenerStats and
    bench-CLI exit-code logic):

      dropped_data_count        -- data records that could not be
                                   placed (no open sweep, sweep_id
                                   mismatch, duplicate idx, or
                                   non-monotonic idx). Each kind
                                   also bumps a typed counter below.
      duplicate_idx_count       -- data records sharing an idx
                                   already buffered for the same
                                   sweep_id. Dropped, not written.
      nonmonotonic_idx_count    -- data records whose idx <= the
                                   most recent idx in the buffer.
                                   Dropped, not written.
      mismatched_sweep_id_count -- data records with a sweep_id not
                                   matching the open sweep_begin.
      missing_sweep_end_count   -- sweeps whose buffered rows were
                                   discarded because sweep_end
                                   never arrived (a new sweep_begin
                                   arrived first, or close() ran
                                   while the buffer was open).
      sweep_end_error_count     -- sweep_end records with non-null
                                   error (rows still flushed with
                                   sweep_end_error= tag in notes,
                                   per the §I.5 self-contained-CSV
                                   invariant). Bench gates treat
                                   these as non-pass evidence.
      point_count_mismatch_count -- sweeps whose flushed row count
                                   does not equal sweep_begin.points.
                                   Counts the *sweep*, not rows.

    A bench listener invocation is "clean" iff lines_failed and all
    of the counters above are zero -- the CLI checks
    ListenerStats.is_clean() to decide its exit code.
    """

    def __init__(
        self,
        path: Path,
        operator: str = "",
        sample_id: str = "",
        notes: str = "",
        session_id: Optional[str] = None,
        row_type_override: Optional[str] = None,
        load_id_override: Optional[str] = None,
    ) -> None:
        self.path = Path(path)
        self.operator = operator
        self.sample_id = sample_id
        self.notes = notes
        # If session_id is supplied, it overrides the firmware's
        # empty-string emission. The listener owns this decision.
        self.session_id_override = session_id
        self.row_type_override = row_type_override
        self.load_id_override = load_id_override
        self._buffer: Optional[_SweepBuffer] = None
        self.dropped_data_count = 0
        # Typed sequence-safety counters (see class docstring).
        self.duplicate_idx_count = 0
        self.nonmonotonic_idx_count = 0
        self.mismatched_sweep_id_count = 0
        self.missing_sweep_end_count = 0
        self.sweep_end_error_count = 0
        self.point_count_mismatch_count = 0

        # newline="" is the Python 3 CSV idiom; without it, embedded
        # newlines in quoted fields would not round-trip correctly.
        self._fh = self.path.open("w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(
            self._fh, fieldnames=RAW_CSV_COLUMNS, extrasaction="raise"
        )
        self._writer.writeheader()
        self._fh.flush()

    def on_record(self, record: JsonlRecord) -> None:
        if isinstance(record, SweepBeginRecord):
            if self._buffer is not None:
                # An open buffer at sweep_begin means the previous
                # sweep_end never arrived. Drop the buffered rows --
                # they would be unannotated (no post-temps) and so
                # unjoinable downstream.
                self.dropped_data_count += len(self._buffer.rows)
                self.missing_sweep_end_count += 1
            self._buffer = _SweepBuffer(meta=record)
        elif isinstance(record, DataRecord):
            if self._buffer is None:
                self.dropped_data_count += 1
                self.mismatched_sweep_id_count += 1
                return
            if record.sweep_id != self._buffer.meta.sweep_id:
                self.dropped_data_count += 1
                self.mismatched_sweep_id_count += 1
                return
            # Duplicate / non-monotonic idx within the same sweep
            # is a firmware re-send or scrambled-stream symptom; an
            # accepted duplicate would inflate the per-sweep mean
            # and silently corrupt calibration.
            if record.idx in self._buffer.seen_indices:
                self.dropped_data_count += 1
                self.duplicate_idx_count += 1
                return
            if (
                self._buffer.last_idx is not None
                and record.idx <= self._buffer.last_idx
            ):
                self.dropped_data_count += 1
                self.nonmonotonic_idx_count += 1
                return
            self._buffer.seen_indices.add(record.idx)
            self._buffer.last_idx = record.idx
            self._buffer.rows.append(self._row_for_data(record))
        elif isinstance(record, SweepEndRecord):
            if (
                self._buffer is None
                or record.sweep_id != self._buffer.meta.sweep_id
            ):
                # Stray sweep_end with no matching open sweep.
                self.mismatched_sweep_id_count += 1
                return
            if record.error is not None:
                self.sweep_end_error_count += 1
            expected = self._buffer.meta.points
            if expected and len(self._buffer.rows) != int(expected):
                self.point_count_mismatch_count += 1
            self._flush(record)
            self._buffer = None
        # Other record types (hello, module_id_set, error, etc.)
        # contribute no row; the listener persists them to raw.jsonl.

    def close(self) -> None:
        # An open buffer at close means sweep_end never arrived;
        # post-temps would be unknown, so the buffered rows are
        # dropped from the CSV. raw.jsonl is still complete.
        if self._buffer is not None:
            self.dropped_data_count += len(self._buffer.rows)
            self.missing_sweep_end_count += 1
            self._buffer = None
        self._fh.flush()
        self._fh.close()

    def __enter__(self) -> "RawCsvWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------

    def _row_for_data(self, rec: DataRecord) -> Dict[str, str]:
        assert self._buffer is not None
        meta = self._buffer.meta
        session_id = (
            self.session_id_override
            if self.session_id_override is not None
            else meta.session_id
        )
        row: Dict[str, str] = {col: "" for col in RAW_CSV_COLUMNS}
        row.update({
            "session_id": session_id,
            "sweep_id": rec.sweep_id,
            "row_type": (
                self.row_type_override
                if self.row_type_override is not None
                else meta.row_type
            ),
            # module_id may be None at this point -- emit empty cell.
            "module_id": meta.module_id or "",
            "cell_id": meta.cell_id,
            "sample_id": self.sample_id,
            "load_id": (
                self.load_id_override
                if self.load_id_override is not None
                else meta.load_id
            ),
            # Float precisions match firmware snprintf templates so
            # the CSV is byte-comparable to the JSONL field values.
            "frequency_hz": _fmt_float(rec.frequency_hz, 1),
            "real": str(rec.real),
            "imag": str(rec.imag),
            "status": str(rec.status),
            "range_setting": meta.range,
            "pga_setting": meta.pga,
            "settling_cycles": str(meta.settling_cycles),
            "ds18b20_pre_c": _fmt_optfloat(meta.ds18b20_pre_c, 4),
            "ad5933_pre_c": _fmt_optfloat(meta.ad5933_pre_c, 1),
            "operator": self.operator,
            "notes": self.notes,
        })
        return row

    def _flush(self, end: SweepEndRecord) -> None:
        assert self._buffer is not None
        ds18_post = _fmt_optfloat(end.ds18b20_post_c, 4)
        ad_post = _fmt_optfloat(end.ad5933_post_c, 1)
        for row in self._buffer.rows:
            row["ds18b20_post_c"] = ds18_post
            row["ad5933_post_c"] = ad_post
            if end.error is not None:
                # Preserve the §I.5 self-contained-CSV invariant: a
                # sweep that ended in firmware-reported error is
                # surfaced inside the row, not lost.
                tag = f"sweep_end_error={end.error}"
                base = row.get("notes", "")
                row["notes"] = f"{base}; {tag}" if base else tag
            self._writer.writerow(row)
        self._fh.flush()


def _fmt_float(v: float, decimals: int) -> str:
    return f"{v:.{decimals}f}"


def _fmt_optfloat(v: Optional[float], decimals: int) -> str:
    if v is None:
        return ""
    return f"{v:.{decimals}f}"
