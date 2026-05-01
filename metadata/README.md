# EISight Sample Metadata Templates

This directory contains manually filled sample and dataset metadata logs for
the EISight milk experiment workflow. These templates describe samples,
sessions, dilution plans, reference measurements, temperatures, and grouped
dataset split intent.

These logs are separate from `hardware/` bench logs. Use `hardware/` for
instrument, cell, module, rail, jumper, and bench-state records.

These logs are also separate from logger-generated `data/real` artifacts.
Captured session CSVs are created later by `eisight-logger`; do not manually
create generated data products such as `raw.csv`, `cal.csv`, `raw_qc.csv`,
`raw_trusted.csv`, or `cal_trusted.csv`.

## First Dataset Target

The first AURA dataset target is a commercial milk baseline plus controlled
water dilution. Treat commercial packaged milk as a nominal baseline, not as
certified pure milk.

For each water-dilution session, record a same-day pure control in the
metadata. That control anchors the dilution set for the day/session/source.

Future datasets may include freshness, spoilage, and other adulterants, but
those are not part of this first template set.

## Dataset Splits

Do not use a random row split for ML. A random row split can leak same-session,
same-cell, same-day, or same-source structure into multiple sets.

Use `dataset_split_plan.csv` to plan grouped validation by day, session, cell,
and source. The split plan should make it clear which samples are allowed for
training, validation, and test use.

## Template Files

- `sample_inventory.csv`: sample identity, source, condition, dilution, and
  cell metadata.
- `milk_session_log.csv`: high-level milk session tracking and outcome notes.
- `dilution_plan.csv`: controlled water-dilution preparation plan.
- `lactometer_log.csv`: lactometer readings and correction metadata.
- `ph_log.csv`: pH readings and meter calibration status.
- `temperature_log.csv`: sample and ambient temperature observations.
- `dataset_split_plan.csv`: grouped ML split plan by day/session/cell/source.
