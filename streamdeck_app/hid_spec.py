"""Elgato Stream Deck HID input report parser and validator.

Spec references:
  https://docs.elgato.com/streamdeck/hid/general/
  https://docs.elgato.com/streamdeck/hid/stream-deck-plus-xl/
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Input report header (general reference, offsets from start of report)
REPORT_ID_INPUT = 0x01
OFFSET_REPORT_ID = 0x00
OFFSET_COMMAND = 0x01
OFFSET_PAYLOAD_LEN = 0x02  # UINT16 little-endian
OFFSET_PAYLOAD = 0x04

CMD_KEY = 0x00
CMD_TOUCH = 0x02
CMD_DIAL = 0x03

# Stream Deck + XL (PID 0x00C6)
XL_VID = 0x0FD9
XL_PID = 0x00C6
XL_MODEL = "20GBD9901"
XL_KEY_COUNT = 36
XL_DIAL_COUNT = 6
XL_KEY_COLS = 9
XL_KEY_ROWS = 4
XL_KEY_PIXEL_WIDTH = 112
XL_KEY_PIXEL_HEIGHT = 112
XL_LCD_WIDTH = 1280
XL_LCD_HEIGHT = 800
XL_TOUCH_WIDTH = 1200
XL_TOUCH_HEIGHT = 100

# Elgato general reference: recommended HID READ poll period
SPEC_POLL_INTERVAL_MS = 50
SPEC_READ_POLL_HZ = 20  # 1000 / 50
SPEC_INPUT_REPORT_MAX_LEN = 512

FEATURE_UNIT_INFO_REPORT_ID = 0x08
FEATURE_SERIAL_REPORT_ID = 0x06
FEATURE_FIRMWARE_AP2_REPORT_ID = 0x05

KEY_STATE_RELEASED = 0x00
KEY_STATE_PRESSED = 0x01

TOUCH_TYPE_TAP = 0x01
TOUCH_TYPE_PRESS = 0x02
TOUCH_TYPE_FLICK = 0x03
TOUCH_PAYLOAD_LEN = {
    TOUCH_TYPE_TAP: 0x10,
    TOUCH_TYPE_PRESS: 0x0A,
    TOUCH_TYPE_FLICK: 0x0E,
}

DIAL_SUBTYPE_BTN = 0x00
DIAL_SUBTYPE_ROTATE = 0x01

EXPECTED_XL_UNIT_INFO = {
    "rows": XL_KEY_ROWS,
    "cols": XL_KEY_COLS,
    "key_width": XL_KEY_PIXEL_WIDTH,
    "key_height": XL_KEY_PIXEL_HEIGHT,
    "lcd_width": XL_LCD_WIDTH,
    "lcd_height": XL_LCD_HEIGHT,
}


@dataclass
class SpecViolation:
    code: str
    message: str


@dataclass
class ParsedInputReport:
    report_id: int
    command: int
    payload_len: int
    kind: str
    raw: bytes
    pressed_keys: list[int] = field(default_factory=list)
    key_states: list[bool] = field(default_factory=list)
    touch: dict[str, Any] | None = None
    dial_pressed: list[int] = field(default_factory=list)
    dial_rotate: list[tuple[int, int]] = field(default_factory=list)
    violations: list[SpecViolation] = field(default_factory=list)


def _dial_rotation(value: int) -> int:
    if value < 0x80:
        return value
    return -(0x100 - value)


def _uint16_le(data: bytes, offset: int) -> int:
    if offset + 1 >= len(data):
        return 0
    return data[offset] | (data[offset + 1] << 8)


def validate_report_header(raw: bytes) -> list[SpecViolation]:
    violations: list[SpecViolation] = []
    if len(raw) < OFFSET_PAYLOAD:
        violations.append(
            SpecViolation("SHORT_REPORT", f"report length {len(raw)} < {OFFSET_PAYLOAD}")
        )
        return violations
    if raw[OFFSET_REPORT_ID] != REPORT_ID_INPUT:
        violations.append(
            SpecViolation(
                "BAD_REPORT_ID",
                f"report_id=0x{raw[OFFSET_REPORT_ID]:02x} expected 0x{REPORT_ID_INPUT:02x}",
            )
        )
    return violations


def parse_input_report(
    raw: bytes,
    *,
    key_count: int = XL_KEY_COUNT,
    dial_count: int = XL_DIAL_COUNT,
) -> ParsedInputReport | None:
    """Parse an input report per Elgato HID general/+XL layout."""
    if not raw:
        return None

    violations = validate_report_header(raw)
    command = raw[OFFSET_COMMAND] if len(raw) > OFFSET_COMMAND else 0
    payload_len = _uint16_le(raw, OFFSET_PAYLOAD_LEN)

    parsed = ParsedInputReport(
        report_id=raw[OFFSET_REPORT_ID],
        command=command,
        payload_len=payload_len,
        kind="UNKNOWN",
        raw=raw,
        violations=violations,
    )

    if violations:
        return parsed

    payload = raw[OFFSET_PAYLOAD:]

    if command == CMD_KEY:
        parsed.kind = "KEY"
        if payload_len != key_count:
            parsed.violations.append(
                SpecViolation(
                    "KEY_LEN",
                    f"payload_len={payload_len} expected {key_count} key bytes",
                )
            )
        end = min(len(payload), key_count)
        states = list(payload[:end])
        for index, value in enumerate(states):
            if value not in (KEY_STATE_RELEASED, KEY_STATE_PRESSED):
                parsed.violations.append(
                    SpecViolation(
                        "KEY_BYTE",
                        f"key[{index}]=0x{value:02x} expected 0x00 or 0x01",
                    )
                )
        parsed.key_states = [bool(s) for s in states]
        if len(parsed.key_states) < key_count:
            parsed.key_states.extend([False] * (key_count - len(parsed.key_states)))
        parsed.pressed_keys = [
            i for i, down in enumerate(parsed.key_states[:key_count]) if down
        ]
        return parsed

    if command == CMD_TOUCH:
        parsed.kind = "TOUCH"
        if len(payload) < 1:
            parsed.violations.append(SpecViolation("TOUCH_SHORT", "missing content type"))
            return parsed
        touch_type = payload[0]
        names = {TOUCH_TYPE_TAP: "TAP", TOUCH_TYPE_PRESS: "PRESS", TOUCH_TYPE_FLICK: "FLICK"}
        expected_len = TOUCH_PAYLOAD_LEN.get(touch_type)
        if expected_len is not None and payload_len != expected_len:
            parsed.violations.append(
                SpecViolation(
                    "TOUCH_LEN",
                    f"type={names[touch_type]} payload_len={payload_len} "
                    f"expected 0x{expected_len:02x}",
                )
            )
        if touch_type not in names:
            parsed.violations.append(
                SpecViolation("TOUCH_TYPE", f"unknown touch type 0x{touch_type:02x}")
            )
        if len(payload) >= 5:
            x = _uint16_le(payload, 2)
            y = _uint16_le(payload, 4)
            touch = {"type": touch_type, "name": names.get(touch_type), "x": x, "y": y}
            if touch_type == TOUCH_TYPE_FLICK and len(payload) >= 10:
                touch["x_out"] = _uint16_le(payload, 6)
                touch["y_out"] = _uint16_le(payload, 8)
            parsed.touch = touch
        return parsed

    if command == CMD_DIAL:
        parsed.kind = "DIAL"
        if len(payload) < 1:
            parsed.violations.append(SpecViolation("DIAL_SHORT", "missing dial subtype"))
            return parsed
        dial_type = payload[0]
        expected_len = dial_count + 1
        if payload_len != expected_len:
            parsed.violations.append(
                SpecViolation(
                    "DIAL_LEN",
                    f"payload_len={payload_len} expected {expected_len} "
                    f"(subtype + {dial_count} encoders)",
                )
            )
        values = list(payload[1 : 1 + dial_count])
        if dial_type == DIAL_SUBTYPE_BTN:
            parsed.dial_pressed = [i for i, v in enumerate(values) if v]
        elif dial_type == DIAL_SUBTYPE_ROTATE:
            parsed.dial_rotate = [
                (i, _dial_rotation(v)) for i, v in enumerate(values) if v != 0
            ]
        else:
            parsed.violations.append(
                SpecViolation("DIAL_TYPE", f"unknown dial subtype 0x{dial_type:02x}")
            )
        return parsed

    parsed.violations.append(
        SpecViolation("UNKNOWN_CMD", f"command=0x{command:02x}")
    )
    return parsed


def driver_key_states_from_report(
    raw: bytes,
    *,
    key_count: int = XL_KEY_COUNT,
) -> list[bool] | None:
    """Mirror StreamDeckPlusXL._read_control_states KEY branch."""
    if len(raw) < OFFSET_PAYLOAD:
        return None
    states = raw[1:]
    if not states or states[0] != CMD_KEY:
        return None
    return [bool(s) for s in states[3 : 3 + key_count]]


def driver_touch_from_report(raw: bytes) -> dict[str, Any] | None:
    """Mirror StreamDeckPlusXL._read_control_states TOUCH branch."""
    if len(raw) < 9:
        return None
    states = raw[1:]
    if states[0] != CMD_TOUCH:
        return None
    touch_type = states[3]
    value: dict[str, Any] = {
        "type": touch_type,
        "x": _uint16_le(states, 5),
        "y": _uint16_le(states, 7),
    }
    if touch_type == TOUCH_TYPE_FLICK and len(states) >= 13:
        value["x_out"] = _uint16_le(states, 9)
        value["y_out"] = _uint16_le(states, 11)
    return value


def compare_key_states(
    spec_states: list[bool],
    driver_states: list[bool],
) -> list[SpecViolation]:
    violations: list[SpecViolation] = []
    limit = min(len(spec_states), len(driver_states))
    for index in range(limit):
        if spec_states[index] != driver_states[index]:
            violations.append(
                SpecViolation(
                    "DRIVER_MISMATCH",
                    f"key[{index}] spec={spec_states[index]} driver={driver_states[index]}",
                )
            )
    if len(spec_states) != len(driver_states):
        violations.append(
            SpecViolation(
                "DRIVER_MISMATCH",
                f"key state length spec={len(spec_states)} driver={len(driver_states)}",
            )
        )
    return violations


def compare_touch(
    spec_touch: dict[str, Any] | None,
    driver_touch: dict[str, Any] | None,
) -> list[SpecViolation]:
    if spec_touch is None and driver_touch is None:
        return []
    if spec_touch is None or driver_touch is None:
        return [
            SpecViolation(
                "DRIVER_MISMATCH",
                f"touch spec={spec_touch} driver={driver_touch}",
            )
        ]
    violations: list[SpecViolation] = []
    for field in ("type", "x", "y", "x_out", "y_out"):
        if field in spec_touch or field in driver_touch:
            if spec_touch.get(field) != driver_touch.get(field):
                violations.append(
                    SpecViolation(
                        "DRIVER_MISMATCH",
                        f"touch.{field} spec={spec_touch.get(field)} "
                        f"driver={driver_touch.get(field)}",
                    )
                )
    return violations


def format_parsed_report(parsed: ParsedInputReport) -> str:
    if parsed.kind == "KEY":
        return (
            f"KEY cmd=0x{parsed.command:02x} len={parsed.payload_len} "
            f"pressed={parsed.pressed_keys}"
        )
    if parsed.kind == "TOUCH" and parsed.touch:
        touch = parsed.touch
        extra = ""
        if "x_out" in touch:
            extra = f" x_out={touch['x_out']} y_out={touch['y_out']}"
        return (
            f"TOUCH {touch.get('name', '?')} cmd=0x{parsed.command:02x} "
            f"len={parsed.payload_len} x={touch['x']} y={touch['y']}{extra}"
        )
    if parsed.kind == "DIAL":
        if parsed.dial_rotate:
            return (
                f"DIAL ROTATE cmd=0x{parsed.command:02x} len={parsed.payload_len} "
                f"active={parsed.dial_rotate}"
            )
        return (
            f"DIAL BTN cmd=0x{parsed.command:02x} len={parsed.payload_len} "
            f"pressed={parsed.dial_pressed}"
        )
    return f"{parsed.kind} cmd=0x{parsed.command:02x} len={parsed.payload_len}"


def format_violations(violations: list[SpecViolation]) -> str:
    return "; ".join(f"{v.code}: {v.message}" for v in violations)


def parse_unit_information(raw: bytes) -> dict[str, int]:
    """Parse feature report 0x08 response (general reference layout)."""
    if len(raw) < 17:
        return {}
    return {
        "rows": raw[1],
        "cols": raw[2],
        "key_width": _uint16_le(raw, 3),
        "key_height": _uint16_le(raw, 5),
        "lcd_width": _uint16_le(raw, 7),
        "lcd_height": _uint16_le(raw, 9),
    }


def validate_unit_information(
    info: dict[str, int],
    *,
    expected: dict[str, int] | None = None,
) -> list[SpecViolation]:
    violations: list[SpecViolation] = []
    if not info:
        violations.append(SpecViolation("UNIT_INFO", "empty unit information response"))
        return violations
    expected = expected or EXPECTED_XL_UNIT_INFO
    for key, want in expected.items():
        got = info.get(key)
        if got != want:
            violations.append(
                SpecViolation(
                    "UNIT_INFO",
                    f"{key}={got} expected {want}",
                )
            )
    return violations