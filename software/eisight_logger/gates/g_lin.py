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

from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple, Union

import pandas as pd

from eisight_logger.gates.common import (
    GateReport,
    GateVerdict,
    aggregate_verdict,
    write_report_artifacts,
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
    trusted_band_freqs: Optional[
        Union[Iterable[float], Mapping[str, Iterable[float]]]
    ] = None,
) -> GateReport:
    """Evaluate §F.10.b G-LIN from two §I.6 calibration tables.

    cal_r4_df / cal_r2_df are §I.6 cal tables collected at
    Range 4 and Range 2 respectively. Both must contain the
    test load and the anchor load. Returns a GateReport with
    one per-item row per (module, frequency_hz) evaluation.

    trusted_band_freqs accepts either:
      - a Mapping[module_id, Mapping[range_setting, Iterable[float]]]
        (the bench-safe form produced by run_g_lin from a
        range-aware trusted-band CSV -- a frequency must be trusted
        in both input ranges for the module);
      - a Mapping[module_id, Iterable[float]] (the bench-safe
        legacy form -- per-module trusted set; a frequency trusted
        on Module A does NOT authorize that frequency for Module B);
      - or a flat Iterable[float] (back-compat for single-module
        library tests; applied globally across modules).

    The CLI runner (run_g_lin) always passes the per-module
    Mapping form via _trusted_freqs_from_csv. When None, all
    frequencies common to both tables are evaluated --
    conservative and surfaces out-of-band non-linearity in the
    report, but a single bad edge frequency would FAIL.

    Required-evidence contract: the module universe is the UNION
    of module_id values in cal_r4_df and cal_r2_df. Each module
    must be present in both ranges, must carry both test_load and
    anchor_load in both ranges, and must have at least one
    overlapping frequency (post trusted-band restriction). Any
    module that fails any precondition is NOT_EVALUATED at the
    module level. Overall verdict is PASS only when every module
    in the universe was evaluated and PASSed; any
    NOT_EVALUATED / WARN / FAIL module flips the overall to a
    non-pass state. The Range-2 cal MUST come from the SAME
    module_id as the Range-4 cal -- pooling different module_ids
    measures inter-module variance, not amplitude linearity.
    """
    if cal_r4_df.empty or cal_r2_df.empty:
        return GateReport(
            gate_id="G-LIN",
            verdict=GateVerdict.NOT_EVALUATED,
            summary=(
                "G-LIN: empty calibration table on at least one range "
                "-- gate NOT_EVALUATED (unsafe-as-PASS)"
            ),
            details={
                "r4_row_count": int(len(cal_r4_df)),
                "r2_row_count": int(len(cal_r2_df)),
                "test_load_id": test_load_id,
                "anchor_load_id": anchor_load_id,
            },
            per_item=pd.DataFrame(),
        )

    r4 = _coerce(cal_r4_df)
    r2 = _coerce(cal_r2_df)

    (
        per_module_range_freqs,
        per_module_freqs,
        eval_freqs,
    ) = _normalize_trusted_freqs(trusted_band_freqs)
    if (
        per_module_range_freqs is not None
        and not per_module_range_freqs
        and per_module_freqs is None
        and eval_freqs is None
    ) or (
        per_module_freqs is not None
        and not per_module_freqs
        and per_module_range_freqs is None
        and eval_freqs is None
    ) or (eval_freqs is not None and not eval_freqs):
        return GateReport(
            gate_id="G-LIN",
            verdict=GateVerdict.NOT_EVALUATED,
            summary=(
                "G-LIN: trusted_band_freqs is empty -- "
                "no overlap to evaluate -- NOT_EVALUATED"
            ),
            details={
                "test_load_id": test_load_id,
                "anchor_load_id": anchor_load_id,
                "trusted_band_restricted": True,
            },
            per_item=pd.DataFrame(),
        )

    r4_modules = set(r4["module_id"].astype(str).unique())
    r2_modules = set(r2["module_id"].astype(str).unique())
    module_universe = sorted(r4_modules | r2_modules)
    shared_modules = sorted(r4_modules & r2_modules)

    per_item_rows: List[dict] = []
    modules_not_evaluated: List[Tuple[str, str]] = []
    modules_evaluated: List[str] = []

    for module_id in module_universe:
        if module_id not in r4_modules:
            modules_not_evaluated.append((
                module_id, "absent from Range-4 cal"
            ))
            continue
        if module_id not in r2_modules:
            modules_not_evaluated.append((
                module_id, "absent from Range-2 cal"
            ))
            continue

        r4_mod = r4[r4["module_id"].astype(str) == module_id]
        r2_mod = r2[r2["module_id"].astype(str) == module_id]
        r4_ranges = set(r4_mod["range_setting"].astype(str).unique())
        r2_ranges = set(r2_mod["range_setting"].astype(str).unique())
        r4_loads = set(r4_mod["load_id"].astype(str).unique())
        r2_loads = set(r2_mod["load_id"].astype(str).unique())
        missing_in_r4 = [
            ld for ld in (test_load_id, anchor_load_id) if ld not in r4_loads
        ]
        missing_in_r2 = [
            ld for ld in (test_load_id, anchor_load_id) if ld not in r2_loads
        ]
        if missing_in_r4 or missing_in_r2:
            modules_not_evaluated.append((
                module_id,
                f"missing R4={missing_in_r4} R2={missing_in_r2}",
            ))
            continue

        r_test = _r_test_actual(r4_mod, test_load_id)
        if r_test is None or r_test == 0.0:
            modules_not_evaluated.append((
                module_id, "missing/zero actual_ohm for test load"
            ))
            continue
        z_r4 = _calibrated_z_per_freq(
            r4_mod, test_load_id, anchor_load_id, r_test,
        )
        z_r2 = _calibrated_z_per_freq(
            r2_mod, test_load_id, anchor_load_id, r_test,
        )
        common_freqs = sorted(set(z_r4) & set(z_r2))
        # Per-module trusted-band restriction: a frequency trusted
        # on Module A is NOT authorized for Module B. Pure-iterable
        # back-compat applies the same set globally.
        module_eval_freqs = _resolve_module_freqs(
            module_id, r4_ranges, r2_ranges,
            per_module_range_freqs, per_module_freqs, eval_freqs,
        )
        if module_eval_freqs is not None:
            common_freqs = [f for f in common_freqs if f in module_eval_freqs]
        if not common_freqs:
            modules_not_evaluated.append((
                module_id,
                "no overlapping frequencies"
                + (
                    " (after per-module trusted-band restriction)"
                    if module_eval_freqs is not None
                    else ""
                ),
            ))
            continue

        modules_evaluated.append(module_id)
        for f in common_freqs:
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
            verdict=GateVerdict.NOT_EVALUATED,
            summary=(
                "G-LIN: NOT_EVALUATED -- no module had both required "
                f"loads ({test_load_id}, {anchor_load_id}) and an "
                "overlapping frequency on both ranges"
            ),
            details={
                "test_load_id": test_load_id,
                "anchor_load_id": anchor_load_id,
                "module_universe": module_universe,
                "shared_modules": shared_modules,
                "modules_not_evaluated": [
                    {"module_id": m, "reason": r}
                    for m, r in modules_not_evaluated
                ],
            },
            per_item=per_item,
        )

    rolled = aggregate_verdict(per_item["verdict"])
    # Severity: PASS only if every module in the universe is PASS.
    # FAIL > WARN > NOT_EVALUATED > PASS for the rolled-up overall.
    if modules_not_evaluated and rolled == GateVerdict.PASS:
        overall = GateVerdict.NOT_EVALUATED
    else:
        overall = rolled
    summary = (
        f"G-LIN: {overall.value} on test load {test_load_id} "
        f"(anchor {anchor_load_id}; max |diff| = "
        f"{float(per_item['diff_pct'].max()):.2f}% over "
        f"{len(per_item)} frequency point(s); threshold "
        f"{pass_threshold_pct:g}%)"
    )
    if modules_not_evaluated:
        summary += (
            f" -- modules NOT_EVALUATED: "
            f"{[m for m, _ in modules_not_evaluated]}"
        )
    details = {
        "test_load_id": test_load_id,
        "anchor_load_id": anchor_load_id,
        "pass_threshold_pct": pass_threshold_pct,
        "max_diff_pct": float(per_item["diff_pct"].max()),
        "evaluated_freq_count": int(len(per_item)),
        "trusted_band_restricted": (
            per_module_range_freqs is not None
            or per_module_freqs is not None
            or eval_freqs is not None
        ),
        "trusted_band_per_module": (
            per_module_range_freqs is not None or per_module_freqs is not None
        ),
        "trusted_band_range_aware": per_module_range_freqs is not None,
        "module_universe": module_universe,
        "modules_evaluated": sorted(modules_evaluated),
        "modules_not_evaluated": [
            {"module_id": m, "reason": r}
            for m, r in modules_not_evaluated
        ],
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
    if "range_setting" not in out.columns:
        out["range_setting"] = ""
    out["range_setting"] = out["range_setting"].fillna("").astype(str)
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


def _normalize_trusted_freqs(
    trusted_band_freqs,
) -> Tuple[
    Optional[Dict[str, Dict[str, Set[float]]]],
    Optional[Dict[str, Set[float]]],
    Optional[Set[float]],
]:
    """Normalize trusted_band_freqs into (module/range, module, global).

    Exactly one output is non-None when the caller supplied a
    value: a nested Mapping yields per-module/per-range (the
    bench-safe CSV form), a flat Mapping yields per_module, and an
    Iterable yields global (back-compat). None passes through
    unchanged.
    """
    if trusted_band_freqs is None:
        return None, None, None
    if isinstance(trusted_band_freqs, Mapping):
        if any(isinstance(v, Mapping) for v in trusted_band_freqs.values()):
            per_module_range: Dict[str, Dict[str, Set[float]]] = {}
            for module_id, by_range in trusted_band_freqs.items():
                if not isinstance(by_range, Mapping):
                    raise ValueError(
                        "trusted_band_freqs cannot mix range-aware and "
                        "range-less module entries"
                    )
                per_module_range[str(module_id)] = {
                    str(range_setting): {float(f) for f in freqs}
                    for range_setting, freqs in by_range.items()
                }
            return per_module_range, None, None
        per_module: Dict[str, Set[float]] = {
            str(k): {float(f) for f in v}
            for k, v in trusted_band_freqs.items()
        }
        return None, per_module, None
    flat = {float(f) for f in trusted_band_freqs}
    return None, None, flat


def _resolve_module_freqs(
    module_id: str,
    r4_ranges: Set[str],
    r2_ranges: Set[str],
    per_module_range_freqs: Optional[Dict[str, Dict[str, Set[float]]]],
    per_module_freqs: Optional[Dict[str, Set[float]]],
    eval_freqs: Optional[Set[float]],
) -> Optional[Set[float]]:
    """Per-module trusted frequency set (or global fall-through).

    The per-module mapping is the canonical bench form: a module
    with no entry in the mapping (or with an empty set) is treated
    as having NO trusted frequencies, NOT as 'unrestricted' --
    silently falling through to the union would let Module A's
    trusted band authorize Module B by accident, which is
    exactly the bug the audit flagged.
    """
    if per_module_range_freqs is not None:
        by_range = per_module_range_freqs.get(module_id, {})
        required_ranges = sorted(r4_ranges | r2_ranges)
        if not required_ranges:
            return set()
        freq_sets = []
        for range_setting in required_ranges:
            freqs = by_range.get(range_setting)
            if not freqs:
                return set()
            freq_sets.append(set(freqs))
        return set.intersection(*freq_sets) if freq_sets else set()
    if per_module_freqs is not None:
        return per_module_freqs.get(module_id, set())
    return eval_freqs


def _trusted_freqs_from_csv(
    path: Union[Path, str],
) -> Dict[str, Dict[str, Set[float]]]:
    """Per-module trusted-band frequencies from a merged §I.5/§I.6 CSV.

    Either of run_trusted_band's outputs (merged_raw, merged_cal)
    works as input -- both carry module_id, range_setting,
    frequency_hz, and the "True"/"False"/"" trusted_flag encoding
    locked in raw_writer.py. Returns
    ``{module_id: {range_setting: {frequency_hz, ...}}}`` over
    rows whose trusted_flag == "True".

    The §H.5 trusted band is module-level. A flat global frequency
    set would let a frequency trusted on Module A authorize that
    same frequency on Module B, which is unsafe -- per-module
    isolation is enforced by returning the dict.

    Raises ValueError if the trusted-band CSV lacks the ``module_id``
    or ``range_setting`` column entirely; that file cannot be safely
    consumed by G-LIN under module/range isolation. The legacy
    global-set form is intentionally not preserved as a fallback.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if "module_id" not in df.columns:
        raise ValueError(
            f"trusted-band CSV {path} lacks 'module_id' column; "
            "G-LIN requires per-module trusted-frequency isolation, "
            "so a global-set fallback is unsafe and not provided"
        )
    if "range_setting" not in df.columns:
        raise ValueError(
            f"trusted-band CSV {path} lacks 'range_setting' column; "
            "G-LIN requires range-aware trusted-frequency isolation"
        )
    trusted_rows = df[df["trusted_flag"] == "True"]
    if trusted_rows.empty:
        return {}
    out: Dict[str, Dict[str, Set[float]]] = {}
    freqs = pd.to_numeric(trusted_rows["frequency_hz"], errors="raise").astype(float)
    for module_id, range_setting, freq in zip(
        trusted_rows["module_id"].astype(str).tolist(),
        trusted_rows["range_setting"].fillna("").astype(str).tolist(),
        freqs.tolist(),
    ):
        out.setdefault(module_id, {}).setdefault(range_setting, set()).add(
            float(freq)
        )
    return out


def run_g_lin(
    cal_r4_path: Union[Path, str],
    cal_r2_path: Union[Path, str],
    output_dir: Optional[Union[Path, str]] = None,
    trusted_band_csv: Optional[Union[Path, str]] = None,
    *,
    pass_threshold_pct: float = G_LIN_PASS_THRESHOLD_PCT,
    test_load_id: str = G_LIN_TEST_LOAD_ID,
    anchor_load_id: str = G_LIN_ANCHOR_LOAD_ID,
    fmt: str = "both",
) -> GateReport:
    """Read two §I.6 cal tables; evaluate G-LIN; optionally write.

    Composes pd.read_csv x2 + _trusted_freqs_from_csv (when
    trusted_band_csv is supplied) + evaluate_g_lin +
    write_report_artifacts. Returns the GateReport regardless
    of whether output_dir is supplied.

    trusted_band_csv accepts either of run_trusted_band's
    outputs (merged_raw or merged_cal); the trusted-frequency
    set is extracted from rows with trusted_flag == "True".
    Pass None to evaluate every overlapping (Range-4, Range-2)
    frequency -- conservative, but a single bad edge frequency
    would FAIL the module under that mode.
    """
    cal_r4 = pd.read_csv(cal_r4_path, dtype=str, keep_default_na=False)
    cal_r2 = pd.read_csv(cal_r2_path, dtype=str, keep_default_na=False)
    trusted_freqs = (
        _trusted_freqs_from_csv(trusted_band_csv)
        if trusted_band_csv is not None
        else None
    )
    report = evaluate_g_lin(
        cal_r4, cal_r2,
        pass_threshold_pct=pass_threshold_pct,
        test_load_id=test_load_id,
        anchor_load_id=anchor_load_id,
        trusted_band_freqs=trusted_freqs,
    )
    if output_dir is not None:
        write_report_artifacts(report, output_dir, "g_lin", fmt=fmt)
    return report
