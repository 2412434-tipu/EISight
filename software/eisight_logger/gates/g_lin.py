"""g_lin.py -- §F.10.b amplitude-linearity gate (G-LIN) evaluator.

G-LIN proves the system is in its small-signal linear regime
by comparing the calibrated |Z| of one resistor across two
excitation ranges. If |Z|_Range2 differs from |Z|_Range4 by
more than 2 % across the trusted band, the apparent impedance
is amplitude-dependent -- a non-linearity that breaks every
EIS feature relying on superposition (§F.10.b "Why this gate").

Math (§H.2 identity, applied to each Range independently):

  |Z_test|_Rn(f) = R_test_actual * GF_test_Rn(f) / GF_anchor_Rn(f)
  diff_pct(f)    = | |Z|_R2(f) - |Z|_R4(f) | / R_test_actual * 100

R_test_actual is taken from the Range-4 cal_df's actual_ohm
column (the lab-DMM value cross-checked per G-DMMx). Both the
Range-2 and Range-4 cal tables must carry the same anchor
load_id -- §F.10.b requires "one additional 1k sweep at
Range 2" specifically because the gain factor is range-
dependent (datasheet Table 17).

Per-frequency verdict:

  PASS: diff_pct(f) <= pass_threshold (default 2 %)
  FAIL: otherwise

§F.10.b does not define a warning band, so G-LIN is binary
(PASS / FAIL) under the GateVerdict tri-state.

Module verdict: PASS only if every frequency in the evaluation
set passes. The evaluation set is trusted_band_freqs if
supplied (recommended -- routes through trusted_band.evaluate_
trusted_band's frequency_hz column where trusted is True), or
all frequencies present in both Range-4 and Range-2 cal tables
otherwise.

Implements: §F.10.b (G-LIN). Consumes: §I.6 calibration CSV
(one per range).
"""

from __future__ import annotations

from typing import Iterable, List, Optional

import pandas as pd

from eisight_logger.gates.common import (
    GateReport,
    GateVerdict,
    aggregate_verdict,
)

G_LIN_PASS_THRESHOLD_PCT = 2.0
G_LIN_TEST_LOAD_ID = "R470_01"
G_LIN_ANCHOR_LOAD_ID = "R1k_01"


