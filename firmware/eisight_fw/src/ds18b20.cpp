// EISight v4.0c firmware — DS18B20 sample-temperature reader.

#include "ds18b20.h"

#include <Arduino.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <math.h>

#include "config.h"

namespace eisight {
namespace ds18b20 {

namespace {

OneWire           s_one_wire(kPinDs18b20);
DallasTemperature s_sensors(&s_one_wire);
DeviceAddress     s_addr        = {0};
bool              s_has_device  = false;

}  // namespace

bool begin() {
  s_sensors.begin();

  // The DallasTemperature library scans the bus during
  // begin(); ask for the count and grab the first ROM.
  if (s_sensors.getDeviceCount() == 0) {
    s_has_device = false;
    return false;
  }
  if (!s_sensors.getAddress(s_addr, 0)) {
    s_has_device = false;
    return false;
  }

  // 12-bit resolution → 0.0625 °C / LSB, ~750 ms conversion.
  // Worth the extra time: pre/post sweep is blocking by
  // design (we never read during AD5933 conversion).
  s_sensors.setResolution(s_addr, kDs18b20Bits);
  // Blocking mode: requestTemperatures() returns only after
  // the conversion delay has elapsed. Matches the §H.7
  // "before sweep_begin / after sweep_end" cadence.
  s_sensors.setWaitForConversion(true);

  s_has_device = true;
  return true;
}

bool has_device() {
  return s_has_device;
}

bool read_c(float* out_c) {
  if (!s_has_device) {
    *out_c = NAN;
    return false;
  }

  s_sensors.requestTemperaturesByAddress(s_addr);
  const float t_c = s_sensors.getTempC(s_addr);

  // DallasTemperature returns DEVICE_DISCONNECTED_C (-127.0)
  // on any failure (no ACK, CRC mismatch, scratchpad zeroed).
  if (t_c == DEVICE_DISCONNECTED_C || isnan(t_c)) {
    *out_c = NAN;
    return false;
  }

  *out_c = t_c;
  return true;
}

}  // namespace ds18b20
}  // namespace eisight
