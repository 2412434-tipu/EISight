// EISight v4.0c firmware — JSONL packet writers.
//
// Each writer emits exactly one JSON object followed by a
// newline on Serial. Schemas match blueprint §I.4. The
// firmware never buffers across records: any caller that
// returns mid-packet has either Serial.println'd a complete
// line or emitted nothing.
//
// String inputs (session_id, sweep_id, module_id, cell_id,
// row_type, load_id, error, detail) MUST be ASCII-printable
// with no embedded double-quotes or backslashes. The
// session module-id validator already enforces this; other
// callers are firmware-internal constants. No JSON escaping
// is performed on this side of the wire.

#pragma once

#include <stdint.h>
#include <stddef.h>

namespace eisight {
namespace jsonl {

// {"type":"hello","fw":"<ver>","module_id":<str|null>}
// module_id may be nullptr until the operator runs 'm <id>'.
void write_hello(const char* module_id);

// {"type":"module_id_set","module_id":"<id>"}
// Sent once after a successful 'm <id>' command.
void write_module_id_set(const char* module_id);

// {"type":"sweep_begin", ...}  per §I.4.
// NaN floats are emitted as JSON literal null.
void write_sweep_begin(const char* session_id,
                       const char* sweep_id,
                       const char* module_id,
                       const char* cell_id,
                       const char* row_type,
                       const char* load_id,
                       uint32_t    start_hz,
                       uint32_t    stop_hz,
                       uint16_t    points,
                       const char* range_str,
                       const char* pga_str,
                       uint16_t    settling_cycles,
                       float       ds18b20_pre_c,
                       float       ad5933_pre_c);

// {"type":"data","sweep_id":"...","idx":N,
//  "frequency_hz":F,"real":R,"imag":I,"status":S}
void write_data(const char* sweep_id,
                uint16_t    idx,
                double      frequency_hz,
                int16_t     real,
                int16_t     imag,
                uint8_t     status);

// {"type":"sweep_end", ...}  per §I.4.
// error may be nullptr (clean run) or an ASCII reason.
void write_sweep_end(const char* sweep_id,
                     float       ds18b20_post_c,
                     float       ad5933_post_c,
                     uint32_t    elapsed_ms,
                     const char* error);

// {"type":"error","detail":"<detail>"}
void write_error(const char* detail);

// {"type":"self_test_fail","detail":"<detail>"}
void write_self_test_fail(const char* detail);

// {"type":"i2c_scan","module_id":<str|null>,"addrs":[0xNN,...]}
// Used by mode 's' SCAN_I2C. addrs is an array of 7-bit
// addresses; count is the number of valid entries.
void write_scan_result(const char* module_id,
                       const uint8_t* addrs,
                       size_t count);

// {"type":"reg_sanity","iter":N,"status":S,"ad5933_c":T}
// Per-iteration record for mode 'r' READ_REG_SANITY.
void write_reg_sanity_iter(uint16_t iter,
                           uint8_t  status,
                           float    ad5933_c);

// {"type":"temp_only","ds18b20_c":<v|null>,"ad5933_c":<v|null>}
// Sole record emitted by mode 't' TEMP_ONLY.
void write_temp_only(float ds18b20_c, float ad5933_c);

}  // namespace jsonl
}  // namespace eisight
