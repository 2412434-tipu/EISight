"""generate_resistor_jsonl.py -- ideal-resistor JSONL fixture for tests.

Produces deterministic JSONL traces matching schemas.JsonlRecord
exactly, simulating the firmware's wire format for an ideal
resistor of value R ohms swept at the v4.0c default sweep
parameters (5--100 kHz, 96 points, Range 4, PGA x1, 15 settling
cycles per §F.10 / §H.5).

Model
-----
|Z| = R, phase = 0, frequency-independent. The DFT result at
each frequency is therefore (real, imag) = (M, 0) where

    M = SYNTHETIC_GF_K / R

The constant SYNTHETIC_GF_K = 1e6 was chosen so all six target
resistors (100, 330, 470, 1000, 4700, 10000) produce M values
comfortably inside the §I.2.a int16 window: M ranges from 100
(R=10k) up to 10000 (R=100), all well under +/-32767. The
"comfortably inside" margin is intentional -- the saturation-
boundary pytest fixtures inject saturation by hand instead of
relying on a tightly-tuned K.

The §H.2 identity gives gain_factor = 1 / (R * M) = 1 /
SYNTHETIC_GF_K, i.e. a constant 1e-6 across all R and all
frequencies. Apply gain_factor to the same M and the unknown-
impedance equation 1/(GF * M) recovers R exactly. Tests use this
property to validate calibration.run_calibration end-to-end.

Emitted record sequence
-----------------------
One JSONL file contains:
  1 x HelloRecord
  1 x SweepBeginRecord
  num_points x DataRecord
  1 x SweepEndRecord

Every record validates against schemas.JsonlRecord by
construction, so a trace can be replayed through
serial_listener.replay_file without further massaging.

Scope note
----------
Software testing infrastructure only. Ideal-resistor data is
never paper evidence -- real fixtures have parasitic capacitance,
lead inductance, and contact resistance, none of which the
|Z|=R model captures. A test that consumes this fixture is
exercising pipeline mechanics, not hardware behavior.

Implements: §F.10 default sweep parameters, §I.4 wire format,
§I.2.a int16 range guard (by construction).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Union

# §I.2.a int16 endpoints; mirrors schemas.INT16_MIN/MAX.
_INT16_MIN = -32768
_INT16_MAX = 32767

# Synthetic gain-factor scale. M(R) = SYNTHETIC_GF_K / R, chosen
# so all six v4.0c target resistors (100..10k) produce |real|
# values comfortably inside the int16 window.
SYNTHETIC_GF_K = 1.0e6

# Default firmware version string -- mirrors the live firmware's
# write_hello template. Keeps the trace round-trippable through
# tests that assert specific 'hello' content.
DEFAULT_FW = "eisight-fw-0.1.0"

# AD5933 STATUS register bit D1 = valid real/imag (datasheet);
# every clean data point sets it.
_STATUS_VALID = 2


def generate_resistor_jsonl(
    R_ohms: float,
    output_path: Union[Path, str],
    *,
    session_id: str = "SYNTH",
    module_id: str = "AD5933-SYNTH",
    load_id: str = "RSYNTH_01",
    cell_id: str = "",
    row_type: str = "CAL",
    sweep_id: str = "SWP0000",
    start_hz: float = 5000.0,
    stop_hz: float = 100_000.0,
    num_points: int = 96,
    range_setting: str = "RANGE_4",
    pga_setting: str = "X1",
    settling_cycles: int = 15,
    ds18b20_pre_c: Optional[float] = 25.0,
    ds18b20_post_c: Optional[float] = 25.0,
    ad5933_pre_c: Optional[float] = 31.0,
    ad5933_post_c: Optional[float] = 31.0,
    elapsed_ms: int = 1820,
    fw: str = DEFAULT_FW,
) -> Path:
    """Write an ideal-resistor JSONL trace for R_ohms to output_path.

    Returns the resolved Path. Raises ValueError if R_ohms is
    non-positive, num_points < 1, or the resulting M would
    overflow the int16 window. Parent directories are created
    on demand.

    row_type defaults to "CAL" so the resulting raw.csv (after
    serial_listener.replay_file) is ready for calibration
    immediately. Pass row_type="" to mimic the firmware's actual
    blank emission and let the laptop annotate later.
    """
    if R_ohms <= 0:
        raise ValueError(f"R_ohms must be positive (got {R_ohms!r})")
    if num_points < 1:
        raise ValueError(f"num_points must be >= 1 (got {num_points!r})")
    if stop_hz < start_hz:
        raise ValueError(
            f"stop_hz {stop_hz!r} < start_hz {start_hz!r}"
        )

    M_float = SYNTHETIC_GF_K / R_ohms
    real_int = int(round(M_float))
    if not (_INT16_MIN <= real_int <= _INT16_MAX):
        raise ValueError(
            f"R={R_ohms} produces real={real_int}, outside int16 window "
            f"[{_INT16_MIN}, {_INT16_MAX}]; tighten SYNTHETIC_GF_K or R."
        )
    imag_int = 0  # phase = 0 for an ideal resistor

    freqs = _frequency_grid(start_hz, stop_hz, num_points)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    records: List[dict] = []
    records.append({
        "type": "hello",
        "fw": fw,
        "module_id": module_id,
    })
    records.append({
        "type": "sweep_begin",
        "session_id": session_id,
        "sweep_id": sweep_id,
        "module_id": module_id,
        "cell_id": cell_id,
        "row_type": row_type,
        "load_id": load_id,
        "start_hz": int(start_hz),
        "stop_hz": int(stop_hz),
        "points": int(num_points),
        "range": range_setting,
        "pga": pga_setting,
        "settling_cycles": int(settling_cycles),
        "ds18b20_pre_c": ds18b20_pre_c,
        "ad5933_pre_c": ad5933_pre_c,
    })
    for idx, f in enumerate(freqs):
        records.append({
            "type": "data",
            "sweep_id": sweep_id,
            "idx": int(idx),
            "frequency_hz": float(f),
            "real": real_int,
            "imag": imag_int,
            "status": _STATUS_VALID,
        })
    records.append({
        "type": "sweep_end",
        "sweep_id": sweep_id,
        "ds18b20_post_c": ds18b20_post_c,
        "ad5933_post_c": ad5933_post_c,
        "elapsed_ms": int(elapsed_ms),
        "error": None,
    })

    with out.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return out


def _frequency_grid(
    start_hz: float, stop_hz: float, num_points: int
) -> List[float]:
    """Evenly-spaced frequency grid from start to stop inclusive."""
    if num_points == 1:
        return [float(start_hz)]
    step = (float(stop_hz) - float(start_hz)) / (num_points - 1)
    return [float(start_hz) + i * step for i in range(num_points)]


def main(argv: Optional[List[str]] = None) -> int:
    """CLI front-end: python -m tests.synthetic.generate_resistor_jsonl R out."""
    parser = argparse.ArgumentParser(
        prog="generate_resistor_jsonl",
        description=(
            "Write an ideal-resistor JSONL trace at the v4.0c default "
            "sweep parameters. Software testing only; not paper evidence."
        ),
    )
    parser.add_argument("R_ohms", type=float, help="Resistor value (ohms)")
    parser.add_argument("output_path", type=Path, help="Output .jsonl path")
    parser.add_argument("--session-id", default="SYNTH")
    parser.add_argument("--module-id", default="AD5933-SYNTH")
    parser.add_argument("--load-id", default="RSYNTH_01")
    parser.add_argument("--row-type", default="CAL")
    parser.add_argument("--sweep-id", default="SWP0000")
    parser.add_argument("--num-points", type=int, default=96)
    args = parser.parse_args(argv)
    out = generate_resistor_jsonl(
        args.R_ohms, args.output_path,
        session_id=args.session_id, module_id=args.module_id,
        load_id=args.load_id, row_type=args.row_type,
        sweep_id=args.sweep_id, num_points=args.num_points,
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
