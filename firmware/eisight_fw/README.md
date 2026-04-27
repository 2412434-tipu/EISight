# eisight_fw

ESP32 PlatformIO firmware for the EISight v4.0c hardware
path. Drives an AD5933 over I²C, reads a DS18B20 over
1-Wire, and emits per-sweep JSONL packets over USB serial.

The firmware is deliberately boring: no calibration math,
no ML, no UI, no Wi-Fi or BLE (per blueprint §I.3). The
laptop pipeline does all interpretation; this binary's
only job is to read registers correctly and stream them
unmodified.

## Project context

- Repository: <https://github.com/2412434-tipu/EISight>
- Authoritative spec: [`docs/EISight_Blueprint_v4_0c.pdf`](../../docs/EISight_Blueprint_v4_0c.pdf)
  (the LaTeX source under `docs/` is the same content;
   the PDF is the citable artifact).

Section references throughout this README (§I.2.a, §I.4,
§F.4, §H.5/H.6) point into that blueprint.

## Hardware required

| Item | Notes |
|------|-------|
| ESP32 dev board | Stock `esp32dev` target (`platformio.ini`); WROOM-32 / DevKitC class. Onboard blue LED on GPIO 2 used for self-test halt indication. |
| AD5933 evaluation board | Address `0x0D`. Module A is run as-received in Direct mode for the §F.4 sanity gate; Module B is reworked to Dual-AFE only after F.4 passes. |
| DS18B20 temperature sensor | OneWire on GPIO 4, 12-bit resolution. Optional — boot continues if missing, with a non-fatal `error` packet. |
| Calibration resistor set | At minimum a 1 kΩ ±0.1 % for the §F.4 sanity gate. Full set per §F covers the calibration band. |
| 4.7 kΩ pull-up | OneWire data line to +3.3 V. |
| USB cable | Connects the dev-board's UART bridge (CP2102 / CH340) to the laptop. The host opens the resulting COM port at 921600 baud. |

## Wiring

```
                    +-----------+
   +3.3V o----------| Vdd       |
                    |           |
   GPIO 21 (SDA) ---| SDA  AD   |
   GPIO 22 (SCL) ---| SCL  5933 |
                    |   0x0D    |
                    | VOUT  VIN |---[ DUT / 1 kohm cal ]---+
                    |           |                          |
   GND   o----------| GND       |                          |
                    +-----------+                          |
                                                           |
   +3.3V o---+--[4.7k]---+                                 |
             |           |                                 |
   GPIO 4 ---+-----------+--- DS18B20 DQ                   |
                             DS18B20 GND -----------o GND -+
                             DS18B20 Vdd -----o +3.3V

   GPIO 2 = onboard blue status LED (self-test halt).
```

Pins are defined in `include/config.h` (`kPinSda`,
`kPinScl`, `kPinDs18b20`, `kPinStatusLed`). Change them
there, never inline.

## Build and flash

This is a PlatformIO project. From the firmware directory:

```
pio run                       # build
pio run --target upload       # build + flash
pio device monitor            # 921600 baud, JSONL stream
```

In VS Code with the PlatformIO extension, the General →
Build / Upload / Monitor buttons do the equivalent.

### Library dependencies

`platformio.ini` pulls these in automatically:

- `milesburton/DallasTemperature@^3.11.0`
- `paulstoffregen/OneWire@^2.3.7`
- `https://github.com/mjmeli/arduino-ad5933.git` (upstream)

**AD5933 library — ESP32 fork note.** Upstream
`mjmeli/arduino-ad5933` was written for AVR (Arduino
Uno). Some users report needing small ESP32 patches in
its `Wire` usage. EISight's own `src/ad5933.cpp` reads
result registers via `Wire` directly — the §I.2.a
signed-int16 parse is in our auditable code, not a
library call — so upstream is acceptable here. If you
prefer a known-good ESP32 fork, replace the URL in
`platformio.ini` with that fork's URL; do not rename the
`lib_deps` variable.

## Boot self-test (§I.2.a)

Before `ad5933::begin()` is allowed to touch the bus,
`setup()` runs two parse-only fixtures (no I/O):

- `run_int16_parse()` — six int16 fixtures: 0, +1, −1,
  `INT16_MAX`, `INT16_MIN`, and −567 (the `imag` value
  from the §I.4 example packet).
- `run_temp14_parse()` — +25.0 °C with the don't-care bits
  D15..D14 set (exercises the `0x3FFF` mask) and −25.0 °C
  with D13 set (exercises sign extension).

On the first mismatch the helper emits one
`self_test_fail` packet and returns false. `setup()` then
halts in a 4 Hz blink loop on GPIO 2 — the device does
**not** proceed to `hello`, does **not** open the I²C
bus, and does **not** dispatch modes. A regression in
either parser would silently corrupt every sweep this
firmware emits, so failing closed at boot is intentional.