def evaluate_g_lin(
    cal_r4_df: pd.DataFrame,
    cal_r2_df: pd.DataFrame,
    *,
    pass_threshold_pct: float = G_LIN_PASS_THRESHOLD_PCT,
    test_load_id: str = G_LIN_TEST_LOAD_ID,
    anchor_load_id: str = G_LIN_ANCHOR_LOAD_ID,
    trusted_band_freqs: Optional[Iterable[float]] = None,
) -> GateReport:
    """Evaluate §F.10.b G-LIN from two §I.6 calibration tables.

    cal_r4_df / cal_r2_df are §I.6 cal tables collected at
    Range 4 and Range 2 respectively. Both must contain the
    test load and the anchor load. Returns a GateReport with
    one per-item row per (module, frequency_hz) evaluation.

    trusted_band_freqs, when supplied, restricts evaluation to
    those frequencies (recommended; pass the frequency_hz
    column of the trusted_band.evaluate_trusted_band result
    filtered to trusted == True). When None, all frequencies
    common to both tables are evaluated -- conservative and
    surfaces out-of-band non-linearity in the report, but a
    single bad edge frequency would otherwise FAIL the module.
    """
    if cal_r4_df.empty or cal_r2_df.empty:
        return GateReport(
            gate_id="G-LIN",
            verdict=GateVerdict.PASS,
            summary=(
                "G-LIN: empty calibration table on at least one range "
                "-- gate not evaluated"
            ),
            details={
                "r4_row_count": int(len(cal_r4_df)),
                "r2_row_count": int(len(cal_r2_df)),
            },
            per_item=pd.DataFrame(),
        )

    r4 = _coerce(cal_r4_df)
    r2 = _coerce(cal_r2_df)

    eval_freqs = (
        {float(f) for f in trusted_band_freqs}
        if trusted_band_freqs is not None
        else None
    )

    per_item_rows: List[dict] = []
    modules = sorted(
        set(r4["module_id"].unique()) & set(r2["module_id"].unique())
    )
    for module_id in modules:
        r_test = _r_test_actual(
            r4[r4["module_id"] == module_id], test_load_id
        )
        if r_test is None or r_test == 0.0:
            continue
        z_r4 = _calibrated_z_per_freq(
            r4[r4["module_id"] == module_id],
            test_load_id, anchor_load_id, r_test,
        )
        z_r2 = _calibrated_z_per_freq(
            r2[r2["module_id"] == module_id],
            test_load_id, anchor_load_id, r_test,
        )
        common_freqs = sorted(set(z_r4) & set(z_r2))
        for f in common_freqs:
            if eval_freqs is not None and f not in eval_freqs:
                continue
            diff_pct = abs(z_r2[f] - z_r4[f]) / r_test * 100.0
            verdict = (
                GateVerdict.PASS.value
                if diff_pct <= pass_threshold_pct
                else GateVerdict.FAIL.value
            )
            per_item_rows.append({
                "module_id": module_id,
                "frequency_hz": f,
                "z_r4_ohm": z_r4[f],
                "z_r2_ohm": z_r2[f],
                "diff_pct": diff_pct,
                "verdict": verdict,
            })

    per_item = pd.DataFrame(per_item_rows)
    if per_item.empty:
        return GateReport(
            gate_id="G-LIN",
            verdict=GateVerdict.PASS,
            summary=(
                "G-LIN: no overlapping frequencies between Range-4 "
                "and Range-2 cal tables -- gate not evaluated"
            ),
            details={
                "test_load_id": test_load_id,
                "anchor_load_id": anchor_load_id,
            },
            per_item=per_item,
        )

    overall = aggregate_verdict(per_item["verdict"])
    summary = (
        f"G-LIN: {overall.value} on test load {test_load_id} "
        f"(anchor {anchor_load_id}; max |diff| = "
        f"{float(per_item['diff_pct'].max()):.2f}% over "
        f"{len(per_item)} frequency point(s); threshold "
        f"{pass_threshold_pct:g}%)"
    )
    details = {
        "test_load_id": test_load_id,
        "anchor_load_id": anchor_load_id,
        "pass_threshold_pct": pass_threshold_pct,
        "max_diff_pct": float(per_item["diff_pct"].max()),
        "evaluated_freq_count": int(len(per_item)),
        "trusted_band_restricted": eval_freqs is not None,
    }

    return GateReport(
        gate_id="G-LIN",
        verdict=overall,
        summary=summary,
        details=details,
        per_item=per_item,
    )


def _coerce(cal_df: pd.DataFrame) -> pd.DataFrame:
    out = cal_df.copy()
    for col in ("frequency_hz", "gain_factor", "actual_ohm"):
        out[col] = pd.to_numeric(out[col], errors="raise")
    return out


def _calibrated_z_per_freq(
    mod_df: pd.DataFrame,
    test_load_id: str,
    anchor_load_id: str,
    r_test_actual: float,
) -> dict:
    """|Z_test|(f) under the same-range anchor GF, keyed by frequency.

    Returns {frequency_hz: |Z| ohm} for every frequency where
    both test and anchor cal rows exist with non-zero anchor GF.
    Other frequencies are dropped silently; the caller's
    common-frequency intersection then produces the evaluation
    set.
    """
    test = mod_df[mod_df["load_id"] == test_load_id]
    anchor = mod_df[mod_df["load_id"] == anchor_load_id]
    if test.empty or anchor.empty:
        return {}
    anchor_gf = dict(zip(
        anchor["frequency_hz"].astype(float),
        anchor["gain_factor"].astype(float),
    ))
    out: dict = {}
    for _, row in test.iterrows():
        f = float(row["frequency_hz"])
        gf_anchor = anchor_gf.get(f)
        if gf_anchor is None or gf_anchor == 0.0:
            continue
        out[f] = r_test_actual * float(row["gain_factor"]) / gf_anchor
    return out


def _r_test_actual(
    mod_df: pd.DataFrame, test_load_id: str
) -> Optional[float]:
    rows = mod_df[mod_df["load_id"] == test_load_id]
    if rows.empty:
        return None
    return float(rows["actual_ohm"].iloc[0])
