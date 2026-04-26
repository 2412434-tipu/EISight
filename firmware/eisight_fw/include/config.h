// EISight v4.0c firmware — pin map and sweep defaults.
//
// All compile-time constants for the firmware live here.
// Anything tunable from outside this header must be added
// here, not redefined in a .cpp.

#pragma once

#include <stdint.h>

namespace eisight {

// ---------------------------------------------------------
// Firmware identity
// ---------------------------------------------------------
// EISIGHT_FW_VERSION is supplied by platformio.ini build_flags
// as a string literal, e.g. "eisight-fw-0.1.0".
constexpr const char* kFwVersion  = EISIGHT_FW_VERSION;
//
// module_id is intentionally NOT a compile-time constant.
// Per v4.0c §F.4 and §F.5, the first bench sweeps are
// Module A in as-received Direct mode (the F.4 1 kΩ sanity
// gate). Module B is reworked to Dual-AFE only after F.4
// passes. A hard-coded module_id would mislabel the entire
// first session. The operator sets it at runtime via the
// 'm' serial command; see src/session.h.

// ---------------------------------------------------------
// Serial
// ---------------------------------------------------------
// Must match platformio.ini monitor_speed. 921600 keeps
// JSONL emission well below sweep timing budget; at 115200
// a 96-point sweep takes ~1 s just to transmit and can mask
// watchdog-relevant bugs.
constexpr uint32_t kSerialBaud    = 921600;

// ---------------------------------------------------------
// I2C
// ---------------------------------------------------------
constexpr uint8_t  kPinSda        = 21;     // ESP32 default SDA
constexpr uint8_t  kPinScl        = 22;     // ESP32 default SCL
// Start at 100 kHz I2C per v4.0c §C.4. The reason is the
// documented ESP32 <-> AD5933 I2C hang behavior that
// requires firmware-level workarounds (see the
// mjmeli/arduino-ad5933 ESP32 patch). 400 kHz is permitted
// by the AD5933 datasheet but only after 100 kHz is proven
// stable in F.4.
constexpr uint32_t kI2cClockHz    = 100000;

constexpr uint8_t  kAd5933Addr    = 0x0D;   // AD5933 7-bit I2C addr

// ---------------------------------------------------------
// DS18B20
// ---------------------------------------------------------
constexpr uint8_t  kPinDs18b20    = 4;      // OneWire data pin
constexpr uint8_t  kDs18b20Bits   = 12;     // resolution (0.0625 °C)

// ---------------------------------------------------------
// AD5933 clock
// ---------------------------------------------------------
// Internal oscillator. Per AD5933 datasheet rev D and UG-364.
constexpr double   kAd5933MclkHz  = 16.776e6;

// AD5933 collects 1024 ADC samples per DFT bin; the ADC
// sample clock is MCLK/16.
//   T_dft = 1024 / (MCLK / 16)
//         = 1024 / (16.776e6 / 16)
//         = 1024 / 1048500
//         ≈ 977 µs
constexpr uint32_t kDftWindowUs   = 977;

// ---------------------------------------------------------
// v4.0c default sweep, internal MCLK = 16.776 MHz
//   start_freq    = 5000 Hz
//   step          = 1000 Hz
//   num_points    = 96     (96 frequency points total)
//   num_increments= 95     (AD5933 register value: produces
//                           96 points incl. start point)
//   stop_freq     = start_freq + step * num_increments
//                 = 5000 + 1000 * 95 = 100000 Hz   ✓
// ---------------------------------------------------------
constexpr uint32_t kStartFreqHz     = 5000;
constexpr uint32_t kStepHz          = 1000;
constexpr uint16_t kNumPoints       = 96;
constexpr uint16_t kNumIncrements   = 95;   // = kNumPoints - 1
constexpr uint32_t kStopFreqHz      = 100000;

static_assert(kNumIncrements == kNumPoints - 1,
              "AD5933 NUM_INCREMENTS register value must equal "
              "kNumPoints - 1 (the start point is not counted "
              "as an increment).");
static_assert(kStartFreqHz + kStepHz * kNumIncrements == kStopFreqHz,
              "Worked sweep arithmetic must close: "
              "start + step * num_increments == stop.");

// ---------------------------------------------------------
// Excitation defaults (v4.0c §H.6)
// ---------------------------------------------------------
//   Range 4 (198 mVpp, 173 mV DC at VOUT)
//   PGA ×1
//   Settling cycles 15 (escalate to 30 or 100 if first-point
//                       bias is observed in F.10)
constexpr uint16_t kSettlingCycles  = 15;

// Bit values written to the AD5933 control register MSB
// (0x80, upper nibble). Encoded here as named constants so
// callers don't sprinkle magic numbers.
constexpr uint8_t  kCtrlRange4      = 0x03; // D10..D9 = 11
constexpr uint8_t  kCtrlPga1        = 0x01; // D8     = 1

// ---------------------------------------------------------
// Single-point sanity sweep (mode '1', F.4 sanity gate)
// ---------------------------------------------------------
// One-point sweep at 10 kHz on a 1 kΩ load to confirm signed
// real/imag readout works end-to-end before milk testing.
constexpr uint32_t kSinglePointFreqHz = 10000;

// ---------------------------------------------------------
// Watchdog
// ---------------------------------------------------------
// Per-frequency budget = K * (T_settle + T_dft + T_i2c).
//   T_settle = settling_cycles / f_exc           (computed)
//   T_dft    = kDftWindowUs                       (constant)
//   T_i2c    = ad5933::t_i2c_us_measured()        (live measurement;
//                                                  initialized to
//                                                  kI2cInitialUs and
//                                                  overwritten on the
//                                                  first STATUS poll
//                                                  of every sweep)
//   K        = kWatchdogSafetyK = 3
constexpr uint32_t kI2cInitialUs    = 1500;
constexpr uint32_t kWatchdogSafetyK = 3;

// Safety ceiling: even on the lowest-frequency point the
// budget should never balloon past this. Acts as a sanity
// limit if t_i2c_us_measured ever returns nonsense.
constexpr uint32_t kWatchdogMaxMs   = 250;

// ---------------------------------------------------------
// READ_REG_SANITY mode (mode 'r', F.4 100-iteration check)
// ---------------------------------------------------------
constexpr uint16_t kRegSanityIters  = 100;

// ---------------------------------------------------------
// Status / boot LED
// ---------------------------------------------------------
constexpr uint8_t  kPinStatusLed    = 2;    // ESP32 dev-kit blue LED

}  // namespace eisight
