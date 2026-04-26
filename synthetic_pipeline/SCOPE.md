# Synthetic Pipeline — AURA Phase 2 Feasibility Study

This directory contains the simulation-only pipeline used
for the AURA Phase 2 feasibility study. **All outputs from
this directory are synthetic.** They demonstrate that the
ML approach is feasible if real EIS data conformed to a
Cole–Cole model with the assumed adulteration
perturbations. They are not evidence of instrument
performance.

**Frequency grid:** 1–100 kHz (legacy, pre-v4.0c).
**Data source:** Cole–Cole synthesis with Gaussian noise.
**Inference bundle:** trained on the synthetic grid;
incompatible with the v4.0c hardware sweep (5–100 kHz, 96
points).

The hardware-side pipeline lives in `software/`. Do not
mix outputs from the two paths.
