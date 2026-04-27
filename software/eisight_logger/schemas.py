"""schemas.py -- Pydantic v2 models for the EISight v4.0c JSONL packets.

Implements v4.0c blueprint sections:
  - I.4  Serial packet format (the on-the-wire JSONL grammar).
  - I.2.a Signed register parsing -- the laptop pipeline must
          range-validate AD5933 real/imag DFT values to the
          int16 interval [-32768, +32767]. Anything outside
          that range is a sign of unsigned-parse drift in
          firmware and must be refused at ingest, not silently
          coerced.

Wire-format source of truth is firmware/eisight_fw/src/jsonl.cpp.
Each model below mirrors one snprintf template in that file --
field order, decimal precisions, and the empty-string-vs-null
conventions track the firmware writers character-for-character.
If a firmware writer changes, the matching model here MUST move
in lockstep, otherwise the pipeline will silently drop or accept
malformed packets.

Model -> firmware writer cross-reference (jsonl.cpp):
   HelloRecord         <-  write_hello
   ModuleIdSetRecord   <-  write_module_id_set
   SweepBeginRecord    <-  write_sweep_begin
   DataRecord          <-  write_data
   SweepEndRecord      <-  write_sweep_end
   ErrorRecord         <-  write_error
   SelfTestFailRecord  <-  write_self_test_fail
   I2cScanRecord       <-  write_scan_result        (type "i2c_scan")
   RegSanityRecord     <-  write_reg_sanity_iter
   TempOnlyRecord      <-  write_temp_only
"""

from __future__ import annotations

import re
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

# §I.2.a: signed-int16 range guard for the AD5933 0x94/0x95 (real)
# and 0x96/0x97 (imag) DFT result registers.
INT16_MIN = -32768
INT16_MAX = 32767

# Mirrors the firmware command validator in
# session::handle_set_command: A-Z, a-z, 0-9, '-', '_'; 1..32 chars.
_MODULE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# write_scan_result formats addresses as "0xNN" with %02X (upper hex).
_I2C_ADDR_RE = re.compile(r"^0x[0-9A-F]{2}$")


class _StrictBase(BaseModel):
    # extra="forbid" catches firmware adding a new field without
    # us updating the schema -- a silent contract drift would
    # otherwise pass validation and corrupt downstream stages.
    model_config = ConfigDict(extra="forbid")


def _check_module_id_optional(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    if not _MODULE_ID_RE.match(v):
        raise ValueError(
            f"module_id {v!r} must be 1..32 chars of [A-Za-z0-9_-]"
        )
    return v


class HelloRecord(_StrictBase):
    type: Literal["hello"]
    fw: str
    module_id: Optional[str]

    _check_module_id = field_validator("module_id")(_check_module_id_optional)


class ModuleIdSetRecord(_StrictBase):
    type: Literal["module_id_set"]
    module_id: str

    @field_validator("module_id")
    @classmethod
    def _validate_module_id(cls, v: str) -> str:
        if not _MODULE_ID_RE.match(v):
            raise ValueError(
                f"module_id {v!r} must be 1..32 chars of [A-Za-z0-9_-]"
            )
        return v


RangeStr = Literal["RANGE_1", "RANGE_2", "RANGE_3", "RANGE_4"]
PgaStr = Literal["X1", "X5"]


class SweepBeginRecord(_StrictBase):
    type: Literal["sweep_begin"]
    # Firmware emits these as "" until the laptop annotates them on
    # ingest -- empty string is valid, None is not (the snprintf
    # template emits %s for these slots, never null).
    session_id: str
    sweep_id: str
    module_id: Optional[str]  # null until 'm <id>' has succeeded
    cell_id: str
    row_type: str
    load_id: str
    start_hz: int = Field(ge=0)
    stop_hz: int = Field(ge=0)
    points: int = Field(ge=1)
    range: RangeStr
    pga: PgaStr
    settling_cycles: int = Field(ge=0)
    ds18b20_pre_c: Optional[float]  # null on probe read failure
    ad5933_pre_c: Optional[float]

    _check_module_id = field_validator("module_id")(_check_module_id_optional)


class DataRecord(_StrictBase):
    type: Literal["data"]
    sweep_id: str
    idx: int = Field(ge=0)
    frequency_hz: float = Field(ge=0.0)
    # §I.2.a: refuse any record whose real/imag fall outside int16.
    real: int = Field(ge=INT16_MIN, le=INT16_MAX)
    imag: int = Field(ge=INT16_MIN, le=INT16_MAX)
    status: int = Field(ge=0, le=255)


class SweepEndRecord(_StrictBase):
    type: Literal["sweep_end"]
    sweep_id: str
    ds18b20_post_c: Optional[float]
    ad5933_post_c: Optional[float]
    elapsed_ms: int = Field(ge=0)
    error: Optional[str]  # null on a clean sweep, string on failure


class ErrorRecord(_StrictBase):
    type: Literal["error"]
    detail: str


class SelfTestFailRecord(_StrictBase):
    type: Literal["self_test_fail"]
    detail: str


class I2cScanRecord(_StrictBase):
    type: Literal["i2c_scan"]
    module_id: Optional[str]
    addrs: List[str]

    _check_module_id = field_validator("module_id")(_check_module_id_optional)

    @field_validator("addrs")
    @classmethod
    def _validate_addrs(cls, v: List[str]) -> List[str]:
        for a in v:
            if not _I2C_ADDR_RE.match(a):
                raise ValueError(
                    f"i2c_scan addr {a!r} must match the firmware "
                    f"'0xNN' upper-hex format (see write_scan_result)"
                )
        return v


class RegSanityRecord(_StrictBase):
    type: Literal["reg_sanity"]
    iter: int = Field(ge=0)
    status: int = Field(ge=0, le=255)
    ad5933_c: Optional[float]


class TempOnlyRecord(_StrictBase):
    type: Literal["temp_only"]
    ds18b20_c: Optional[float]
    ad5933_c: Optional[float]


JsonlRecord = Annotated[
    Union[
        HelloRecord,
        ModuleIdSetRecord,
        SweepBeginRecord,
        DataRecord,
        SweepEndRecord,
        ErrorRecord,
        SelfTestFailRecord,
        I2cScanRecord,
        RegSanityRecord,
        TempOnlyRecord,
    ],
    Field(discriminator="type"),
]


JSONL_ADAPTER: TypeAdapter[JsonlRecord] = TypeAdapter(JsonlRecord)


def parse_line(line: str) -> JsonlRecord:
    """Validate one JSONL line. Raises pydantic.ValidationError on failure.

    The firmware emits exactly one JSON object per line with no
    embedded newlines (jsonl.cpp uses Serial.println), so callers
    should split the stream on '\\n' and pass each non-empty line
    to this function unchanged.
    """
    return JSONL_ADAPTER.validate_json(line)
