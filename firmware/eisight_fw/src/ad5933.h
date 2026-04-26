// EISight v4.0c firmware — AD5933 driver public API.
//
// Implements the §I.2 firmware responsibilities from the
// blueprint. The signed-int16 result-register read in
// read_point_signed() is the §I.2.a frozen-rule path and
// uses Wire directly so the parser is in our own auditable
// code, not a third-party library call.

#pragma once

#include <stdint.h>

namespace eisight {
namespace ad5933 {

// One-time bus and device init. Sets Wire pins/clock,
// confirms the device ACKs at kAd5933Addr, and parks it in
// standby with the internal MCLK selected. Returns false on
// any I2C error.
bool begin();

// Issue datasheet "Reset" (CTRL_LSB D4 = 1, then 0) and
// re-issue the standby command into CTRL_MSB. Used by the
// watchdog timeout path and on demand from the host.
bool soft_reset();

// Program all sweep parameters. Does NOT start the sweep.
// On exit the device is in standby with valid start_freq,
// freq_increment, num_increments, settling, range, and PGA
// loaded.
bool program_sweep(uint32_t start_freq_hz,
                   uint32_t step_hz,
                   uint16_t num_increments,
                   uint16_t settling_cycles,
                   uint8_t  range_bits,
                   uint8_t  pga_bit);

// Sweep state-machine commands. Each preserves the range
// and PGA bits already in CTRL_MSB and replaces only the
// command nibble.
bool init_with_start();
bool start_sweep();
bool increment_freq();
bool repeat_freq();
bool standby();

// Status register read. STATUS bits live in registers.h
// (kStatusValidTemp / kStatusDataReady / kStatusSweepDone).
bool read_status(uint8_t* out_status);

// Read one DFT result point as signed int16 per §I.2.a.
// Caller is responsible for ensuring kStatusDataReady is
// set first (typically via wait_for_status_bit).
bool read_point_signed(int16_t* out_real, int16_t* out_imag);

// Read AD5933 internal die temperature in °C.
// Issues kCmdMeasureTemp, waits for kStatusValidTemp, then
// parses the 14-bit signed value per §I.2.a.
bool read_internal_temp_c(float* out_c);

// Watchdog: poll STATUS until any bit in `mask` is set, or
// `budget_us` elapses. Microsecond timing via
// esp_timer_get_time(). On the FIRST successful poll of a
// sweep, measures the actual I2C round-trip time and
// updates t_i2c_us_measured() for downstream budget calcs.
bool wait_for_status_bit(uint8_t mask, uint32_t budget_us);

// Compute the per-frequency watchdog budget from the
// current excitation frequency, settling-cycle count, the
// measured I2C round-trip time, and the K=3 safety factor.
// See config.h kWatchdog* for the components.
uint32_t watchdog_budget_us(uint32_t f_exc_hz,
                            uint16_t settling_cycles);

// Reset the per-sweep watchdog calibration state. Call at
// the start of every sweep so a stale measurement from a
// prior session does not carry over.
void reset_i2c_timing();

// Last calibrated I2C round-trip time in microseconds.
// Equal to kI2cInitialUs (1500) until the first successful
// status poll of the current sweep.
uint32_t t_i2c_us_measured();

}  // namespace ad5933
}  // namespace eisight
