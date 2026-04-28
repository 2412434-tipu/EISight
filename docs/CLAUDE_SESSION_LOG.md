# Claude session log

Per-session notes for the EISight v4.0c laptop-side pipeline
work. One entry per session; newest first. Format intentionally
terse -- the commits and code are the source of truth, this file
just bookmarks where each session started and stopped.

## 2026-04-28 session (continued -- ended early at gates)

Landed: qc.py, gates/ package (common.py, g_dc3.py, g_sat.py,
        g_lin.py, __init__.py), one-line cross-reference comment
        in trusted_band._g_sat_freqs_for.

Commits:
  - 7379cbc Add qc.py: §H.8 sweep QC rules using phase.py
            primitives
  - 55f7b0a Add gates/ package: G-DC3, G-SAT, G-LIN evaluators
            with shared GateReport

Remaining: cli.py, plots.py, scripts/validate_logs.py,
           tests/synthetic/generate_resistor_jsonl.py,
           test_schemas.py, test_calibration.py,
           test_qc.py, test_gates.py,
           pyproject.toml, README.md, pytest run

Next session resumes at: cli.py

Open notes:
  - Session ended after gates/ landed; cli.py was the planned
    third file but was deferred to keep the gates split clean
    rather than rushing the CLI layer.
  - gates is a *package* (gates/__init__.py with explicit
    re-exports), not a module. Multi-purpose justified the
    split per the CLAUDE.md 300-line rule: three independent
    gates with different inputs (DC-bias CSV vs cal table vs
    two cal tables) and different math. Every file in the
    package is under 300 lines (largest is g_sat.py at 269).
  - Public surface from `eisight_logger.gates`: GateVerdict,
    GateReport, write_text, write_json, evaluate_g_dc3,
    evaluate_g_sat, evaluate_g_lin. G_SAT_FAILURE_COLUMNS and
    build_g_sat_failures stay submodule-level on g_sat.py
    because they form a cross-module schema contract with
    trusted_band.py; importers reach in via
    `eisight_logger.gates.g_sat`.
  - GateVerdict inherits from str so json.dumps(member)
    serializes as "PASS"/"WARN"/"FAIL" without a custom
    encoder. Per-item DataFrames store .value strings, not
    enum members, so a CSV round-trip preserves the encoding.
  - aggregate_verdict returns PASS on empty input by
    convention -- "nothing evaluated" is not a failure.
  - G-DC3 is tri-state (50/100 mV thresholds per §E.11). G-SAT
    and G-LIN are binary PASS/FAIL under the GateVerdict enum
    because §F.10.a / §F.10.b do not define warning bands;
    they emit only PASS or FAIL members.
  - G-SAT module verdict uses a contiguous-band-coverage rule
    (≥20 kHz of frequencies with |epsilon_R|<=5%) per §F.10.a
    step 4. _max_contiguous_pass_band_hz counts frequency span
    in Hz, not point count, so a sparse-but-wide band is
    rewarded.
  - G-LIN takes two cal tables (Range 4 + Range 2). Optional
    trusted_band_freqs parameter restricts evaluation; when
    None, all overlapping frequencies are evaluated and a
    single bad edge frequency would FAIL the module. Pass
    trusted_band_freqs = trusted_band.evaluate_trusted_band(
    ...).query("trusted")["frequency_hz"] when wiring through
    cli.py.
  - qc.py uses phase.py primitives (dft_magnitude,
    raw_phase_rad, phase_to_deg) -- the calibration.py
    inline-sqrt miss is not repeated. _flag_phase_jumps is
    intentionally CAL-only; sample sweeps have legitimate
    frequency-dependent phase, so jump-based QC there would
    be false-positive heavy. Documented in the helper
    docstring.
  - qc.py does NOT implement §H.8's "missing frequency points"
    rule because the §I.5 raw CSV does not carry start_hz /
    stop_hz / points; that check belongs in the listener
    stage. Documented in the module docstring.

## 2026-04-28 session

Landed: phase.py, calibration.py, trusted_band.py
        (DEFAULT_BAUD refactor in serial_listener)

Commits:
  - f2ff697 Add phase.py: §H.2 magnitude/phase primitives;
            promote DEFAULT_BAUD to module constant in
            serial_listener
  - 4e23791 Add calibration: §H.2 GF + system phase from CAL
            sweeps, §I.6 CSV writer
  - 0b85ce6 Add trusted_band: §H.5 per-frequency band membership
            from cal+raw

Remaining: qc.py, gates.py, plots.py, cli.py,
           scripts/validate_logs.py,
           tests/synthetic/generate_resistor_jsonl.py,
           test_schemas.py, test_calibration.py,
           test_qc.py, test_gates.py,
           pyproject.toml, README.md, pytest run

Next session resumes at: qc.py

Open notes:
  - calibration.py inlines one np.sqrt for per-repeat CV
    instead of routing through phase.dft_magnitude. Acceptable;
    route through phase.py in any future module that does
    magnitude or atan2 math.
  - trusted_band.py was trimmed from 395 to 344 lines (target
    was ~280); same logic, dropped private helper docstrings,
    inlined _anchor_gf_lookup, single-line reason f-strings,
    pruned the module docstring's API-surface paragraphs that
    duplicated the public function docstrings. Still 44 lines
    over the CLAUDE.md 300 heuristic; staying single-file
    rather than splitting since the criterion helpers are
    tightly coupled to evaluate_trusted_band's per-frequency
    loop.
  - GateVerdict tri-state convention (PASS / WARN / FAIL) is
    locked but unimplemented -- gates.py in the next session.
  - trusted_band's criterion 7 (G-SAT) accepts None and skips
    silently; gates.py needs to produce a g_sat_failures
    DataFrame with columns (module_id, frequency_hz, load_id)
    for the wiring to take effect.
