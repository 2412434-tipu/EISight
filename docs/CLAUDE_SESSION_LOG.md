# Claude session log

Per-session notes for the EISight v4.0c laptop-side pipeline
work. One entry per session; newest first. Format intentionally
terse -- the commits and code are the source of truth, this file
just bookmarks where each session started and stopped.

## 2026-04-29 session (continued -- test suite + operator README)

Landed: tests/synthetic/generate_resistor_jsonl.py (ideal-
        resistor JSONL fixture); tests/synthetic/test_schemas.py
        (33 tests); tests/synthetic/test_calibration.py (5
        tests); tests/synthetic/test_qc.py (14 tests);
        tests/synthetic/test_gates.py (15 tests);
        software/eisight_logger/README.md (242-line operator
        manual with end-to-end synthetic walk-through).

Commits:
  - 48e2a72 Add tests/synthetic/generate_resistor_jsonl.py:
            ideal-resistor JSONL fixture for tests
  - b7ab7ab Add tests/synthetic/test_schemas.py: 33 tests
            covering §I.2.a int16 boundary, required-field
            presence, empty-string-vs-null conventions,
            and i2c_scan addr grammar
  - bfef18a Add tests/synthetic/test_calibration.py: 5 tests
            covering §H.2 GF math, §I.6 round-trip, and
            runner contract
  - 5e0bd68 Add tests/synthetic/test_qc.py: 14 tests
            covering §H.8 QC rules and §I.5 boolean
            encoding round-trip
  - fea9997 Add tests/synthetic/test_gates.py: 15 tests
            covering G-DC3/G-SAT/G-LIN evaluators and
            runner contract
  - 1d03941 Add eisight_logger/README.md: operator manual
            with synthetic walk-through

Remaining: plots.py (pending; the `plot` CLI subcommand
           currently raises NotImplementedError), full
           pytest run with coverage measurement, top-level
           README.md update to reflect the now-complete
           eisight_logger.

Next session resumes at: plots.py.

Open notes:
  - 67 tests pass in 1.72s. Coverage of the four most
    load-bearing modules is intentionally biased toward
    contract violations (extra="forbid" drift, int16
    boundaries on both ends, runner-return-vs-write
    semantics) rather than happy-path duplication of the
    runner code.
  - test_qc.py findings (worth carrying forward): pandas
    coerces a Python `bool` list to numpy.bool_ at
    DataFrame construction, so `qc.iloc[0]["qc_pass"] is
    True` returns False even when the value is logically
    True. Fix is `bool(qc.iloc[0]["qc_pass"]) is True` --
    not a runner bug. The {True: "True", False: "False"}
    dict in qc_pass_to_str works under == semantics so
    numpy booleans round-trip to string encoding correctly;
    no runner change.
  - generate_resistor_jsonl uses |Z|=R, phase=0, M = 1e6/R
    so all six v4.0c target resistors (100..10k) produce
    real values comfortably inside the §I.2.a int16
    window (M=100..10000 vs +/-32767). Saturation tests
    inject endpoints by hand instead of relying on a
    tightly-tuned K. SYNTHETIC_GF_K = 1e6 documented in
    the module docstring.
  - row_type defaults to "CAL" in the fixture so the
    resulting raw.csv (after replay_file) is calibration-
    ready immediately. Pass row_type="" to mimic the
    firmware's actual blank emission and let the laptop
    annotate later. Documented in the function docstring.
  - G-LIN cannot be exercised end-to-end with a single
    synthetic file; it requires Range 4 + Range 2 cal
    tables per §F.10.b. The pytest suite tests evaluate_g_lin
    directly with two synthetic cal frames; the README
    walk-through documents the two-table requirement
    explicitly and shows the call shape but does not run
    it.
  - The README walk-through was captured from a real
    `eisight-logger` invocation against /tmp/walk/, not
    transcribed from the source. Every command and output
    quoted there is verbatim from a fresh `pip install -e .`
    run. If a CLI flag rename later breaks the
    walk-through, the README is the canary.
  - Spec ambiguity surfaced during fixture generation:
    schemas.PgaStr is Literal["X1", "X5"], not "PGA_1" /
    "PGA_5" as the user prompt sketched. Followed the
    schema (X1/X5) since that is the wire-format source
    of truth in firmware/eisight_fw/src/jsonl.cpp; the
    Python kwarg name `pga_setting` keeps the schema
    nomenclature too. Same goes for the `range` JSON key
    vs the `range_setting` Python kwarg -- the schema
    field is `range` (a Python keyword shadow that
    pydantic handles fine via the model field name).

## 2026-04-29 session (continued -- path-(b) refactor completion + packaging)

