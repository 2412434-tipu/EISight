// EISight v4.0c firmware — single-character mode dispatcher.

#include "modes.h"

#include <Arduino.h>
#include <Wire.h>
#include <math.h>
#include <stdio.h>

#include "ad5933.h"
#include "config.h"
#include "ds18b20.h"
#include "jsonl.h"
#include "registers.h"
#include "session.h"

namespace eisight {
namespace modes {

namespace {

uint16_t g_sweep_counter = 0;

// Spread READ_REG_SANITY iterations over ~1 s so the JSONL
// log is human-readable at 921600 baud and an I2C hang is
// easy to spot relative to wall time.
constexpr uint32_t kRegSanityIterDelayMs = 10;

// Mode 's'. Probe the standard 7-bit I2C range. 0x00..0x07
// and 0x78..0x7F are reserved per the I2C spec, so we scan
// 0x08..0x77. The result buffer is sized for the realistic
// worst case on this fixture (AD5933 + DS18B20 plus a
// little headroom).
void scan_i2c() {
  uint8_t addrs[16];
  size_t  count = 0;
  for (uint8_t a = 0x08; a <= 0x77; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      if (count < sizeof(addrs)) addrs[count++] = a;
    }
  }
  jsonl::write_scan_result(session::id(), addrs, count);
}

// Mode 'r'. Blueprint §F.4 100-iteration loop that
// exercises the I2C path without starting a sweep — flushes
// out the documented ESP32 ↔ AD5933 hang behavior before
// milk testing risks losing data.
void read_reg_sanity() {
  for (uint16_t i = 0; i < kRegSanityIters; i++) {
    uint8_t status = 0;
    if (!ad5933::read_status(&status)) {
      jsonl::write_error("reg_sanity: status read failed");
      return;
    }
    float t_c = NAN;
    ad5933::read_internal_temp_c(&t_c);  // null on failure
    jsonl::write_reg_sanity_iter(i, status, t_c);
    delay(kRegSanityIterDelayMs);
  }
}

// Mode 't'. Quick fixturing probe — both temperatures, no
// sweep. Failures from either sensor surface as JSON null
// in the emitted record.
void temp_only() {
  float ds_c = NAN, ad_c = NAN;
  ds18b20::read_c(&ds_c);
  ad5933::read_internal_temp_c(&ad_c);
  jsonl::write_temp_only(ds_c, ad_c);
}

// Shared sweep body for modes '1' and 'f'. The caller is
// responsible for num_points >= 1. session_id, cell_id,
// row_type, and load_id are emitted as empty strings: they
// are operator/laptop concepts and the laptop pipeline
// annotates them on ingest.
void run_sweep(uint32_t start_hz,
               uint32_t step_hz,
               uint16_t num_points) {
  const uint16_t num_inc = (uint16_t)(num_points - 1);
  const uint32_t stop_hz = start_hz + step_hz * num_inc;

  char sweep_id[12];
  snprintf(sweep_id, sizeof(sweep_id), "SWP%04u",
           (unsigned)g_sweep_counter);
  g_sweep_counter++;

  float ds_pre = NAN, ad_pre = NAN;
  ds18b20::read_c(&ds_pre);
  ad5933::read_internal_temp_c(&ad_pre);

  jsonl::write_sweep_begin(
      /*session_id=*/"", sweep_id, session::id(),
      /*cell_id=*/"", /*row_type=*/"", /*load_id=*/"",
      start_hz, stop_hz, num_points,
      "RANGE_4", "X1", kSettlingCycles,
      ds_pre, ad_pre);

  const uint32_t t_start_ms = millis();
  ad5933::reset_i2c_timing();

  // program_sweep parks the device in standby with range
  // and PGA loaded; no extra standby() call is needed
  // before init_with_start.
  const char* err = nullptr;
  bool ok = ad5933::program_sweep(start_hz, step_hz, num_inc,
                                   kSettlingCycles,
                                   kCtrlRange4, kCtrlPga1);
  if (!ok)                                    err = "program_sweep failed";
  if (ok && !ad5933::init_with_start()) {     ok = false; err = "init_with_start failed"; }
  if (ok && !ad5933::start_sweep())     {     ok = false; err = "start_sweep failed"; }

  for (uint16_t i = 0; ok && i < num_points; i++) {
    const uint32_t f_hz = start_hz + step_hz * i;
    const uint32_t budget_us =
        ad5933::watchdog_budget_us(f_hz, kSettlingCycles);
    if (!ad5933::wait_for_status_bit(
            ad5933::kStatusDataReady, budget_us)) {
      ad5933::soft_reset();
      ok  = false;
      err = "watchdog timeout";
      break;
    }
    int16_t real = 0, imag = 0;
    uint8_t status = 0;
    if (!ad5933::read_point_signed(&real, &imag) ||
        !ad5933::read_status(&status)) {
      ok  = false;
      err = "read_point failed";
      break;
    }
    jsonl::write_data(sweep_id, i, (double)f_hz,
                      real, imag, status);
    if (i + 1 < num_points) {
      if (!ad5933::increment_freq()) {
        ok  = false;
        err = "increment_freq failed";
        break;
      }
    }
  }

  ad5933::standby();

  float ds_post = NAN, ad_post = NAN;
  ds18b20::read_c(&ds_post);
  ad5933::read_internal_temp_c(&ad_post);

  jsonl::write_sweep_end(sweep_id, ds_post, ad_post,
                         millis() - t_start_ms, err);
}

// Mode '1'. F.4 sanity gate: one point at 10 kHz on the
// 1 kΩ load. Confirms the §I.2.a signed parse works
// end-to-end before the full 96-point sweep is trusted.
void single_10khz() {
  run_sweep(kSinglePointFreqHz, /*step_hz=*/0,
            /*num_points=*/1);
}

// Mode 'f'. v4.0c default sweep — 96 points, 5–100 kHz,
// step 1 kHz, Range 4, PGA ×1, 15 settling cycles.
void sweep_5k_100k() {
  run_sweep(kStartFreqHz, kStepHz, kNumPoints);
}

}  // namespace

void run(char cmd) {
  // Drop framing bytes (NUL through space — covers CR, LF,
  // tab, real space) and the negative range a signed-char
  // ESP32 reports for high-bit bytes. These are noise from
  // line-oriented terminals, not commands.
  if (cmd <= ' ') return;

  // 'm' is the one command allowed before a module_id is
  // registered, since registering it is its purpose.
  if (cmd == 'm') {
    session::handle_set_command();
    return;
  }

  if (!session::is_set()) {
    jsonl::write_error(
        "module_id not set; run 'm <id>' first");
    return;
  }

  switch (cmd) {
    case 's': scan_i2c();        break;
    case 'r': read_reg_sanity(); break;
    case '1': single_10khz();    break;
    case 'f': sweep_5k_100k();   break;
    case 't': temp_only();       break;
    default:  jsonl::write_error("unknown command"); break;
  }
}

}  // namespace modes
}  // namespace eisight
