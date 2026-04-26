# Claude Code instructions for EISight

- Authoritative spec: docs/EISight_Blueprint_v4_0c.pdf.
- Synthetic code lives in `synthetic_pipeline/`. **Do not
  modify it** unless I explicitly ask. Treat its outputs
  as feasibility-study artifacts, not evidence of
  hardware behavior.
- Real-hardware code lives under `software/` and
  `firmware/`. Build new modules here.
- Default sweep band is 5–100 kHz, 96 points, Range 4,
  PGA x1, settling 15 (per v4.0c §H.5, §H.6, §F.10).
- AD5933 real and imag registers (0x94/0x95, 0x96/0x97)
  are signed 16-bit twos-complement. Internal temperature
  (0x92/0x93) is 14-bit signed in 16-bit envelope. Always
  sign-extend per v4.0c §I.2.a.
- Phase must be unwrapped (numpy.unwrap) before any
  slope, derivative, or Nyquist-area feature.
- G-SAT and G-LIN are analyses on the F.10 dataset, not
  separate sweep sessions.
- Do not invent libraries. For Python, use only numpy,
  pandas, pyserial, scipy, matplotlib, streamlit,
  pydantic, pytest. For ESP32, use the standard Arduino
  core + Wire + OneWire/DallasTemperature + the ESP32-
  patched fork of mjmeli/arduino-ad5933.
- Approve file changes in small batches. Stop and ask if
  a refactor would touch more than ~5 files at once.
