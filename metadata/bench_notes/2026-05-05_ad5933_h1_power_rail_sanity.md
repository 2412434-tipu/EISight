# AD5933 power-rail sanity

Date: 2026-05-05
Gate: H1
Result: PASS

Hardware state:
- AD5933 modules tested one at a time.
- Power only: ESP32 5V/VIN to AD5933 +5V, ESP32 GND to AD5933 GND.
- No SDA/SCL connection.
- No Vin/Vout sample connection.
- No resistor load.
- No DS18B20 connection.
- No jumper rework performed.

Module A:
ID: AD5933-A-DIRECT

Unpowered short check:
- +5V to GND: 1.3 MΩ

Powered measurements:
- AD5933 +5V to GND: 5.13 V
- AD5933 +3.3V to GND: 3.31 V
- AD5933 SDA to GND: 3.31 V
- AD5933 SCL to GND: 3.31 V

Observations:
- Red module power LED turned on.
- No smell.
- No heat.
- No USB instability.
- No unusual behavior.

Module B:
ID: AD5933-B-DIRECT-PRE-REWORK

Unpowered short check:
- +5V to GND: 0.95 MΩ

Powered measurements:
- AD5933 +5V to GND: 5.14 V
- AD5933 +3.3V to GND: 3.31 V
- AD5933 SDA to GND: 3.31 V
- AD5933 SCL to GND: 3.31 V

Observations:
- Red module power LED turned on.
- No smell.
- No heat.
- No USB instability.
- No unusual behavior.

Serial observation during Module A power-only state:
- Firmware emitted ad5933 begin failed.
- Firmware emitted ds18b20 begin failed.
- Firmware emitted hello.
- These errors were expected because SDA/SCL and DS18B20 were intentionally disconnected.

Interpretation:
- Both AD5933 modules pass power-rail sanity.
- On-board 3.3 V rails are present and within expected range.
- SDA/SCL pull-ups are at approximately 3.3 V, not 5 V.
- It is safe to proceed to I2C sanity testing next.
- Still not clear for resistor capture, G-DC3, liquid testing, or Module B rework.
