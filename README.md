# EISight: Low-Cost EIS for Food Adulteration Detection

## Project Overview
This repository contains the software pipeline, firmware, and hardware schematics for an IoT-enabled Electrical Impedance Spectroscopy (EIS) system. The project aims to detect adulterants in liquid commodities (with an initial focus on milk) using a low-cost AD5933 impedance converter paired with an ESP32 microcontroller.

## Repository Structure
- `/synthetic_pipeline` - AURA Phase 2 feasibility study, **synthetic only** (Cole–Cole simulation, 1–100 kHz). See `synthetic_pipeline/SCOPE.md`.
- `/software/eisight_logger` - Real hardware pipeline: serial listener, calibration, QC, and v4.0c gates (planned, next session).
- `/software/eisight_dashboard` - Local Streamlit dashboard for calibrated sweeps and gate verdicts (planned).
- `/firmware/eisight_fw` - ESP32 firmware: AD5933 driver, DS18B20 reader, JSONL packet emitter. **Complete and building clean** (22.8% flash / 6.7% RAM on `esp32dev`). See [`firmware/eisight_fw/README.md`](firmware/eisight_fw/README.md) for the operator manual.
- `/hardware` - Bench logs, jumper state, RFB inventory, electrode-cell notes.
- `/paper` - Literature library: reference PDFs cited in the blueprint and progress report.
- `/docs` - v4.0c blueprint and AURA progress reports.
- `/tests/synthetic` - Regression fixtures (synthetic resistor JSONL traces) for the software modules.
- `/figures`, `/pipeline_outputs` - Legacy synthetic outputs from the Phase 2 feasibility study (`pipeline_outputs/` is gitignored).

## Authoritative Specification
All hardware-side work follows `docs/EISight_Blueprint_v4_0c.pdf`. The synthetic pipeline predates v4.0c and is preserved unchanged for reproducibility of the Phase 2 report.