`ad5933::begin()` and `ds18b20::begin()` failures, by
contrast, emit one `error` packet each and the boot
continues; mode `s` (i2c scan) and DS18B20-less benches
remain usable.

## Serial command protocol

Single-character commands read one byte at a time. Whitespace,
control characters (anything with code ≤ space), and
non-printable bytes are silently dropped — terminal framing
does not generate "unknown command" noise.

| Cmd | Name | Description |
|-----|------|-------------|
| `m <id>` | SET_MODULE_ID | Register the module identity. **Must be sent first.** Other commands return an `error` packet until this succeeds. The validator allows `A-Z`, `a-z`, `0-9`, `-`, `_` only; max 32 chars. Re-issuing replaces the prior id. |
| `s` | SCAN_I2C | Probe addresses `0x08`–`0x77`, list the ACK'd ones. |
| `r` | READ_REG_SANITY | 100 iterations of STATUS-read + die-temperature read, ~10 ms apart. The §F.4 bus-stability check. |
| `1` | SINGLE_10KHZ | One-point sanity sweep at 10 kHz. The §F.4 signed-real/imag end-to-end check on a 1 kΩ load. |
| `f` | SWEEP_5K_100K | The v4.0c default 96-point sweep: 5–100 kHz step 1 kHz, Range 4, PGA ×1, 15 settling cycles. |
| `t` | TEMP_ONLY | DS18B20 + AD5933 die temperature snapshot, no sweep. |

Why module_id is runtime-only: §F.4 starts with Module A
in as-received Direct mode and only after that gate
passes is Module B reworked to Dual-AFE (§F.5). Hard-coding
a single id would mislabel the entire first session, so
the operator sets it via `m <id>` at the start of each
session.

## JSONL packet schemas

One JSON object per line, no embedded newlines, no
trailing comma. Strings are not escaped — the
`session::handle_set_command` validator already rejects
quotes/backslashes; other string fields are firmware-internal
constants. NaN/inf floats are emitted as the JSON literal
`null`, never quoted. The exact format strings live in
`src/jsonl.cpp`; the examples below are copied from those
`snprintf` templates verbatim.

### `hello`
Emitted last in `setup()` (after any begin-time `error`
packets). `module_id` is `null` until `m <id>` succeeds.

```
{"type":"hello","fw":"eisight-fw-0.1.0","module_id":null}
```

### `module_id_set`
Emitted on a successful `m <id>` command.

```
{"type":"module_id_set","module_id":"AD5933-A-DIRECT"}
```

### `sweep_begin`
`session_id`, `cell_id`, `row_type`, `load_id` are
emitted as empty strings — the laptop pipeline annotates
them on ingest. `ds18b20_pre_c` formats with 4 decimals;
`ad5933_pre_c` with 1.

```
{"type":"sweep_begin","session_id":"","sweep_id":"SWP0000","module_id":"AD5933-A-DIRECT","cell_id":"","row_type":"","load_id":"","start_hz":10000,"stop_hz":10000,"points":1,"range":"RANGE_4","pga":"X1","settling_cycles":15,"ds18b20_pre_c":25.1250,"ad5933_pre_c":31.0}
```

### `data`
`real` and `imag` are signed int16, parsed per §I.2.a.
`frequency_hz` formats with 1 decimal.

```
{"type":"data","sweep_id":"SWP0000","idx":0,"frequency_hz":10000.0,"real":1234,"imag":-567,"status":2}
```

### `sweep_end`
`error` is `null` on a clean sweep, a quoted string
otherwise. `elapsed_ms` is the wall-clock duration from
just before `program_sweep` to just after the post-sweep
temperature reads.

```
{"type":"sweep_end","sweep_id":"SWP0000","ds18b20_post_c":25.1875,"ad5933_post_c":31.1,"elapsed_ms":1820,"error":null}
```

### `error`
Generic non-fatal error. Emitted by begin failures, the
mode dispatcher's "module_id not set" gate, and inside
mode bodies (e.g. watchdog timeout if it fires before any
data point lands).

```
{"type":"error","detail":"module_id not set; run 'm <id>' first"}
```

### `self_test_fail`
The §I.2.a parse fixture that failed. Always followed by
the halt-blink loop on GPIO 2; no further packets emit.

```
{"type":"self_test_fail","detail":"int16 fixture: -567"}
```

### `i2c_scan`
Emitted by mode `s`. Addresses are formatted as quoted
hex strings (`"0xNN"`), not bare numbers, so the laptop
pipeline does not have to worry about JSON's lack of hex
literals.

```
{"type":"i2c_scan","module_id":"AD5933-A-DIRECT","addrs":["0x0D","0x28"]}
```

