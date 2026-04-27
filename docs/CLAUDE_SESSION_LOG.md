# Claude session log

Per-session notes for the EISight v4.0c laptop-side pipeline
work. One entry per session; newest first. Format intentionally
terse -- the commits and code are the source of truth, this file
just bookmarks where each session started and stopped.

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
