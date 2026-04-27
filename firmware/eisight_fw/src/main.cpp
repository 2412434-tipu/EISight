// EISight v4.0c firmware — Arduino entry point.
//
// Boot sequence is fixed and minimal: Serial first so any
// later failure can emit JSONL, then the §I.2.a parse
// self-tests before ad5933::begin() is allowed to touch the
// bus, then the peripheral begins. Any self-test failure
// halts the boot with an LED-blink loop. ad5933 / ds18b20
// begin failures are reported and the boot continues so the
// operator can still run mode 's' (i2c scan) for diagnosis,
// and DS18B20-less bench setups still come up.
//
// hello is emitted last, after any begin-time error
// packets, so the laptop pipeline sees the failure context
// before the boot greeting. module_id is null in hello
// until the operator runs 'm <id>' — see session.h.
//
// loop() is a polled single-byte serial dispatcher. Mode
// handlers own their JSONL output and all peripheral I/O
// for the duration of a mode; main.cpp holds no state.

#include <Arduino.h>

#include "ad5933.h"
#include "config.h"
#include "ds18b20.h"
#include "jsonl.h"
#include "modes.h"
#include "self_test.h"
#include "session.h"

void setup() {
    Serial.begin(eisight::kSerialBaud);

    // Bounded wait for native-USB hosts to enumerate. On
    // the current esp32dev UART-bridge target !Serial is
    // false immediately and this falls through; on future
    // ESP32-S3/C3 native-USB targets it gives the host up
    // to 200 ms to attach so the hello packet is not lost.
    // A missing host must not stop the device from booting.
    const unsigned long t_serial_wait_start = millis();
    while (!Serial && millis() - t_serial_wait_start < 200) {
    }

    pinMode(eisight::kPinStatusLed, OUTPUT);
    digitalWrite(eisight::kPinStatusLed, LOW);

    eisight::session::begin();

    // §I.2.a parse self-tests must pass before ad5933 is
    // allowed on the bus. On failure self_test has already
    // emitted exactly one jsonl::write_self_test_fail; we
    // halt here with a slow LED blink so the failure is
    // visible at the bench.
    if (!eisight::self_test::run_int16_parse()) {
        while (true) {
            digitalWrite(eisight::kPinStatusLed, HIGH);
            delay(250);
            digitalWrite(eisight::kPinStatusLed, LOW);
            delay(250);
        }
    }
    if (!eisight::self_test::run_temp14_parse()) {
        while (true) {
            digitalWrite(eisight::kPinStatusLed, HIGH);
            delay(250);
            digitalWrite(eisight::kPinStatusLed, LOW);
            delay(250);
        }
    }

    if (!eisight::ad5933::begin()) {
        eisight::jsonl::write_error("ad5933 begin failed");
    }
    if (!eisight::ds18b20::begin()) {
        eisight::jsonl::write_error("ds18b20 begin failed");
    }

    eisight::jsonl::write_hello(eisight::session::id());
}

void loop() {
    if (Serial.available()) {
        const char c = (char)Serial.read();
        eisight::modes::run(c);
    } else {
        // delay(1) on Arduino-ESP32 calls vTaskDelay()
        // internally and yields to IDLE, feeding the task
        // watchdog and letting Wi-Fi/BT housekeeping run
        // even though we don't use those stacks.
        delay(1);
    }
}
