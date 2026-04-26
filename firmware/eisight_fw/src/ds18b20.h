// EISight v4.0c firmware — DS18B20 sample-temperature reader.
//
// Per blueprint §H.7: read once before sweep_begin and once
// after sweep_end. Never during AD5933 conversion. The
// software-side QC enforces the |T_post - T_pre| <= 0.5 °C
// rejection rule; firmware only reports the values.

#pragma once

#include <stdint.h>

namespace eisight {
namespace ds18b20 {

// Initialize OneWire on kPinDs18b20, locate the first
// DS18B20 on the bus, cache its 8-byte ROM address, and
// program 12-bit resolution (0.0625 °C / LSB). Returns
// false if no device is found.
bool begin();

// Whether begin() succeeded and a device address is cached.
bool has_device();

// Blocking single-shot read. Triggers a conversion (~750 ms
// at 12-bit), reads the scratchpad, returns the temperature
// in °C. On any failure (no device, CRC error, disconnected
// scratchpad value) returns false and sets *out_c = NaN.
bool read_c(float* out_c);

}  // namespace ds18b20
}  // namespace eisight
