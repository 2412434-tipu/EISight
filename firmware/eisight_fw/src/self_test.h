// EISight v4.0c firmware — boot-time §I.2.a parse self-tests.
//
// The signed-int16 and 14-bit-signed-temperature parse
// paths in src/ad5933.cpp are the §I.2.a frozen rule. A
// regression in either one would silently corrupt every
// sweep this firmware emits — the laptop pipeline trusts
// wire-side parsing as authoritative and does not redo it.
//
// To catch such a regression at boot, main.cpp::setup()
// runs run_int16_parse() and run_temp14_parse() against
// fixed bit-pattern fixtures BEFORE ad5933::begin() is
// allowed to execute. On any failure the corresponding
// function emits exactly one jsonl::write_self_test_fail
// packet naming the offending fixture, and main.cpp halts
// the boot sequence (no hello, no mode dispatch).

#pragma once

namespace eisight {
namespace self_test {

// Six §I.2.a int16 fixtures covering zero, +1, -1,
// INT16_MAX, INT16_MIN, and -567 (the imag value from the
// §I.4 example packet). Calls ad5933::parse_int16 on each.
// Returns true on full pass; on first mismatch emits
// write_self_test_fail and returns false.
bool run_int16_parse();

// Two §I.2.a temp14 fixtures: +25.0 °C with the don't-care
// bits (D15..D14) set high (exercises the 0x3FFF mask) and
// -25.0 °C with D13 set (exercises sign extension). Calls
// ad5933::parse_temp14_c on each. Comparison epsilon is
// 1e-4 °C; 0.03125 is exactly representable in IEEE-754
// float so the expected values are exact, not approximate.
// Returns true on full pass; on mismatch emits
// write_self_test_fail and returns false.
bool run_temp14_parse();

}  // namespace self_test
}  // namespace eisight
