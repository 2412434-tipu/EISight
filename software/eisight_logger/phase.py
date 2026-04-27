"""phase.py -- DFT magnitude/phase primitives for the EISight v4.0c
laptop pipeline.

The AD5933 returns signed 16-bit DFT real/imag registers per §I.2.a;
this module turns those signed integers into magnitude and phase
arrays per the §H.2 equations:

  M(f_i)        = sqrt(R(f_i)^2 + I(f_i)^2)              (eq:dft_magnitude)
  phi_raw(f_i)  = atan2(I(f_i), R(f_i))                  (eq:raw_phase)
  phi_system(f) = atan2(I_cal(f),    R_cal(f))           (eq:system_phase)
  phi_sample(f) = atan2(I_smp(f),    R_smp(f)) - phi_system(f)
                                                         (eq:corrected_phase)
  phi_deg(f)    = phi(f) * 180/pi                        (eq:phase_deg)

§H.2 rulebox (locked into the project CLAUDE.md): phase MUST be
unwrapped with numpy.unwrap over the frequency axis BEFORE any
slope, derivative, or Nyquist-arc feature is computed. Raw atan2
output has +/-pi jumps that break those features. This module
provides the unwrap primitive; downstream callers (feature
extraction, plotting) MUST invoke it before deriving anything.

Units convention: this module is the single boundary between
radians (numpy convention) and degrees (CSV/feature output).
Functions named *_rad return radians; phase_to_deg converts at
the very end so unwrap is well-defined.

The functions are pure-numpy primitives with no I/O and no
dependencies on the other v4.0c laptop modules; they exist to
be composed by calibration.py, qc.py, gates.py, and the feature
layer.

Implements: §H.2 (gain/phase calibration math).
"""

from __future__ import annotations

import numpy as np


def dft_magnitude(real: np.ndarray, imag: np.ndarray) -> np.ndarray:
    """Per-frequency DFT magnitude M(f) = sqrt(R^2 + I^2).

    real/imag are 1-D arrays of signed int16 register values, the
    §I.2.a output of the AD5933 0x94/0x95 and 0x96/0x97 registers.
    They MUST be aligned point-by-point in frequency order and have
    matching shape. Cast to float64 before squaring so the result
    type is unambiguous downstream.
    """
    r = np.asarray(real, dtype=np.float64)
    i = np.asarray(imag, dtype=np.float64)
    if r.shape != i.shape:
        raise ValueError(
            f"real/imag shape mismatch: {r.shape} vs {i.shape}"
        )
    return np.sqrt(r * r + i * i)


def raw_phase_rad(real: np.ndarray, imag: np.ndarray) -> np.ndarray:
    """Per-frequency raw DFT phase in radians: atan2(I, R).

    Returned values are in [-pi, +pi]. Callers MUST unwrap (with
    unwrap_phase) over the frequency axis before computing any
    slope, derivative, or Nyquist-arc feature -- the §H.2 rulebox
    requires it.
    """
    r = np.asarray(real, dtype=np.float64)
    i = np.asarray(imag, dtype=np.float64)
    if r.shape != i.shape:
        raise ValueError(
            f"real/imag shape mismatch: {r.shape} vs {i.shape}"
        )
    return np.arctan2(i, r)


def system_phase_rad(
    real_cal: np.ndarray, imag_cal: np.ndarray
) -> np.ndarray:
    """phi_system(f) per eq:system_phase: atan2(I_cal, R_cal).

    A near-ideal calibration resistor has zero impedance phase, so
    its measured DFT phase IS the system's per-frequency phase
    offset. Equivalent in math to raw_phase_rad, but kept distinct
    so the call site self-documents which role the array plays.
    """
    return raw_phase_rad(real_cal, imag_cal)


def corrected_phase_rad(
    real_sample: np.ndarray,
    imag_sample: np.ndarray,
    phi_system_rad: np.ndarray,
) -> np.ndarray:
    """phi_sample(f) per eq:corrected_phase.

    phi_sample(f) = atan2(I_s(f), R_s(f)) - phi_system(f)

    phi_system_rad must come from the same frequency grid as the
    sample arrays (i.e. produced by system_phase_rad on a
    calibration sweep at the same start_hz / stop_hz / points).

    NOT unwrapped -- caller must call unwrap_phase on the result
    before any derivative, slope, or Nyquist-arc feature
    (§H.2 rulebox).
    """
    raw = raw_phase_rad(real_sample, imag_sample)
    sys_phase = np.asarray(phi_system_rad, dtype=np.float64)
    if raw.shape != sys_phase.shape:
        raise ValueError(
            "sample/system phase shape mismatch: "
            f"{raw.shape} vs {sys_phase.shape}"
        )
    return raw - sys_phase


def unwrap_phase(phase_rad: np.ndarray) -> np.ndarray:
    """numpy.unwrap on a frequency-ordered phase array (radians).

    Removes 2*pi jumps along the last axis. The input must be
    sorted ascending in frequency; otherwise the jumps unwrap
    against the wrong neighbours and the result is meaningless.
    Required before any slope/derivative/Nyquist feature per the
    §H.2 rulebox.
    """
    p = np.asarray(phase_rad, dtype=np.float64)
    return np.unwrap(p)


def phase_to_deg(phase_rad: np.ndarray) -> np.ndarray:
    """Convert a radian phase array to degrees (eq:phase_deg).

    Apply AFTER unwrap_phase; converting wrapped radians to degrees
    just shifts the +/-pi jumps to +/-180 deg without resolving
    them.
    """
    p = np.asarray(phase_rad, dtype=np.float64)
    return p * (180.0 / np.pi)
