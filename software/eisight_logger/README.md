# eisight_logger

Laptop-side measurement pipeline for the EISight v4.0c hardware
path: ingests JSONL packets from the ESP32 firmware, builds the
§I.6 calibration table, applies §H.8 sweep QC, computes the §H.5
trusted band, and evaluates the §E.11 / §F.10 quality gates
(G-DC3, G-SAT, G-LIN). All §-references point into
`docs/EISight_Blueprint_v4_0c.pdf`, the authoritative spec.

## Install

From the repo root, with a Python ≥ 3.10 environment active:

```
pip install -e .
```

This installs the package and the `eisight-logger` console
script (entry point `eisight_logger.cli:main`). The dependency
allowlist is in `pyproject.toml`; no other libraries are
permitted in the laptop pipeline (per `CLAUDE.md`).

Confirm the install:

```
eisight-logger --help
```

Seven subcommands are exposed: `listen`, `validate`,
`calibrate`, `qc`, `trust`, `gate`, `plot`. `plot` is a
`NotImplementedError` stub until `plots.py` lands.

## Subcommand reference

Every subcommand routes to a `run_*` function in its owning
module. The CLI is intentionally thin — orchestration lives in
the runners, not in `cli.py`. To extend a stage, add the runner
in the relevant module and wire ~10 lines into `cli.py`.

| Subcommand  | Module                             | Spec sections   |
|-------------|------------------------------------|-----------------|
| `listen`    | `serial_listener` (live + replay)  | §I.4, §I.5      |
| `validate`  | `validate_jsonl`                   | §I.4, §I.2.a    |
| `calibrate` | `calibration.run_calibration`      | §H.2, §I.6      |
| `qc`        | `qc.run_qc`                        | §H.7, §H.8      |
| `trust`     | `trusted_band.run_trusted_band`    | §H.5            |
| `gate`      | `gates.run_g_dc3 / run_g_sat / run_g_lin` | §E.11, §F.10.a/b |
| `plot`      | (pending `plots.py`)               | —               |

## End-to-end walk-through (synthetic data, no hardware)

This sequence runs from a fresh checkout against the synthetic
ideal-resistor fixture in `tests/synthetic/`. It exercises every
implemented stage and is the easiest way to confirm an editable
install is working end-to-end. All commands are run from the
repo root.

```
# 1) Generate an ideal-resistor JSONL trace (1 kΩ, 96-point sweep
#    matching the v4.0c §F.10 defaults).
python -m tests.synthetic.generate_resistor_jsonl 1000 \
    /tmp/walk/r1k.jsonl --load-id R1k_01 \
    --module-id AD5933-A-DIRECT
# -> wrote /tmp/walk/r1k.jsonl

# 2) Validate the JSONL against the §I.4 schema (incl. the §I.2.a
#    int16 range guard on real/imag).
eisight-logger validate /tmp/walk/r1k.jsonl
# -> validated 99 records, 0 failed

# 3) Replay the JSONL through the listener to produce raw.{jsonl,csv}.
eisight-logger listen --session-id WALK \
    --replay /tmp/walk/r1k.jsonl --output-root /tmp/walk/data
# -> listener summary: lines=99 failed=0 records=99 dropped_data=0
# -> raw.csv:   /tmp/walk/data/WALK/raw.csv

# 4) Build the §I.6 calibration table. The inventory CSV maps each
#    load_id to its metrology (nominal + measured ohm; lab DMM
#    columns optional per §F.7 G-DMMx).
cat > /tmp/walk/inventory.csv <<EOF
load_id,nominal_ohm,measured_ohm
R1k_01,1000,1000.0
EOF
eisight-logger calibrate /tmp/walk/data/WALK/raw.csv \
    /tmp/walk/inventory.csv /tmp/walk/cal.csv

# 5) Run §H.8 / §H.7 per-row QC; populates qc_pass / qc_reasons
#    on a copy of raw.csv with the locked "True"/"False"/""
#    encoding.
eisight-logger qc /tmp/walk/data/WALK/raw.csv \
    /tmp/walk/raw_qc.csv

# 6) Run §H.5 trusted-band selection. Optional --g-sat-failures
#    wires in G-SAT criterion 7 from a prior `gate --type g_sat
#    --failures-output ...` run.
eisight-logger trust /tmp/walk/data/WALK/raw.csv /tmp/walk/cal.csv \
    --raw-output /tmp/walk/raw_trusted.csv \
    --cal-output /tmp/walk/cal_trusted.csv

