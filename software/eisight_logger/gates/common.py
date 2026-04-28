"""common.py -- Shared scaffolding for the v4.0c gate evaluators.

The three gates (G-DC3 §E.11, G-SAT §F.10.a, G-LIN §F.10.b) are
independent in math and inputs but share the same tri-state
verdict surface and report-artifact format. This module locks
that surface so cli.py and the report writers do not need
gate-specific code paths.

GateVerdict is the canonical PASS / WARN / FAIL enum locked in
the project CLAUDE.md. GateReport bundles a gate's overall
verdict with its per-item evaluation table for downstream
inspection. write_text and write_json render a GateReport to
disk in the two formats cli.py exposes.

aggregate_verdict is the canonical worst-case roll-up: any FAIL
beats any WARN beats PASS. Empty input returns PASS by
convention -- nothing was evaluated, so nothing failed.

Implements: shared report contract for gates/g_dc3.py,
gates/g_sat.py, gates/g_lin.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Union

import pandas as pd


class GateVerdict(str, Enum):
    """Tri-state gate outcome.

    Values are uppercase strings so the JSON / text artifacts
    self-document. Inheriting from str makes the members
    trivially JSON-serializable -- json.dumps(verdict) produces
    "PASS" / "WARN" / "FAIL" without a custom encoder.
    """

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


def aggregate_verdict(verdicts: Iterable[Any]) -> GateVerdict:
    """Worst-case aggregator: any FAIL -> FAIL; any WARN -> WARN; else PASS.

    Used by every gate to roll a per-item verdict column up to
    the report-level overall. verdicts may be a Series of
    strings, an iterable of GateVerdict members, or a mix; each
    value is coerced through GateVerdict(...) which raises on
    an unknown string. Empty input returns PASS.
    """
    seen = set()
    for v in verdicts:
        seen.add(GateVerdict(v) if not isinstance(v, GateVerdict) else v)
    if GateVerdict.FAIL in seen:
        return GateVerdict.FAIL
    if GateVerdict.WARN in seen:
        return GateVerdict.WARN
    return GateVerdict.PASS


@dataclass
class GateReport:
    """One gate's evaluation result.

    gate_id is the canonical §-cited id: "G-DC3", "G-SAT",
        "G-LIN". cli.py and the report writers do not branch on
        it -- they just pass it through into the artifact name
        / header line.
    verdict is the rolled-up overall outcome (aggregate_verdict
        applied to per_item['verdict']).
    summary is a one-line human description suitable for the
        first line of the text report.
    details is a free-form dict of gate-specific summary
        numbers (e.g. G-DC3 max-diff per module, G-SAT
        trusted-band-width per module). Must be
        JSON-serializable; write_json falls back to str() for
        unknown types but a non-trivial object is a smell.
    per_item is the row-level verdict table; column set is
        gate-specific. Always carries a 'verdict' column whose
        values are GateVerdict.value strings ("PASS" / "WARN" /
        "FAIL") -- not the enum members, so a CSV round-trip
        through pandas keeps the same string encoding.
    """

    gate_id: str
    verdict: GateVerdict
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)
    per_item: pd.DataFrame = field(default_factory=pd.DataFrame)


def write_text(report: GateReport, path: Union[Path, str]) -> None:
    """Render a GateReport as plain text for the lab notebook.

    Format:

        GATE <gate_id> -- <verdict>
        <summary>

        Details:
          <key>: <value>
          ...

        Per-item:
          <pandas to_string of per_item>

    Meant to be pasted into the lab notebook as-is or diffed
    across sessions.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = [
        f"GATE {report.gate_id} -- {report.verdict.value}",
        report.summary,
        "",
    ]
    if report.details:
        lines.append("Details:")
        for k in sorted(report.details):
            lines.append(f"  {k}: {report.details[k]}")
        lines.append("")
    if not report.per_item.empty:
        lines.append("Per-item:")
        lines.append(report.per_item.to_string(index=False))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(report: GateReport, path: Union[Path, str]) -> None:
    """Render a GateReport as JSON for downstream tooling.

    Format:

        {
          "gate_id": "...",
          "verdict": "PASS|WARN|FAIL",
          "summary": "...",
          "details": {...},
          "per_item": [ {col: val, ...}, ... ]
        }

    per_item rounds through DataFrame.to_dict(orient="records"),
    which preserves column order and produces JSON-native types
    for str / int / float / bool. NaN cells become None so the
    output is strict JSON (no NaN literal). default=str catches
    any non-serializable detail value as a last resort; a
    non-trivial detail object is a gate-side bug.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "gate_id": report.gate_id,
        "verdict": report.verdict.value,
        "summary": report.summary,
        "details": report.details,
        "per_item": _records_with_nan_as_null(report.per_item),
    }
    out.write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _records_with_nan_as_null(df: pd.DataFrame) -> list:
    if df.empty:
        return []
    cleaned = df.where(pd.notna(df), None)
    return cleaned.to_dict(orient="records")


def write_report_artifacts(
    report: GateReport,
    output_dir: Union[Path, str],
    stem: str,
    fmt: str = "both",
) -> None:
    """Write {stem}.txt and/or {stem}.json under output_dir.

    fmt is one of "text", "json", "both". Submodule helper for
    the run_g_* runners; not part of the gates package public
    surface (callers reach in via eisight_logger.gates.common).
    """
    if fmt not in ("text", "json", "both"):
        raise ValueError(
            f"fmt must be 'text', 'json', or 'both' (got {fmt!r})"
        )
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if fmt in ("text", "both"):
        write_text(report, out / f"{stem}.txt")
    if fmt in ("json", "both"):
        write_json(report, out / f"{stem}.json")
