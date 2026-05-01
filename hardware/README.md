# EISight Hardware Bench Logs

This directory contains manually filled hardware and operator logs for the
EISight bench workflow. These files are templates: fill them by hand during
inventory, bring-up, probing, and bench sessions.

Pipeline-generated CSVs are created later under `data/real/...` by
`eisight-logger`. Do not manually create generated or trusted data products
such as `raw.csv`, `cal.csv`, `raw_qc.csv`, `raw_trusted.csv`, or
`cal_trusted.csv`.

`data/real` is reserved for generated or captured sessions and is ignored by
git. Keep captured session outputs there, not in `hardware/`.

## First Files To Fill

Before powering hardware, fill these logs first:

- `dmm_inventory.csv`
- `resistor_inventory.csv`
- `jumper_state.csv`

## Bench Log Notes

- `rfb_inventory.csv` may be marked skipped if SSOP probing is unsafe.
- `dc_bias_check.csv` is filled only during active G-DC3 DC-bias testing.
- Leave unknown fields blank until the value is observed or assigned.
- Use `notes` for operator context, exceptions, skipped checks, or setup
  details.

## Template Files

- `dmm_inventory.csv`: DMM models, asset IDs, accuracy, calibration status,
  and operator metadata.
- `resistor_inventory.csv`: load resistor identity and manual measurement
  metadata.
- `jumper_state.csv`: continuity and expected jumper states before bring-up.
- `rfb_inventory.csv`: feedback-resistor probing decisions and results.
- `power_rail_check.csv`: bring-up rail, idle bus, current, and thermal checks.
- `i2c_sanity_log.csv`: I2C discovery and basic read sanity checks.
- `dc_bias_check.csv`: active G-DC3 DC-bias test observations.
- `probe_inventory.csv`: probe identity and calibration check metadata.
- `cell_geometry.csv`: cell geometry, material, and inspection metadata.
- `bench_session_log.csv`: high-level session tracking and next actions.
