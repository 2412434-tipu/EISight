# AD5933 unpowered inspection and jumper verification

Date: 2026-05-05
Gate: H0
Result: PASS

Hardware state:
- AD5933 modules were unpowered.
- No ESP32 connection.
- No USB power to AD5933.
- No SDA/SCL connection.
- No resistor load.
- No jumper rework performed.

Module labels:
- AD5933-A-DIRECT
- AD5933-B-DIRECT-PRE-REWORK

Expected Direct-mode jumper state:
- J1 open
- J2 closed
- J3 open
- J4 open
- J5 open
- J6 closed

Measured jumper state:

AD5933-A-DIRECT:
- J1: open; no reading on 20 MΩ scale, occasional >2 MΩ fluctuation
- J2: closed; 0.0 Ω
- J3: open; no reading on 20 MΩ scale, occasional >2 MΩ fluctuation
- J4: open; no reading on 20 MΩ scale, occasional >2 MΩ fluctuation
- J5: open; no reading on 20 MΩ scale, occasional >2 MΩ fluctuation
- J6: closed; 0.0 Ω

AD5933-B-DIRECT-PRE-REWORK:
- J1: open; no reading on 20 MΩ scale, occasional >2 MΩ fluctuation
- J2: closed; 0.0 Ω
- J3: open; no reading on 20 MΩ scale, occasional >2 MΩ fluctuation
- J4: open; no reading on 20 MΩ scale, occasional >2 MΩ fluctuation
- J5: open; no reading on 20 MΩ scale, occasional >2 MΩ fluctuation
- J6: closed; 0.0 Ω

Visual inspection:
- Modules photographed front/back.
- Labels applied.
- No obvious burn mark, loose terminal, broken connector, or severe solder bridge observed from submitted photos.

Interpretation:
- Both modules match vendor Direct-mode jumper configuration.
- Module A remains the Direct-mode control.
- Module B remains pre-rework and must not be modified until Direct-mode power/I2C/single-resistor sanity passes.
- Clear to proceed to AD5933 power-rail sanity, but not yet to resistor capture or liquid testing.