### `reg_sanity`
One per iteration of mode `r`, 100 total. `ad5933_c`
formats with 2 decimals (or `null` on read failure).

```
{"type":"reg_sanity","iter":0,"status":0,"ad5933_c":31.06}
```

### `temp_only`
Sole record from mode `t`. `ds18b20_c` is 4-decimal,
`ad5933_c` 2-decimal; either may be `null`.

```
{"type":"temp_only","ds18b20_c":25.1250,"ad5933_c":31.06}
```

## Watchdog (per-frequency budget)

Every sweep point gets its own poll budget before the
firmware soft-resets the AD5933 and aborts the sweep with
`"error":"watchdog timeout"`:

```
budget_us = K * (T_settle + T_dft + T_i2c)
```

where, from `include/config.h`:

| Term | Value | Source |
|------|-------|--------|
| `T_settle` | `settling_cycles / f_exc` | computed per point; with 15 cycles and `f_exc = 5 kHz` this is 3 ms; with `f_exc = 100 kHz` it is 150 µs. |
| `T_dft`    | 977 µs (`kDftWindowUs`) | constant; 1024 ADC samples at MCLK/16 with MCLK = 16.776 MHz. |
| `T_i2c`    | 1500 µs initial (`kI2cInitialUs`); overwritten on the first successful STATUS poll of every sweep | live measurement, so a sluggish bus does not need a recompile. |
| `K`        | 3 (`kWatchdogSafetyK`) | safety factor. |
| ceiling    | 250 ms (`kWatchdogMaxMs`) | absolute cap; if `t_i2c_us_measured` ever returns nonsense, the budget cannot run away. |

The first sweep on a fresh boot uses the 1500 µs initial
T_i2c; from the second point onward the measurement is
the actual round-trip. `ad5933::reset_i2c_timing()` is
called at the start of every sweep so a stale value from
a previous session does not carry over.

## Minimum operator session — §F.4 1 kΩ sanity gate

This is the smallest session that proves Module A in
as-received Direct mode satisfies all five §F.4 gate
criteria from the firmware side. The R_FB inventory
(criterion i) and the rail/thermal check (criterion ii)
are external — they are DMM measurements logged into
`hardware/rfb_inventory.csv`, not emitted by this
firmware.

**Setup.** Wire 1 kΩ ±0.1 % across the AD5933 IN/OUT
path. Power the dev-board over USB. Open a serial
monitor at **921600 baud** (e.g. `pio device monitor`).

The exact serial dialog:

```
# (hello arrives unprompted on boot)
< {"type":"hello","fw":"eisight-fw-0.1.0","module_id":null}

# 1) Register the module id. F.4 is on Module A, Direct mode.
> m AD5933-A-DIRECT
< {"type":"module_id_set","module_id":"AD5933-A-DIRECT"}

# 2) Criterion (iii): module responds at 0x0D.
> s
< {"type":"i2c_scan","module_id":"AD5933-A-DIRECT","addrs":["0x0D","0x28"]}
#   (0x28 is the DS18B20 if present; expect at least 0x0D.)

# 3) Criterion (iv): 100 consecutive register reads, no bus error.
> r
< {"type":"reg_sanity","iter":0,"status":0,"ad5933_c":31.06}
< {"type":"reg_sanity","iter":1,"status":0,"ad5933_c":31.06}
< ... 98 more ...
< {"type":"reg_sanity","iter":99,"status":0,"ad5933_c":31.09}
#   Pass if all 100 records arrive with finite ad5933_c
#   and no "error" packet interleaves.

# 4) Criterion (v): finite, non-saturated, signed real/imag
#    on a 1 kohm load at 10 kHz.
> 1
< {"type":"sweep_begin", ... "start_hz":10000,"stop_hz":10000,"points":1, ...}
< {"type":"data","sweep_id":"SWP0000","idx":0,"frequency_hz":10000.0,"real":1234,"imag":-567,"status":2}
< {"type":"sweep_end","sweep_id":"SWP0000", ... ,"error":null}
#   Pass if real and imag are both well inside
#   [-32768, +32767] (saturated values are red flags) and
#   error is null.
```

If all four firmware-observable steps pass, the §F.4
gate is satisfied for Module A. Only then is Module B
reworked into the Dual-AFE configuration (§F.5) and
re-tested with `m AD5933-B-DUAL-AFE` followed by the
same `s` / `r` / `1` sequence.

## What this firmware deliberately does **not** do

Per blueprint §I.3:

- No magnitude / phase math (laptop pipeline computes
  these from the raw signed real/imag).
- No calibration, no ML inference, no Wi-Fi, no BLE, no
  display.
- No interrupts. The serial loop is polled.
- No state across modes. Each mode emits a self-contained
  record set; a dropped byte at the boundary degrades to
  one missed mode invocation, not a corrupt sweep.
