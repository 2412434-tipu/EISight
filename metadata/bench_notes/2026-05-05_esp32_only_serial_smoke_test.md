# ESP32-only serial smoke test

Date: 2026-05-05
Port: COM11
Firmware: eisight-fw-0.1.0
Baud: 921600

Result: PASS

Evidence:
- PlatformIO upload succeeded.
- ESP32 detected as ESP32-D0WD-V3.
- Serial monitor opened on COM11.
- Firmware emitted JSONL:
  {"type":"error","detail":"ad5933 begin failed"}
  {"type":"error","detail":"ds18b20 begin failed"}
  {"type":"hello","fw":"eisight-fw-0.1.0","module_id":null}

Interpretation:
- ESP32 firmware boots.
- USB serial path works.
- AD5933 and DS18B20 errors are expected because peripherals were not connected.
- No AD5933 power/wiring was performed in this gate.
