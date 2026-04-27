// EISight v4.0c firmware — boot-time §I.2.a parse self-tests.
//
// Fixtures are intentionally compile-time data so the test
// itself cannot drift from production: parse_int16 and
// parse_temp14_c are called directly from ad5933.h, not
// re-implemented here. A typo in either production parser
// is caught by these eight bit patterns at boot.

#include "self_test.h"

#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "ad5933.h"
#include "jsonl.h"

namespace eisight {
namespace self_test {

namespace {

struct Int16Fixture {
  uint8_t msb;
  uint8_t lsb;
  int16_t expected;
};

// Six fixtures spanning both signs and both edges. The
// 0xFFFF / -1 case is the load-bearing one: a parser that
// went uint16_t -> int (skipping the int16_t cast) would
// return +65535 here on the ESP32's 32-bit `int` and pass
// every other fixture in the set.
constexpr Int16Fixture kInt16Fixtures[] = {
  {0x00, 0x00,      0},  // zero
  {0x00, 0x01,     +1},  // smallest positive
  {0xFF, 0xFF,     -1},  // smallest negative; sign-cast gate
  {0x7F, 0xFF, +32767},  // INT16_MAX
  {0x80, 0x00, -32768},  // INT16_MIN; sign bit alone
  {0xFD, 0xC9,   -567},  // matches §I.4 example imag value
};

struct Temp14Fixture {
  uint8_t msb;
  uint8_t lsb;
  float   expected_c;
};

// +25.0 °C with D15..D14 set high so a parser that forgot
// the 0x3FFF mask would read 0xC320 = 49952 raw and produce
// a garbage temperature. -25.0 °C with D13 set so a parser
// that skipped the D13 sign-extension branch would read
// +15584 and produce +487 °C. Together these cover both
// branches of parse_temp14_c.
constexpr Temp14Fixture kTemp14Fixtures[] = {
  {0xC3, 0x20, +25.0f},
  {0xFC, 0xE0, -25.0f},
};

constexpr float kTempEpsilonC = 1e-4f;

}  // namespace

bool run_int16_parse() {
  constexpr size_t N =
      sizeof(kInt16Fixtures) / sizeof(kInt16Fixtures[0]);
  for (size_t i = 0; i < N; i++) {
    const Int16Fixture& f = kInt16Fixtures[i];
    const int16_t got = ad5933::parse_int16(f.msb, f.lsb);
    if (got != f.expected) {
      char detail[96];
      snprintf(detail, sizeof(detail),
        "int16 fixture %u: msb=0x%02X lsb=0x%02X "
        "got=%d expected=%d",
        (unsigned)i,
        (unsigned)f.msb, (unsigned)f.lsb,
        (int)got, (int)f.expected);
      jsonl::write_self_test_fail(detail);
      return false;
    }
  }
  return true;
}

bool run_temp14_parse() {
  constexpr size_t N =
      sizeof(kTemp14Fixtures) / sizeof(kTemp14Fixtures[0]);
  for (size_t i = 0; i < N; i++) {
    const Temp14Fixture& f = kTemp14Fixtures[i];
    const float got  = ad5933::parse_temp14_c(f.msb, f.lsb);
    const float diff = fabsf(got - f.expected_c);
    if (diff > kTempEpsilonC) {
      char detail[112];
      snprintf(detail, sizeof(detail),
        "temp14 fixture %u: msb=0x%02X lsb=0x%02X "
        "got=%.4f expected=%.4f",
        (unsigned)i,
        (unsigned)f.msb, (unsigned)f.lsb,
        (double)got, (double)f.expected_c);
      jsonl::write_self_test_fail(detail);
      return false;
    }
  }
  return true;
}

}  // namespace self_test
}  // namespace eisight
