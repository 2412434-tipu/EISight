// EISight v4.0c firmware — runtime session state.
//
// Reads the post-'m' line from Serial, applies the strict
// validator (A-Z, a-z, 0-9, '-', '_' only), and commits
// the module_id buffer that the rest of the firmware
// consumes via session::id(). Validation failures emit a
// single jsonl::write_error and never write_module_id_set
// — the laptop pipeline treats module_id_set as
// authoritative for relabeling, so a false positive here
// would corrupt downstream grouping.

#include "session.h"

#include <Arduino.h>
#include <string.h>

#include "jsonl.h"

namespace eisight {
namespace session {

namespace {

// Whole-line read budget from the moment the leading 'm'
// byte was consumed by main.cpp. 2 s is generous enough
// for an operator typing the full id by hand and short
// enough that an abandoned 'm' does not stall mode
// dispatch indefinitely.
constexpr uint32_t kLineTimeoutMs   = 2000;

// Overflow drain: keep reading after the line terminator
// until the next newline OR this much silence has elapsed,
// whichever comes first. Prevents pasted multi-line junk
// from being mis-dispatched on the next loop tick.
constexpr uint32_t kDrainSilenceMs  = 1000;

// On a CR, briefly look for the paired LF of a CRLF
// terminator and consume it if it arrives. A non-LF byte
// in this window belongs to the next command and must be
// left untouched.
constexpr uint32_t kCrLfPairWaitMs  = 20;

char g_module_id[kModuleIdMax + 1];
bool g_set = false;

// Restricted character class. A-Z, a-z, 0-9, '-', '_'.
// Anything outside this set is rejected so the laptop
// pipeline can use module_id as a CSV value, filename
// segment, and filesystem path component without
// escaping anywhere downstream.
bool is_valid_id_char(char c) {
  return (c >= 'A' && c <= 'Z') ||
         (c >= 'a' && c <= 'z') ||
         (c >= '0' && c <= '9') ||
         c == '-' || c == '_';
}

bool is_ws(char c) { return c == ' ' || c == '\t'; }

// Drain remaining bytes up to (and consuming) the next
// newline OR until kDrainSilenceMs of silence, whichever
// comes first. Used on the overflow path so the next
// command poll does not see leftover garbage.
void drain_to_newline() {
  uint32_t last_byte_ms = millis();
  while ((millis() - last_byte_ms) < kDrainSilenceMs) {
    if (Serial.available() <= 0) {
      delay(1);
      continue;
    }
    int ci = Serial.read();
    if (ci < 0) continue;
    last_byte_ms = millis();
    if (ci == '\n' || ci == '\r') return;
  }
}

}  // namespace

void begin() {
  g_module_id[0] = '\0';
  g_set = false;
}

bool is_set() { return g_set; }

const char* id() { return g_set ? g_module_id : nullptr; }

void handle_set_command() {
  char buf[kModuleIdMax];
  size_t len           = 0;
  bool   overflow      = false;
  bool   seen_non_ws   = false;
  bool   got_terminator = false;

  const uint32_t start_ms = millis();

  while ((millis() - start_ms) < kLineTimeoutMs) {
    int ci = Serial.read();
    if (ci < 0) { delay(1); continue; }
    char c = (char)ci;

    if (c == '\r' || c == '\n') {
      // Accept CR, LF, or CRLF. After a CR, peek briefly
      // for a paired LF; consume only if it shows up.
      if (c == '\r') {
        const uint32_t pair_start = millis();
        while ((millis() - pair_start) < kCrLfPairWaitMs) {
          int p = Serial.peek();
          if (p == '\n') { Serial.read(); break; }
          if (p >= 0)    { break; }
          delay(1);
        }
      }
      got_terminator = true;
      break;
    }

    // Strip leading whitespace before the first id byte.
    if (!seen_non_ws) {
      if (is_ws(c)) continue;
      seen_non_ws = true;
    }

    if (len >= kModuleIdMax) {
      // Mark overflow but keep consuming until terminator
      // so we still know where the line ends. buf is not
      // grown past kModuleIdMax.
      overflow = true;
      continue;
    }
    buf[len++] = c;
  }

  if (!got_terminator) {
    // Timeout: do not drain. A partial in-flight line
    // belongs to the operator's next attempt and the
    // main-loop poller can keep parsing from wherever
    // they finish typing.
    jsonl::write_error("module_id set timed out");
    return;
  }

  if (overflow) {
    drain_to_newline();
    jsonl::write_error(
      "module_id invalid: too long (max 32)");
    return;
  }

  // Trim trailing whitespace before validation.
  while (len > 0 && is_ws(buf[len - 1])) len--;

  if (len == 0) {
    jsonl::write_error("module_id invalid: empty");
    return;
  }

  for (size_t i = 0; i < len; i++) {
    if (!is_valid_id_char(buf[i])) {
      jsonl::write_error(
        "module_id invalid: contains forbidden character");
      return;
    }
  }

  // len ≤ kModuleIdMax here (overflow returned earlier),
  // so the NUL fits in g_module_id[kModuleIdMax + 1].
  memcpy(g_module_id, buf, len);
  g_module_id[len] = '\0';
  g_set = true;

  jsonl::write_module_id_set(g_module_id);
}

}  // namespace session
}  // namespace eisight
