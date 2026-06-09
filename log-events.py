#!/usr/bin/env python3
"""Elgato HID spec validation test for Stream Deck + XL.

Parses every input report with streamdeck_app.hid_spec (aligned to
https://docs.elgato.com/streamdeck/hid/general/ and
https://docs.elgato.com/streamdeck/hid/stream-deck-plus-xl/),
compares against the XL driver parser, and logs violations.

Two reader modes:
  xl-driver  — StreamDeckPlusXL._read + hid_read_timeout (controller path)
  library    — python-elgato-streamdeck default _read + read_poll_hz=20 (50ms)
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from PIL import ImageDraw

import streamdeck_app.patch_streamdeck  # noqa: F401
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper

from streamdeck_app.hid_spec import (
    FEATURE_UNIT_INFO_REPORT_ID,
    SPEC_POLL_INTERVAL_MS,
    SPEC_READ_POLL_HZ,
    SpecViolation,
    XL_DIAL_COUNT,
    XL_KEY_COUNT,
    XL_KEY_ROWS,
    XL_KEY_COLS,
    compare_key_states,
    compare_touch,
    driver_key_states_from_report,
    driver_touch_from_report,
    format_parsed_report,
    parse_input_report,
    parse_unit_information,
    validate_unit_information,
)

DEFAULT_LOG = Path.home() / ".openpnp2" / "log" / "streamdeck-events.log"
SPEC_URL_GENERAL = "https://docs.elgato.com/streamdeck/hid/general/"
SPEC_URL_XL = "https://docs.elgato.com/streamdeck/hid/stream-deck-plus-xl/"


def _blank_key_native(deck) -> bytes:
    return PILHelper.to_native_key_format(
        deck, PILHelper.create_key_image(deck, background="#161a22")
    )


def _flash_key_native(deck, key: int) -> bytes:
    image = PILHelper.create_key_image(deck, background="#2d6cdf")
    draw = ImageDraw.Draw(image)
    draw.text((36, 44), str(key), fill="white")
    return PILHelper.to_native_key_format(deck, image)


def _show_listening_display(deck) -> None:
    blank = _blank_key_native(deck)
    banner = PILHelper.create_key_image(deck, background="#1a3d1a")
    draw = ImageDraw.Draw(banner)
    draw.text((14, 40), "LOG", fill="#9ae6b4")
    banner_native = PILHelper.to_native_key_format(deck, banner)
    with deck:
        for index in range(deck.key_count()):
            deck.set_key_image(index, banner_native if index == 0 else blank)


class SpecValidator:
    def __init__(
        self,
        log_path: Path | None,
        stdout: bool = True,
        *,
        on_key_edge=None,
        key_count: int = XL_KEY_COUNT,
        dial_count: int = XL_DIAL_COUNT,
    ) -> None:
        self._log_path = log_path
        self._stdout = stdout
        self._on_key_edge = on_key_edge
        self._key_count = key_count
        self._dial_count = dial_count
        self._log_file = None
        self._last_mono = time.monotonic()
        self._last_key_states = [False] * key_count
        self._last_dial_states = [False] * dial_count
        self._report_count = 0
        self._burst_count = 0
        self._violation_count = 0
        self._driver_mismatch_count = 0
        self._key_edge_count = 0
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = log_path.open("a", encoding="utf-8")

    @property
    def violation_count(self) -> int:
        return self._violation_count

    @property
    def driver_mismatch_count(self) -> int:
        return self._driver_mismatch_count

    @property
    def key_edge_count(self) -> int:
        return self._key_edge_count

    def close(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def _emit(self, message: str) -> None:
        now = time.time()
        mono = time.monotonic()
        delta_ms = (mono - self._last_mono) * 1000.0
        self._last_mono = mono
        line = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            + f".{int((now % 1) * 1000):03d} "
            + f"+{delta_ms:6.1f}ms "
            + message
        )
        if self._stdout:
            print(line, flush=True)
        if self._log_file is not None:
            self._log_file.write(line + "\n")
            self._log_file.flush()

    def log_session_start(self, deck, *, mode: str, poll_ms: int) -> None:
        self._emit(
            f"SESSION start mode={mode} poll_ms={poll_ms} "
            f"type={deck.deck_type()} keys={deck.key_count()} "
            f"dials={getattr(deck, 'DIAL_COUNT', '?')}"
        )
        self._emit(f"SPEC general={SPEC_URL_GENERAL}")
        self._emit(f"SPEC xl={SPEC_URL_XL}")

    def log_session_end(self) -> None:
        self._emit(
            f"SESSION end reports={self._report_count} "
            f"key_edges={self._key_edge_count} "
            f"violations={self._violation_count} "
            f"driver_mismatches={self._driver_mismatch_count}"
        )

    def log_violations(self, violations, *, prefix: str = "VIOLATION") -> None:
        if not violations:
            return
        self._violation_count += len(violations)
        for violation in violations:
            if violation.code == "DRIVER_MISMATCH":
                self._driver_mismatch_count += 1
            self._emit(f"{prefix} {violation.code}: {violation.message}")

    def process_raw(self, raw: bytes) -> None:
        self._report_count += 1
        self._burst_count += 1

        parsed = parse_input_report(
            raw,
            key_count=self._key_count,
            dial_count=self._dial_count,
        )
        if parsed is None:
            self._emit("RAW len=0 (empty)")
            return

        self._emit(f"RAW len={len(raw)} hex={raw.hex()}")
        self._emit(f"     SPEC {format_parsed_report(parsed)}")
        self.log_violations(parsed.violations, prefix="SPEC")

        if parsed.kind == "KEY":
            driver_states = driver_key_states_from_report(
                raw, key_count=self._key_count
            )
            if driver_states is not None:
                mismatches = compare_key_states(parsed.key_states, driver_states)
                self.log_violations(mismatches, prefix="DRIVER")
            for index, new in enumerate(parsed.key_states[: self._key_count]):
                old = self._last_key_states[index]
                if old == new:
                    continue
                self._last_key_states[index] = new
                self._key_edge_count += 1
                self._emit(f"EDGE key {index} {'DOWN' if new else 'UP'}")
                if self._on_key_edge is not None:
                    self._on_key_edge(index, new)

        elif parsed.kind == "TOUCH" and parsed.touch is not None:
            driver_touch = driver_touch_from_report(raw)
            mismatches = compare_touch(parsed.touch, driver_touch)
            self.log_violations(mismatches, prefix="DRIVER")

        elif parsed.kind == "DIAL":
            payload = raw[4:]
            if payload:
                dial_type = payload[0]
                values = list(payload[1 : 1 + self._dial_count])
                if dial_type == 0x00:
                    for index, new in enumerate(bool(v) for v in values):
                        old = self._last_dial_states[index]
                        if old == new:
                            continue
                        self._last_dial_states[index] = new
                        self._emit(f"EDGE dial {index} {'DOWN' if new else 'UP'}")

    def end_burst(self) -> None:
        if self._burst_count > 0:
            self._emit(f"BURST count={self._burst_count}")
            self._burst_count = 0

    def sync_baseline(self, key_states: list[bool]) -> None:
        self._last_key_states = list(key_states[: self._key_count])
        pressed = [i for i, down in enumerate(self._last_key_states) if down]
        self._emit(f"SYNC baseline pressed={pressed}")


def _controller_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-f", "streamdeck_app.controller"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _stop_controller() -> None:
    subprocess.run(
        ["pkill", "-f", "streamdeck_app.controller"],
        capture_output=True,
    )
    time.sleep(0.5)


def _read_unit_information(deck) -> dict[str, int]:
    try:
        raw = deck.device.read_feature(FEATURE_UNIT_INFO_REPORT_ID, 32)
    except Exception as exc:
        print(f"WARN: could not read unit information: {exc}", file=sys.stderr)
        return {}
    return parse_unit_information(raw)


def _validate_device(deck, validator: SpecValidator) -> bool:
    ok = True
    key_count = deck.key_count()
    if key_count != XL_KEY_COUNT:
        validator.log_violations(
            [
                SpecViolation(
                    "DEVICE",
                    f"key_count={key_count} expected {XL_KEY_COUNT} "
                    f"({XL_KEY_ROWS}x{XL_KEY_COLS})",
                )
            ]
        )
        ok = False

    unit_info = _read_unit_information(deck)
    if unit_info:
        validator._emit(
            "UNIT rows={rows} cols={cols} key={key_width}x{key_height} "
            "lcd={lcd_width}x{lcd_height}".format(**unit_info)
        )
        validator.log_violations(validate_unit_information(unit_info))
    else:
        validator.log_violations(
            [SpecViolation("UNIT_INFO", "feature report 0x08 unavailable")]
        )

    serial = ""
    firmware = ""
    try:
        serial = deck.get_serial_number()
    except Exception:
        pass
    try:
        firmware = deck.get_firmware_version()
    except Exception:
        pass
    if serial:
        validator._emit(f"DEVICE serial={serial}")
    if firmware:
        validator._emit(f"DEVICE firmware={firmware}")

    return ok


def _wrap_device_read(deck, validator: SpecValidator):
    original_read = deck.device.read

    def logged_device_read(length: int):
        raw = original_read(length)
        if raw is None:
            validator.end_burst()
        else:
            validator.process_raw(raw)
        return raw

    deck.device.read = logged_device_read
    return original_read


def _run_xl_driver_mode(deck, validator: SpecValidator, args) -> None:
    poll_ms = args.timeout_ms
    if hasattr(deck, "_INPUT_READ_TIMEOUT_MS"):
        deck._INPUT_READ_TIMEOUT_MS = poll_ms
    if hasattr(deck.device, "input_read_timeout_ms"):
        deck.device.input_read_timeout_ms = poll_ms

    validator._emit(
        f"READER mode=xl-driver path=deck._read timeout_ms={poll_ms} "
        f"(Elgato recommends {SPEC_POLL_INTERVAL_MS}ms)"
    )

    if hasattr(deck, "seed_input_baseline"):
        deck.seed_input_baseline(timeout_sec=args.seed_ms / 1000.0)
        latest = getattr(deck, "_latest_key_states", None)
        if latest:
            validator.sync_baseline(list(latest))

    deck._setup_reader(deck._read)


def _run_library_mode(deck, validator: SpecValidator, args) -> None:
    poll_hz = args.poll_hz
    deck.read_poll_hz = poll_hz
    poll_ms = int(1000 / poll_hz)

    if hasattr(deck.device, "input_read_timeout_ms"):
        deck.device.input_read_timeout_ms = 0

    validator._emit(
        f"READER mode=library path=StreamDeck._read poll_hz={poll_hz} "
        f"(~{poll_ms}ms, Elgato recommends {SPEC_POLL_INTERVAL_MS}ms)"
    )

    callback_edges: list[tuple[int, bool]] = []

    def on_key(_deck, key: int, state: bool) -> None:
        callback_edges.append((key, state))
        validator._emit(f"LIBCB key {key} {'DOWN' if state else 'UP'}")
        if validator._on_key_edge is not None:
            validator._on_key_edge(key, state)

    deck.set_key_callback(on_key)
    deck._setup_reader(deck._read)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Stream Deck + XL HID input against Elgato spec.",
        epilog=(
            "Examples:\n"
            "  ./log-events\n"
            "  ./log-events --mode library\n"
            "  ./log-events --no-display --strict\n"
            "  ./log-events --kill-controller\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=("xl-driver", "library"),
        default="xl-driver",
        help="input reader: xl-driver (controller path) or library (canonical poll)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG,
        help=f"log file path (default: {DEFAULT_LOG})",
    )
    parser.add_argument(
        "--stdout-only",
        action="store_true",
        help="do not write a log file",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="do not update key images (HID-only test)",
    )
    parser.add_argument(
        "--kill-controller",
        action="store_true",
        help="stop streamdeck_app.controller before opening the device",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=SPEC_POLL_INTERVAL_MS,
        help=f"xl-driver hid_read_timeout in ms (default: {SPEC_POLL_INTERVAL_MS})",
    )
    parser.add_argument(
        "--poll-hz",
        type=int,
        default=SPEC_READ_POLL_HZ,
        help=f"library mode poll rate (default: {SPEC_READ_POLL_HZ} = 50ms)",
    )
    parser.add_argument(
        "--seed-ms",
        type=int,
        default=500,
        help="xl-driver baseline sync window at startup in ms (default: 500)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if any spec or driver violation was logged",
    )
    args = parser.parse_args()

    if _controller_running():
        if args.kill_controller:
            print("Stopping streamdeck controller...", flush=True)
            _stop_controller()
            if _controller_running():
                print(
                    "ERROR: controller still running — stop ./run and retry",
                    file=sys.stderr,
                )
                return 1
        else:
            print(
                "ERROR: streamdeck controller is running and holds the device.\n"
                "  Stop it: pkill -f streamdeck_app.controller\n"
                "  Or rerun: ./log-events --kill-controller",
                file=sys.stderr,
            )
            return 1

    decks = DeviceManager().enumerate()
    if not decks:
        print("ERROR: no Stream Deck found", file=sys.stderr)
        return 1

    deck = decks[0]
    log_path = None if args.stdout_only else args.log_file
    blank_key = _blank_key_native(deck)

    def on_key_edge(key: int, down: bool) -> None:
        if args.no_display:
            return
        native = _flash_key_native(deck, key) if down else blank_key
        with deck:
            deck.set_key_image(key, native)

    validator = SpecValidator(
        log_path=log_path,
        on_key_edge=None if args.no_display else on_key_edge,
        key_count=deck.key_count(),
        dial_count=getattr(deck, "DIAL_COUNT", XL_DIAL_COUNT),
    )

    stop = False

    def on_signal(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    deck.open()
    deck._setup_reader(None)
    deck.reset()
    deck.set_brightness(60)
    if not args.no_display:
        _show_listening_display(deck)

    poll_ms = (
        args.timeout_ms if args.mode == "xl-driver" else int(1000 / args.poll_hz)
    )
    validator.log_session_start(deck, mode=args.mode, poll_ms=poll_ms)
    _validate_device(deck, validator)

    print(
        f"Connected: {deck.deck_type()} ({deck.key_count()} keys). "
        f"Mode={args.mode} poll~{poll_ms}ms.",
        flush=True,
    )
    if not args.no_display:
        print("Key 0 shows LOG. Pressed keys flash their index.", flush=True)
    print(
        "Press keys/dials to validate HID reports. Ctrl+C to quit.",
        flush=True,
    )
    if log_path is not None:
        print(f"Log file: {log_path}", flush=True)

    original_read = _wrap_device_read(deck, validator)

    try:
        if args.mode == "xl-driver":
            _run_xl_driver_mode(deck, validator, args)
        else:
            _run_library_mode(deck, validator, args)

        while deck.is_open() and not stop:
            time.sleep(0.05)
    finally:
        deck._setup_reader(None)
        deck.device.read = original_read
        validator.end_burst()
        validator.log_session_end()
        validator.close()
        deck.reset()
        deck.close()

    if args.strict and validator.violation_count > 0:
        print(
            f"FAIL: {validator.violation_count} violation(s) "
            f"({validator.driver_mismatch_count} driver mismatch)",
            file=sys.stderr,
        )
        return 1

    if validator.key_edge_count == 0:
        print(
            "WARN: no key edges detected — press a few keys to confirm input path",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())