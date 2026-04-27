// EISight v4.0c firmware — runtime session state.
//
// The module_id is set at runtime via the 'm' serial
// command, never at compile time. The first bench session
// uses Module A in its as-received Direct configuration
// (v4.0c §F.4 sanity gate), and only after that gate
// passes is Module B reworked into the Dual-AFE
// configuration (§F.5). A hard-coded module_id would
// mislabel the entire first session, so config.h
// deliberately omits one and points here.
//
// All non-'m' modes are gated on is_set(). Every record
// emitted by the firmware should be attributable to a
// labeled module so the laptop pipeline can group rows by
// module without guessing.

#pragma once

#include <stddef.h>
#include <stdint.h>

namespace eisight {
namespace session {

// Maximum module_id length, NUL-excluded. Above the v4.0c
// example "AD5933-B-DUAL-AFE" (17 chars) and below the
// jsonl::write_hello mid[48] buffer's quote+NUL budget.
constexpr size_t kModuleIdMax = 32;

// Clear internal state. Call once from setup() before
// jsonl::write_hello() so id() reliably returns nullptr
// on the boot greeting.
void begin();

// True iff a valid module_id has been registered via 'm'.
// Mode dispatch (see modes::run) checks this before any
// non-'m' command is allowed to run.
bool is_set();

// Pointer to the internal NUL-terminated module_id, or
// nullptr if unset. Pass directly into jsonl::* writers
// — write_hello and write_sweep_begin both treat nullptr
// as the JSON literal null via str_or_null().
const char* id();

// Dispatcher entry for the 'm' command. main.cpp calls
// this after it has already consumed the leading 'm'
// byte from Serial. Reads the rest of the line, applies
// the strict A-Z/a-z/0-9/'-'/'_' validator, stores it,
// and emits exactly one packet:
//   success  : jsonl::write_module_id_set(id())
//   failure  : jsonl::write_error("module_id ...")
// Never both. Calling 'm' a second time after a
// successful first set is allowed; on success the new
// value replaces the old (operators may swap modules
// mid-session, and the laptop pipeline is responsible
// for handling the change).
void handle_set_command();

}  // namespace session
}  // namespace eisight