# 7) Evaluate the §F.10.a saturation gate. Writes both .txt and
#    .json reports to --output-dir; --fmt restricts to one if
#    desired (text / json / both).
eisight-logger gate --type g_sat --cal /tmp/walk/cal.csv \
    --output-dir /tmp/walk/reports --fmt both
# -> /tmp/walk/reports/g_sat.txt, /tmp/walk/reports/g_sat.json

# 8) Evaluate the §E.11 DC-bias gate from a §F.6 dc_bias_check.csv
#    you populated by hand (or via scripts/validate_logs.py's
#    template).
cat > /tmp/walk/dc_bias_check.csv <<EOF
module_id,range,condition,V_DC_P1_GND_mV,V_DC_P2_GND_mV,V_DC_DIFF_mV,V_DD_V,date,operator
AD5933-A-DIRECT,RANGE_4,NOLOAD,0.0,15.0,15.0,5.0,2026-04-29,T
AD5933-A-DIRECT,RANGE_4,R470,0.0,30.0,30.0,5.0,2026-04-29,T
EOF
eisight-logger gate --type g_dc3 --csv /tmp/walk/dc_bias_check.csv \
    --output-dir /tmp/walk/reports
# -> GATE G-DC3 -- PASS
# -> G-DC3: PASS on RANGE_4 (2 row(s); max |V_DC_DIFF| = 30.00 mV)
```

`gate --type g_lin` requires two cal tables (Range 4 and Range 2
per §F.10.b's "one additional Range 2 sweep" rule); a single
synthetic file does not exercise it. Generate two JSONL files
with **the SAME `--module-id`** but distinct `--sweep-id` and
the appropriate `range_setting` overrides (RANGE_4 vs RANGE_2),
calibrate each, then:

> **Safety note — do NOT use different `--module-id` for the two
> ranges.** G-LIN compares |Z| from the same physical module at
> two excitation amplitudes; a different module_id measures
> inter-module variance instead, so the gate now refuses
> (`NOT_EVALUATED`) when the Range-4 and Range-2 cal tables share
> no module_id. Earlier walkthrough text suggested distinct
> `--module-id` values; that was unsafe and is corrected here.

```
eisight-logger gate --type g_lin \
    --cal-r4 cal_r4.csv --cal-r2 cal_r2.csv \
    --trusted-band-csv cal_trusted.csv \
    --output-dir reports
