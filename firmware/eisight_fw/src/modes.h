// EISight v4.0c firmware — single-character mode dispatcher.
//
// main.cpp's loop() reads one byte at a time from Serial
// and passes it here. Each mode emits a complete JSONL
// record set — no streaming state survives across modes —
// so dropped or transposed bytes degrade gracefully.
//
// Recognized commands:
//   m  set module_id (always allowed; routed to session.h)
//   s  SCAN_I2C        — probe 0x08..0x77, list ACK'd addrs
//   r  READ_REG_SANITY — 100 iters of STATUS + die-temp read
//   1  SINGLE_10KHZ    — single-point sanity sweep at 10 kHz
//   f  SWEEP_5K_100K   — v4.0c default 96-point sweep
//   t  TEMP_ONLY       — DS18B20 + AD5933 die temperatures
//
// All non-'m' modes are gated on session::is_set(); when
// it is not set the dispatcher emits one
// jsonl::write_error and skips the mode body. Whitespace
// and other non-printable bytes are silently ignored so
// terminal framing characters don't generate "unknown
// command" noise.

#pragma once

namespace eisight {
namespace modes {

void run(char cmd);

}  // namespace modes
}  // namespace eisight
