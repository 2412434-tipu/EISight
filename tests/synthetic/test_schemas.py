"""test_schemas.py -- schemas.JsonlRecord boundary and grammar tests.

Covers:
  - §I.2.a int16 range guard: -32768/+32767 accepted, one-step-
    out-of-range rejected on both ends, separately for real and
    for imag.
  - Required-field presence per record type (extra="forbid"
    catches firmware adding new fields; the dual is missing-
    required catches firmware dropping a field).
  - Empty-string-vs-null conventions on sweep_begin's annotation
    slots (cell_id / row_type / load_id) and on sweep_end.error.
  - i2c_scan addrs format ("0xNN" upper-hex per
    write_scan_result).
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from eisight_logger.schemas import parse_line


def _data_record(real: int, imag: int, status: int = 2) -> str:
    return json.dumps({
        "type": "data", "sweep_id": "SWP0000", "idx": 0,
        "frequency_hz": 10000.0, "real": real, "imag": imag,
        "status": status,
    })


def _sweep_begin(**overrides) -> str:
    base = {
        "type": "sweep_begin",
        "session_id": "S", "sweep_id": "SWP0000",
        "module_id": "AD5933-A-DIRECT", "cell_id": "", "row_type": "",
        "load_id": "", "start_hz": 5000, "stop_hz": 100000, "points": 96,
        "range": "RANGE_4", "pga": "X1", "settling_cycles": 15,
        "ds18b20_pre_c": 25.0, "ad5933_pre_c": 31.0,
    }
    base.update(overrides)
    return json.dumps(base)


def _sweep_end(**overrides) -> str:
    base = {
        "type": "sweep_end", "sweep_id": "SWP0000",
        "ds18b20_post_c": 25.0, "ad5933_post_c": 31.0,
        "elapsed_ms": 1820, "error": None,
    }
    base.update(overrides)
    return json.dumps(base)


# §I.2.a int16 boundary -- the most load-bearing schema rule.
@pytest.mark.parametrize("real,imag", [
    (-32768, 0), (32767, 0), (0, -32768), (0, 32767), (-32768, 32767),
])
def test_int16_endpoints_accepted(real, imag):
    rec = parse_line(_data_record(real, imag))
    assert rec.real == real and rec.imag == imag


@pytest.mark.parametrize("real,imag", [
    (-32769, 0), (32768, 0), (0, -32769), (0, 32768),
])
def test_int16_one_step_out_rejected(real, imag):
    with pytest.raises(ValidationError):
        parse_line(_data_record(real, imag))


def test_data_status_byte_range():
    parse_line(_data_record(0, 0, status=255))  # max byte
    with pytest.raises(ValidationError):
        parse_line(_data_record(0, 0, status=-1))
    with pytest.raises(ValidationError):
        parse_line(_data_record(0, 0, status=256))


# Required-field presence: dropping any required field fails.
def test_data_missing_required_field_rejected():
    bad = json.dumps({
        "type": "data", "sweep_id": "SWP0000", "idx": 0,
        "frequency_hz": 10000.0, "real": 1234,
        # imag missing
        "status": 2,
    })
    with pytest.raises(ValidationError):
        parse_line(bad)


def test_extra_field_rejected_per_strict_base():
    # extra="forbid" in _StrictBase: silent firmware drift surfaces
    # as a refused record, not a corrupted downstream stage.
    bad = json.dumps({
        "type": "hello", "fw": "x", "module_id": None,
        "unexpected_field": "drift",
    })
    with pytest.raises(ValidationError):
        parse_line(bad)


# sweep_begin: empty strings are valid for annotation slots; null
# is not (the firmware's snprintf %s never emits null for these).
def test_sweep_begin_empty_strings_accepted():
    rec = parse_line(_sweep_begin(cell_id="", row_type="", load_id=""))
    assert rec.cell_id == "" and rec.row_type == "" and rec.load_id == ""


def test_sweep_begin_null_string_field_rejected():
    with pytest.raises(ValidationError):
        parse_line(_sweep_begin(cell_id=None))


def test_sweep_begin_module_id_null_accepted():
    # module_id can be null until 'm <id>' has succeeded.
    rec = parse_line(_sweep_begin(module_id=None))
    assert rec.module_id is None


def test_sweep_begin_temp_null_accepted():
    rec = parse_line(_sweep_begin(ds18b20_pre_c=None))
    assert rec.ds18b20_pre_c is None


# sweep_end.error: null on clean sweep, string on failure.
def test_sweep_end_error_null_accepted():
    rec = parse_line(_sweep_end(error=None))
    assert rec.error is None


def test_sweep_end_error_string_accepted():
    rec = parse_line(_sweep_end(error="watchdog timeout"))
    assert rec.error == "watchdog timeout"


# i2c_scan addrs: "0xNN" upper-hex per write_scan_result.
@pytest.mark.parametrize("addr", ["0x0D", "0x28", "0xFF"])
def test_i2c_scan_valid_addr_accepted(addr):
    rec = parse_line(json.dumps({
        "type": "i2c_scan", "module_id": "M", "addrs": [addr],
    }))
    assert rec.addrs == [addr]


@pytest.mark.parametrize("addr", [
    "0x0d",      # lowercase hex
    "13",        # decimal, not hex
    "0X0D",      # uppercase X
    "0x0",       # one digit
    "0x00D",     # three digits
])
def test_i2c_scan_malformed_addr_rejected(addr):
    bad = json.dumps({
        "type": "i2c_scan", "module_id": "M", "addrs": [addr],
    })
    with pytest.raises(ValidationError):
        parse_line(bad)


# module_id grammar mirrors session::handle_set_command.
@pytest.mark.parametrize("mid", ["A", "AD5933-A-DIRECT", "x" * 32])
def test_module_id_set_valid(mid):
    rec = parse_line(json.dumps({"type": "module_id_set", "module_id": mid}))
    assert rec.module_id == mid


@pytest.mark.parametrize("mid", ["", "x" * 33, "bad space", "bad/slash"])
def test_module_id_set_invalid(mid):
    with pytest.raises(ValidationError):
        parse_line(json.dumps({"type": "module_id_set", "module_id": mid}))
