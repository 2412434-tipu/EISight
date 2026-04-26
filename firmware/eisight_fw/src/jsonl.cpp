// EISight v4.0c firmware — JSONL packet writers.

#include "jsonl.h"

#include <Arduino.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "config.h"

namespace eisight {
namespace jsonl {

namespace {

// Format a float as a JSON number with `precision` decimals,
// or the literal token "null" if non-finite. Returns `tmp`
// or the static "null" string; safe to use directly inside
// a snprintf %s slot.
const char* float_or_null(float val, char* tmp, size_t tmpsz,
                          int precision) {
  if (isnan(val) || isinf(val)) return "null";
  snprintf(tmp, tmpsz, "%.*f", precision, (double)val);
  return tmp;
}

// Format a string as a JSON string literal ("…"), or "null"
// if `s` is nullptr. Caller passes a scratch buffer big
// enough to hold the value plus two quotes and a NUL.
const char* str_or_null(const char* s, char* tmp, size_t tmpsz) {
  if (s == nullptr) return "null";
  snprintf(tmp, tmpsz, "\"%s\"", s);
  return tmp;
}

}  // namespace

void write_hello(const char* module_id) {
  char buf[160];
  char mid[48];
  snprintf(buf, sizeof(buf),
    "{\"type\":\"hello\",\"fw\":\"%s\",\"module_id\":%s}",
    kFwVersion,
    str_or_null(module_id, mid, sizeof(mid)));
  Serial.println(buf);
}

void write_module_id_set(const char* module_id) {
  char buf[96];
  snprintf(buf, sizeof(buf),
    "{\"type\":\"module_id_set\",\"module_id\":\"%s\"}",
    module_id ? module_id : "");
  Serial.println(buf);
}

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
                       float       ad5933_pre_c) {
  char buf[512];
  char t1[16], t2[16];
  char mid[48];
  snprintf(buf, sizeof(buf),
    "{\"type\":\"sweep_begin\","
    "\"session_id\":\"%s\","
    "\"sweep_id\":\"%s\","
    "\"module_id\":%s,"
    "\"cell_id\":\"%s\","
    "\"row_type\":\"%s\","
    "\"load_id\":\"%s\","
    "\"start_hz\":%lu,\"stop_hz\":%lu,\"points\":%u,"
    "\"range\":\"%s\",\"pga\":\"%s\","
    "\"settling_cycles\":%u,"
    "\"ds18b20_pre_c\":%s,\"ad5933_pre_c\":%s}",
    session_id ? session_id : "",
    sweep_id   ? sweep_id   : "",
    str_or_null(module_id, mid, sizeof(mid)),
    cell_id    ? cell_id    : "",
    row_type   ? row_type   : "",
    load_id    ? load_id    : "",
    (unsigned long)start_hz,
    (unsigned long)stop_hz,
    (unsigned)points,
    range_str  ? range_str  : "",
    pga_str    ? pga_str    : "",
    (unsigned)settling_cycles,
    float_or_null(ds18b20_pre_c, t1, sizeof(t1), 4),
    float_or_null(ad5933_pre_c,  t2, sizeof(t2), 1));
  Serial.println(buf);
}

void write_data(const char* sweep_id,
                uint16_t    idx,
                double      frequency_hz,
                int16_t     real,
                int16_t     imag,
                uint8_t     status) {
  char buf[160];
  snprintf(buf, sizeof(buf),
    "{\"type\":\"data\",\"sweep_id\":\"%s\",\"idx\":%u,"
    "\"frequency_hz\":%.1f,"
    "\"real\":%d,\"imag\":%d,\"status\":%u}",
    sweep_id ? sweep_id : "",
    (unsigned)idx,
    frequency_hz,
    (int)real, (int)imag,
    (unsigned)status);
  Serial.println(buf);
}

void write_sweep_end(const char* sweep_id,
                     float       ds18b20_post_c,
                     float       ad5933_post_c,
                     uint32_t    elapsed_ms,
                     const char* error) {
  char buf[256];
  char t1[16], t2[16], err[96];
  snprintf(buf, sizeof(buf),
    "{\"type\":\"sweep_end\",\"sweep_id\":\"%s\","
    "\"ds18b20_post_c\":%s,\"ad5933_post_c\":%s,"
    "\"elapsed_ms\":%lu,\"error\":%s}",
    sweep_id ? sweep_id : "",
    float_or_null(ds18b20_post_c, t1, sizeof(t1), 4),
    float_or_null(ad5933_post_c,  t2, sizeof(t2), 1),
    (unsigned long)elapsed_ms,
    str_or_null(error, err, sizeof(err)));
  Serial.println(buf);
}

void write_error(const char* detail) {
  char buf[160];
  snprintf(buf, sizeof(buf),
    "{\"type\":\"error\",\"detail\":\"%s\"}",
    detail ? detail : "");
  Serial.println(buf);
}

void write_self_test_fail(const char* detail) {
  char buf[160];
  snprintf(buf, sizeof(buf),
    "{\"type\":\"self_test_fail\",\"detail\":\"%s\"}",
    detail ? detail : "");
  Serial.println(buf);
}

void write_scan_result(const char* module_id,
                       const uint8_t* addrs,
                       size_t count) {
  // Build the addrs array as "0xNN,0xNN,..." into a scratch
  // buffer first so the outer snprintf has a single %s slot.
  char addr_list[160];
  size_t pos = 0;
  addr_list[0] = '\0';
  for (size_t i = 0; i < count && pos + 8 < sizeof(addr_list); i++) {
    int n = snprintf(addr_list + pos, sizeof(addr_list) - pos,
                     "%s\"0x%02X\"",
                     (i == 0) ? "" : ",",
                     (unsigned)addrs[i]);
    if (n > 0) pos += (size_t)n;
  }

  char buf[256];
  char mid[48];
  snprintf(buf, sizeof(buf),
    "{\"type\":\"i2c_scan\",\"module_id\":%s,\"addrs\":[%s]}",
    str_or_null(module_id, mid, sizeof(mid)),
    addr_list);
  Serial.println(buf);
}

void write_reg_sanity_iter(uint16_t iter,
                           uint8_t  status,
                           float    ad5933_c) {
  char buf[128];
  char t1[16];
  snprintf(buf, sizeof(buf),
    "{\"type\":\"reg_sanity\",\"iter\":%u,"
    "\"status\":%u,\"ad5933_c\":%s}",
    (unsigned)iter,
    (unsigned)status,
    float_or_null(ad5933_c, t1, sizeof(t1), 2));
  Serial.println(buf);
}

void write_temp_only(float ds18b20_c, float ad5933_c) {
  char buf[128];
  char t1[16], t2[16];
  snprintf(buf, sizeof(buf),
    "{\"type\":\"temp_only\","
    "\"ds18b20_c\":%s,\"ad5933_c\":%s}",
    float_or_null(ds18b20_c, t1, sizeof(t1), 4),
    float_or_null(ad5933_c,  t2, sizeof(t2), 2));
  Serial.println(buf);
}

}  // namespace jsonl
}  // namespace eisight