```

## Canonical column lists

These are the schemas the rest of the project agrees on; the
column order is authoritative. `dtype=str, keep_default_na=False`
on every read preserves the boolean/empty-string conventions on
round-trip.

### §I.5 raw long-format (`raw.csv`)

`session_id, sweep_id, row_type, module_id, cell_id, sample_id,
load_id, frequency_hz, real, imag, status, range_setting,
pga_setting, settling_cycles, ds18b20_pre_c, ds18b20_post_c,
ad5933_pre_c, ad5933_post_c, operator, notes, gain_factor,
phase_system_deg, magnitude_calibrated, phase_calibrated_deg,
qc_pass, qc_reasons, trusted_flag`

The reserved (downstream-populated) columns at the end are
empty until the relevant stage fills them: `gain_factor` /
`phase_system_deg` from `calibration.py`, `qc_pass` /
`qc_reasons` from `qc.py`, `trusted_flag` from
`trusted_band.py`. Boolean cells use `"True"` / `"False"`
(capitalized strings) when evaluated, `""` when not yet
evaluated. `qc_reasons` is semicolon-joined.

### §I.6 calibration (`cal.csv`)

`session_id, module_id, load_id, nominal_ohm, actual_ohm,
dmm_model, dmm_accuracy_class_pct, frequency_hz, gain_factor,
phase_system_deg, repeat_cv_percent, trusted_flag`

`gain_factor = 1 / (R_actual * M(f))` per §H.2;
`phase_system_deg = atan2(I_cal, R_cal) * 180/π` and is the
per-load, per-frequency system phase that downstream phase
correction subtracts. `trusted_flag` shares the same `""` /
`"True"` / `"False"` encoding as §I.5.

## Spec deviations

- §I.5 of the `.tex` source names the DFT result columns
  `real_raw` / `imag_raw`. The firmware emits them on the wire
  as `real` / `imag` (`jsonl.cpp::write_data`), and the laptop
  CSV preserves the wire-format names verbatim to avoid silent
  drift between the two representations. A v4.0d patch can
  align the spec names; the field semantics are unchanged.

## Hardware bench logs

`scripts/validate_logs.py` checks that the four operator-logged
CSVs the pipeline depends on (`hardware/dc_bias_check.csv`,
`resistor_inventory.csv`, `jumper_state.csv`,
`dmm_inventory.csv`) exist with the §F-cited columns, and
creates header-only templates where missing. Run it from the
repo root before a session:

```
python scripts/validate_logs.py
```

Exit 0 if every file PASSes; exit 1 otherwise (MISSING /
MALFORMED).

## Firmware

For the wire-format details (boot self-test, command protocol,
JSONL packet schemas, watchdog behavior), see
[`firmware/eisight_fw/README.md`](../../firmware/eisight_fw/README.md).
The firmware is deliberately boring: no calibration math, no
ML, no UI. Every interpretation lives in this package.

## Tests

`tests/synthetic/` contains a deterministic ideal-resistor
fixture generator and four pytest files exercising the schema,
calibration, QC, and gate layers (67 tests total). Run them
from the repo root:

```
pytest tests/synthetic/ -v
```

A note for whoever next touches the QC tests:
`qc.evaluate_qc` returns a DataFrame whose `qc_pass` column has
numpy.bool\_ dtype (pandas coerces a Python `bool` list at
DataFrame construction). `np.True_ is True` is `False`, so the
test assertions wrap with `bool(...)` before `is`-comparing.
The runner behavior is correct — `qc_pass_to_str`'s
`{True: "True", False: "False"}` lookup uses `==` semantics
under the hood and round-trips numpy booleans unchanged.

## Module map

| File                | Spec                | Purpose                                |
|---------------------|---------------------|----------------------------------------|
| `schemas.py`        | §I.4, §I.2.a        | Pydantic v2 JSONL grammar; int16 guard |
| `validate_jsonl.py` | §I.4                | Line-by-line wire-format validator     |
| `serial_listener.py`| §I.4, §I.5          | Live serial + replay → raw.{jsonl,csv} |
| `raw_writer.py`     | §I.5                | Long-format CSV writer                 |
| `phase.py`          | §H.2                | DFT magnitude / phase primitives       |
| `calibration.py`    | §H.2, §I.6, §F.7    | GF / system phase + inventory loader   |
| `qc.py`             | §H.7, §H.8          | Per-row QC with locked encoding        |
| `trusted_band.py`   | §H.5                | Per-(module, freq) band membership     |
| `gates/`            | §E.11, §F.10.a/b    | G-DC3, G-SAT, G-LIN evaluators         |
| `cli.py`            | (all of the above)  | Thin argparse routing                  |

`tests/synthetic/`, `scripts/validate_logs.py`,
`firmware/eisight_fw/`, and `synthetic_pipeline/` (the
feasibility study artifact) are intentionally outside the
package surface; see `pyproject.toml` for the
`where = ["software"]` discovery scope.
