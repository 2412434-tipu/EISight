"""eisight_logger.gates -- v4.0c quality-gate evaluators.

Three independent gate evaluators with a shared tri-state
verdict surface:

  G-DC3 (§E.11)   -- DC-bias differential threshold gate.
  G-SAT (§F.10.a) -- saturation/clipping gate from cal-table residuals.
  G-LIN (§F.10.b) -- amplitude linearity across excitation ranges.

Public surface (explicit; no wildcard re-export):

  GateVerdict, GateReport, write_text, write_json,
  evaluate_g_dc3, evaluate_g_sat, evaluate_g_lin.

Anything not listed above stays a private detail of the
relevant submodule. Notably, G_SAT_FAILURE_COLUMNS and
build_g_sat_failures are submodule-level on g_sat.py because
they form a cross-module schema contract with trusted_band.py
(import them via eisight_logger.gates.g_sat directly when
that contract is being exercised).
"""

from eisight_logger.gates.common import (
    GateReport,
    GateVerdict,
    write_json,
    write_text,
)
from eisight_logger.gates.g_dc3 import evaluate_g_dc3
from eisight_logger.gates.g_lin import evaluate_g_lin
from eisight_logger.gates.g_sat import evaluate_g_sat

__all__ = [
    "GateReport",
    "GateVerdict",
    "evaluate_g_dc3",
    "evaluate_g_lin",
    "evaluate_g_sat",
    "write_json",
    "write_text",
]