Landed: gate runners (run_g_dc3 in g_dc3.py, run_g_sat in
        g_sat.py, run_g_lin in g_lin.py) with shared
        write_report_artifacts helper in common.py;
        slimmed cli.py (265 lines, path-(b) refactor
        complete); scripts/validate_logs.py;
        pyproject.toml (editable install).

Commits:
  - 60403d0 Add gate runners: run_g_dc3, run_g_sat,
            run_g_lin with shared write_report_artifacts
            helper
  - 593dad4 Slim cli.py per path-(b) refactor: ~265 lines,
            thin routing, runners own orchestration
  - 31bacb3 Add scripts/validate_logs.py: hardware/ CSV
            header conformance check per §F.6/§F.7
  - b14ff6b Add pyproject.toml: editable install with
            eisight-logger console script entry

Remaining: plots.py, tests/synthetic/
           generate_resistor_jsonl.py, test_schemas.py,
           test_calibration.py, test_qc.py,
           test_gates.py, README.md, pytest run.

Next session resumes at: tests/synthetic/generate_resistor_jsonl.py.
Decision: the synthetic fixture generator lands before
plots.py because the four pytest files
(test_schemas/test_calibration/test_qc/test_gates) all need
deterministic synthetic JSONL to exercise the runner contract,
and plots.py will eventually consume the same fixtures for
its golden-image tests. plots.py can then build on a working
test suite rather than the other way round.

Open notes:
  - Path-(b) refactor complete across three commits
    (c17a99d module runners, 60403d0 gate runners,
    593dad4 slim cli). cli.py final size 265 lines, ~40%
    smaller than the rejected 432-line draft.
  - eisight-logger console script live via pyproject.toml;
    pip install -e . confirmed working (exit 0). All seven
    subcommands (listen, validate, calibrate, qc, trust,
    gate, plot) appear in eisight-logger --help. plot is a
    NotImplementedError stub until plots.py lands.
  - write_report_artifacts is a submodule helper in
    gates/common.py (not in __all__) that deduplicates the
    fmt='text'/'json'/'both' write pattern across all three
    gate runners. Same pattern as _trusted_freqs_from_csv
    in g_lin.py (private to one runner) and load_inventory
    in calibration.py (used only by run_calibration).
  - validate_logs.py exit code: 0 if all hardware/ CSVs
    PASS, 1 if any MISSING/MALFORMED. Header-only templates
    are created where MISSING so the operator has a starting
    point. MALFORMED files are NOT modified -- avoids
    silently overwriting hand-entered data. The script is at
    196 lines (under the 200 max).
  - pyproject.toml package discovery uses where=["software"]
    only; synthetic_pipeline/ and scripts/ are intentionally
    out of the package surface (synthetic is feasibility-only
    per CLAUDE.md; scripts/validate_logs.py is a standalone
    tool, not an importable module).
  - g_sat.py grew to 317 lines after run_g_sat landed (was
    269); single-purpose module with the F.10.a primary/
    informational-load logic, the contiguous-band aggregator,
    the failures DataFrame schema, and the runner. Justified
    above 300 per the CLAUDE.md justify-or-split rule.
  - jumper_state.csv and dmm_inventory.csv columns in
    validate_logs.py are spec-derived but not enumerated
    explicitly in the .tex (jumper_state from §E.1 truth-
    table header + §E.7 step 11 'tag with post_rework';
    dmm_inventory from §F.7 step 1's 'DMM model and
    accuracy class' minimum). Documented as the assumption
    in the script docstring; revisit if §F.16 ever
    enumerates them.

## 2026-04-28 session (continued -- runners path-(b) refactor, partial)

Landed: load_inventory + run_calibration (calibration.py),
        run_qc (qc.py), run_trusted_band (trusted_band.py).

Commits:
  - c17a99d Add runners: load_inventory + run_calibration
                         in calibration.py, run_qc in qc.py,
                         run_trusted_band in trusted_band.py

Remaining: gates runners (run_g_dc3, run_g_sat, run_g_lin),
           slimmed cli.py, scripts/validate_logs.py,
           pyproject.toml, plots.py,
           tests/synthetic/generate_resistor_jsonl.py,
           test_schemas.py, test_calibration.py,
           test_qc.py, test_gates.py, README.md, pytest run.

Next session resumes at: gates runners (commit 2).

Open notes:
  - Runner contract is locked across all seven runners
    (four in this commit, three pending). All return their
    primary object always; writes are optional via output
    path kwargs; no prints, no logger setup.
  - load_inventory implements §F.7 G-DMMx promotion: lab_dmm_ohm
    preferred over measured_ohm. Handheld-DMM-only inventory
    still works; lab DMM data promotes automatically when
    added later.
  - cli.py write was rejected at 432 lines because orchestration
    had leaked into routing. Fix is to push runners back to
    modules (this commit + next) so cli.py shrinks to ~220 lines
    of pure argparse routing.

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
