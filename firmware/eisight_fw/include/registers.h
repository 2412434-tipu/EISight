// EISight v4.0c firmware — AD5933 register addresses and
// bit-field codes.
//
// All values come from the AD5933 datasheet (rev D) and
// UG-364. This header is intentionally just addresses and
// constants — no logic — so the silicon's wire-level layout
// is auditable in one place.

#pragma once

#include <stdint.h>

namespace eisight {
namespace ad5933 {

// =========================================================
// Register addresses (datasheet Table 8)
// =========================================================

// Control register (16-bit, two 8-bit halves)
constexpr uint8_t kRegCtrlMsb         = 0x80; // command + range + PGA
constexpr uint8_t kRegCtrlLsb         = 0x81; // reset + clock-source

// Start frequency (24-bit, big-endian)
constexpr uint8_t kRegStartFreqHi     = 0x82;
constexpr uint8_t kRegStartFreqMid    = 0x83;
constexpr uint8_t kRegStartFreqLo     = 0x84;

// Frequency increment (24-bit, big-endian)
constexpr uint8_t kRegFreqIncHi       = 0x85;
constexpr uint8_t kRegFreqIncMid      = 0x86;
constexpr uint8_t kRegFreqIncLo       = 0x87;

// Number of increments (9-bit, packed into two bytes; the
// register holds num_increments = num_points - 1)
constexpr uint8_t kRegNumIncMsb       = 0x88;
constexpr uint8_t kRegNumIncLsb       = 0x89;

// Number of settling-time cycles (9-bit + 2-bit multiplier
// in MSB nibble)
constexpr uint8_t kRegSettlingMsb     = 0x8A;
constexpr uint8_t kRegSettlingLsb     = 0x8B;

// Status register (read-only)
constexpr uint8_t kRegStatus          = 0x8F;

// Internal-temperature result (14-bit signed in 16-bit
// envelope; D13 is sign, D15..D14 are don't-cares)
constexpr uint8_t kRegTempMsb         = 0x92;
constexpr uint8_t kRegTempLsb         = 0x93;

// DFT real result (signed int16, big-endian)
constexpr uint8_t kRegRealMsb         = 0x94;
constexpr uint8_t kRegRealLsb         = 0x95;

// DFT imaginary result (signed int16, big-endian)
constexpr uint8_t kRegImagMsb         = 0x96;
constexpr uint8_t kRegImagLsb         = 0x97;

// =========================================================
// Control register MSB (0x80) — command nibble (D15..D12)
// =========================================================
//
// Layout of 0x80:
//   D15..D12  command code
//   D11       reserved (0)
//   D10..D9   excitation range (encoded per Table 10 below)
//   D8        PGA gain (0 = ×5, 1 = ×1)
//
// These constants are the byte value to OR into the byte
// written *to register 0x80*. They are not bus-level
// command bytes (see kBusCmd* below).
constexpr uint8_t kCmdNoOp            = 0x00; // D15..D12 = 0000
constexpr uint8_t kCmdInitWithStart   = 0x10; // D15..D12 = 0001
constexpr uint8_t kCmdStartSweep      = 0x20; // D15..D12 = 0010
constexpr uint8_t kCmdIncrementFreq   = 0x30; // D15..D12 = 0011
constexpr uint8_t kCmdRepeatFreq      = 0x40; // D15..D12 = 0100
constexpr uint8_t kCmdMeasureTemp     = 0x90; // D15..D12 = 1001
constexpr uint8_t kCmdPowerDown       = 0xA0; // D15..D12 = 1010
constexpr uint8_t kCmdStandby         = 0xB0; // D15..D12 = 1011

// =========================================================
// I2C bus-level command bytes (datasheet p. 18)
// =========================================================
//
// Sent as the first data byte of an I2C transaction, NOT
// written into any AD5933 register. The address-pointer
// trick: write kBusCmdAddressPointer then the target
// register address, then issue a repeated-start read.
//
// Note: kBusCmdBlockWrite and kBusCmdAddressPointer
// numerically equal kCmdPowerDown and kCmdStandby above.
// They are different constants because they appear in
// different positions on the wire (transaction-start byte
// vs register-data byte). The naming prefix kBusCmd* vs
// kCmd* keeps call sites unambiguous.
constexpr uint8_t kBusCmdBlockWrite      = 0xA0;
constexpr uint8_t kBusCmdBlockRead       = 0xA1;
constexpr uint8_t kBusCmdAddressPointer  = 0xB0;

// =========================================================
// Excitation range (D10..D9 of 0x80)
// =========================================================
// Datasheet Table 10:
//   00 = Range 1 (1.98 Vpp)
//   01 = Range 3 (0.383 Vpp)
//   10 = Range 2 (0.97 Vpp)
//   11 = Range 4 (0.198 Vpp)  ← v4.0c default for milk
constexpr uint8_t kRangeBitsRange1    = 0x00;
constexpr uint8_t kRangeBitsRange3    = 0x01;
constexpr uint8_t kRangeBitsRange2    = 0x02;
constexpr uint8_t kRangeBitsRange4    = 0x03;

// =========================================================
// PGA gain (D8 of 0x80)
// =========================================================
constexpr uint8_t kPgaBitX5           = 0x00;
constexpr uint8_t kPgaBitX1           = 0x01;

// =========================================================
// Control register LSB (0x81)
// =========================================================
//   D4   reset bit (1 = reset state machine; sweep aborts;
//                   register contents preserved)
//   D3   clock source (0 = internal MCLK, 1 = external)
constexpr uint8_t kCtrlLsbReset       = 0x10;
constexpr uint8_t kCtrlLsbExtClock    = 0x08;
constexpr uint8_t kCtrlLsbIntClock    = 0x00;

// =========================================================
// Status register (0x8F)
// =========================================================
//   D0   valid temperature measurement
//   D1   valid real/imaginary data ("data ready")
//   D2   frequency-sweep complete
//   D3..D7  reserved (read 0)
constexpr uint8_t kStatusValidTemp    = 0x01;
constexpr uint8_t kStatusDataReady    = 0x02;
constexpr uint8_t kStatusSweepDone    = 0x04;

// =========================================================
// Settling-cycles MSB multiplier field (0x8A, D10..D9)
// =========================================================
// We always use ×1 in v4.0c (multiplier of 2 or 4 only
// becomes useful once settling-cycle count exceeds 511).
constexpr uint8_t kSettlingMultX1     = 0x00;
constexpr uint8_t kSettlingMultX2     = 0x02;
constexpr uint8_t kSettlingMultX4     = 0x06;

}  // namespace ad5933
}  // namespace eisight
