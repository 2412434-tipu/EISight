// EISight v4.0c firmware — AD5933 driver.
//
// Datasheet citations refer to AD5933 Rev D (Analog Devices
// "1 MSPS, 12-Bit Impedance Converter, Network Analyzer").
// Blueprint citations refer to docs/EISight_Blueprint_v4_0c.

#include "ad5933.h"

#include <Arduino.h>
#include <Wire.h>
#include <esp_timer.h>

#include "config.h"
#include "registers.h"

namespace eisight {
namespace ad5933 {

namespace {

// Watchdog calibration state, scoped to this translation
// unit. reset_i2c_timing() clears these at sweep start.
uint32_t s_t_i2c_us_measured       = kI2cInitialUs;
bool     s_i2c_measured_this_sweep = false;

// Single-register write. Datasheet p. 18 "Writing to a
// Single Register":  START + addr/W + reg + data + STOP.
bool write_register(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(kAd5933Addr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

// Single-register read. Datasheet p. 18 "Reading from a
// Single Register": load the internal address pointer with
// kBusCmdAddressPointer + reg, then a fresh requestFrom()
// returns one byte at that address.
bool read_register(uint8_t reg, uint8_t* out) {
  Wire.beginTransmission(kAd5933Addr);
  Wire.write(kBusCmdAddressPointer);
  Wire.write(reg);
  if (Wire.endTransmission() != 0) return false;
  if (Wire.requestFrom((uint8_t)kAd5933Addr, (uint8_t)1) != 1) return false;
  *out = Wire.read();
  return true;
}

// Frequency-to-register-code conversion.
//
// Datasheet p. 24 "Frequency Sweep Parameters", equation:
//
//   Code = ( f_des / (MCLK / 4) ) * 2^27
//
// Where f_des is the desired output frequency in Hz and
// MCLK is the master clock (16.776 MHz internal per UG-364).
// Same formula applies to the start-frequency register and
// to the frequency-increment register (p. 25).
uint32_t encode_freq_to_24bit_code(double f_hz) {
  const double scale = (double)(1UL << 27) / (kAd5933MclkHz / 4.0);
  double code_d = f_hz * scale;
  if (code_d < 0)            code_d = 0;
  if (code_d > 16777215.0)   code_d = 16777215.0;  // 2^24 - 1
  return (uint32_t)(code_d + 0.5);
}

// Datasheet p. 25 "Programming the Start Frequency Register"
// and "Programming the Frequency Increment Register":
// write the 24-bit code MSB-first into three consecutive
// registers (Hi, Mid, Lo).
bool write_24bit_freq(uint8_t reg_hi, uint32_t code) {
  if (!write_register(reg_hi + 0, (uint8_t)((code >> 16) & 0xFF))) return false;
  if (!write_register(reg_hi + 1, (uint8_t)((code >>  8) & 0xFF))) return false;
  if (!write_register(reg_hi + 2, (uint8_t)((code >>  0) & 0xFF))) return false;
  return true;
}

// Replace just the command nibble (D15..D12) of CTRL_MSB,
// preserving the range and PGA bits already programmed.
bool send_command(uint8_t cmd) {
  uint8_t cur;
  if (!read_register(kRegCtrlMsb, &cur)) return false;
  uint8_t next = (cur & 0x0F) | cmd;
  return write_register(kRegCtrlMsb, next);
}

}  // namespace

// =========================================================
// Public API
// =========================================================

// Blueprint §I.2.a frozen rule. Combine MSB:LSB and cast
// through int16_t (NOT uint16_t→int) so negative values
// sign-extend correctly on the ESP32's 32-bit `int`.
int16_t parse_int16(uint8_t msb, uint8_t lsb) {
  return (int16_t)(((uint16_t)msb << 8) | lsb);
}

// Blueprint §I.2.a frozen rule. 14-bit signed value in a
// 16-bit envelope. D15..D14 are don't-cares; D13 is the
// sign bit. Mask to 14 bits, sign-extend if D13 set, scale
// by 0.03125 °C/LSB (= 1/32 °C).
float parse_temp14_c(uint8_t msb, uint8_t lsb) {
  const uint16_t raw = ((uint16_t)msb << 8) | lsb;
  int16_t s14 = (int16_t)(raw & 0x3FFF);
  if (s14 & 0x2000) s14 -= 0x4000;
  return (float)s14 * 0.03125f;
}

bool begin() {
  Wire.begin(kPinSda, kPinScl);
  Wire.setClock(kI2cClockHz);

  // Address-only ACK probe.
  Wire.beginTransmission(kAd5933Addr);
  if (Wire.endTransmission() != 0) return false;

  // Internal MCLK, no reset asserted.
  if (!write_register(kRegCtrlLsb, kCtrlLsbIntClock)) return false;
  // Park in standby.
  if (!write_register(kRegCtrlMsb, kCmdStandby)) return false;
  return true;
}

bool soft_reset() {
  // Datasheet p. 23 "Reset": setting CTRL_LSB D4 = 1 aborts
  // the sweep and parks the state machine in standby; the
  // frequency-sweep registers retain their programmed values.
  // We must clear D4 afterwards (otherwise a subsequent
  // INIT_WITH_START is immediately reset again) and write
  // kCmdStandby into CTRL_MSB so the next command nibble is
  // unambiguous.
  if (!write_register(kRegCtrlLsb,
                      kCtrlLsbIntClock | kCtrlLsbReset)) return false;
  delay(1);
  if (!write_register(kRegCtrlLsb, kCtrlLsbIntClock)) return false;
  if (!write_register(kRegCtrlMsb, kCmdStandby))      return false;
  return true;
}

bool program_sweep(uint32_t start_freq_hz,
                   uint32_t step_hz,
                   uint16_t num_increments,
                   uint16_t settling_cycles,
                   uint8_t  range_bits,
                   uint8_t  pga_bit) {
  // Start frequency: 24-bit code into 0x82/0x83/0x84.
  if (!write_24bit_freq(kRegStartFreqHi,
                        encode_freq_to_24bit_code((double)start_freq_hz))) {
    return false;
  }
  // Frequency increment: 24-bit code into 0x85/0x86/0x87.
  if (!write_24bit_freq(kRegFreqIncHi,
                        encode_freq_to_24bit_code((double)step_hz))) {
    return false;
  }

  // Number of increments. Datasheet p. 25: 9-bit field — D8
  // in 0x88 (upper 7 bits reserved), D7..D0 in 0x89. Register
  // holds num_increments = num_points - 1.
  if (num_increments > 511) return false;
  if (!write_register(kRegNumIncMsb,
                      (uint8_t)((num_increments >> 8) & 0x01))) return false;
  if (!write_register(kRegNumIncLsb,
                      (uint8_t)(num_increments & 0xFF)))         return false;

  // Settling cycles. Datasheet p. 25 "Number of Settling Time
  // Cycles Register": 0x8A holds D15..D8 (D10..D9 multiplier,
  // D8 count high bit, D15..D11 reserved); 0x8B holds the low
  // 8 bits of the count. v4.0c always uses ×1 multiplier.
  if (settling_cycles > 511) return false;
  const uint8_t s_msb = kSettlingMultX1
                      | (uint8_t)((settling_cycles >> 8) & 0x01);
  const uint8_t s_lsb = (uint8_t)(settling_cycles & 0xFF);
  if (!write_register(kRegSettlingMsb, s_msb)) return false;
  if (!write_register(kRegSettlingLsb, s_lsb)) return false;

  // CTRL_MSB byte layout: see registers.h. Park at standby
  // with the desired range/PGA loaded; the sweep state-
  // machine commands later replace only the command nibble.
  const uint8_t ctrl_msb = kCmdStandby
                         | (uint8_t)((range_bits & 0x03) << 1)
                         | (uint8_t)((pga_bit    & 0x01) << 0);
  if (!write_register(kRegCtrlMsb, ctrl_msb))         return false;
  if (!write_register(kRegCtrlLsb, kCtrlLsbIntClock)) return false;
  return true;
}

bool init_with_start()  { return send_command(kCmdInitWithStart); }
bool start_sweep()      { return send_command(kCmdStartSweep);    }
bool increment_freq()   { return send_command(kCmdIncrementFreq); }
bool repeat_freq()      { return send_command(kCmdRepeatFreq);    }
bool standby()          { return send_command(kCmdStandby);       }

bool read_status(uint8_t* out_status) {
  return read_register(kRegStatus, out_status);
}

bool read_point_signed(int16_t* out_real, int16_t* out_imag) {
  uint8_t real_msb, real_lsb, imag_msb, imag_lsb;
  if (!read_register(kRegRealMsb, &real_msb)) return false;
  if (!read_register(kRegRealLsb, &real_lsb)) return false;
  if (!read_register(kRegImagMsb, &imag_msb)) return false;
  if (!read_register(kRegImagLsb, &imag_lsb)) return false;

  *out_real = parse_int16(real_msb, real_lsb);
  *out_imag = parse_int16(imag_msb, imag_lsb);
  return true;
}

bool read_internal_temp_c(float* out_c) {
  // Issue MEASURE_TEMP. Datasheet p. 22: completes in <800 µs.
  if (!send_command(kCmdMeasureTemp)) return false;

  // Poll STATUS for VALID_TEMP. 10 ms is comfortably above
  // the datasheet bound and below any sweep budget.
  const int64_t deadline_us = esp_timer_get_time() + 10000;
  uint8_t status = 0;
  do {
    if (!read_register(kRegStatus, &status)) return false;
    if (status & kStatusValidTemp) break;
  } while (esp_timer_get_time() < deadline_us);
  if (!(status & kStatusValidTemp)) return false;

  uint8_t t_msb, t_lsb;
  if (!read_register(kRegTempMsb, &t_msb)) return false;
  if (!read_register(kRegTempLsb, &t_lsb)) return false;
  *out_c = parse_temp14_c(t_msb, t_lsb);
  return true;
}

uint32_t watchdog_budget_us(uint32_t f_exc_hz,
                            uint16_t settling_cycles) {
  if (f_exc_hz == 0) f_exc_hz = 1;
  const uint64_t t_settle_us =
      ((uint64_t)settling_cycles * 1000000ULL) / f_exc_hz;
  const uint64_t budget = (uint64_t)kWatchdogSafetyK *
      (t_settle_us + (uint64_t)kDftWindowUs +
       (uint64_t)s_t_i2c_us_measured);
  const uint64_t cap_us = (uint64_t)kWatchdogMaxMs * 1000ULL;
  return (uint32_t)((budget < cap_us) ? budget : cap_us);
}

void reset_i2c_timing() {
  s_t_i2c_us_measured       = kI2cInitialUs;
  s_i2c_measured_this_sweep = false;
}

uint32_t t_i2c_us_measured() {
  return s_t_i2c_us_measured;
}

bool wait_for_status_bit(uint8_t mask, uint32_t budget_us) {
  const int64_t start_us    = esp_timer_get_time();
  const int64_t deadline_us = start_us + (int64_t)budget_us;
  uint8_t status = 0;
  while (true) {
    const int64_t t0 = esp_timer_get_time();
    if (!read_register(kRegStatus, &status)) return false;
    const int64_t t1 = esp_timer_get_time();

    // First successful read of the sweep: lock in the actual
    // measured I2C round-trip for downstream budget calcs.
    if (!s_i2c_measured_this_sweep) {
      const int64_t dt = t1 - t0;
      if (dt > 0 && dt < (int64_t)kWatchdogMaxMs * 1000) {
        s_t_i2c_us_measured = (uint32_t)dt;
      }
      s_i2c_measured_this_sweep = true;
    }

    if (status & mask) return true;
    if (t1 >= deadline_us) return false;
  }
}

}  // namespace ad5933
}  // namespace eisight
