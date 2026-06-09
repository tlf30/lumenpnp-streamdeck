# Stream Deck + XL support (PID 0x00C6)
# Adapted from StreamController/streamcontroller-python-elgato-streamdeck PR #11

import time
from pathlib import Path

from StreamDeck.Devices.StreamDeck import StreamDeck, ControlType, DialEventType, TouchscreenEventType
from StreamDeck.ImageHelpers import PILHelper
from StreamDeck.Transport.Transport import TransportError

_HID_LOG = Path.home() / ".openpnp2" / "log" / "streamdeck-hid.log"


def _hid_log(message: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " " + message + "\n"
    try:
        _HID_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _HID_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def _format_raw_report(raw: bytes | None) -> str:
    if raw is None:
        return "none"
    return raw.hex()


def _dials_rotation_transform(value):
    if value < 0x80:
        return value
    return -(0x100 - value)


class StreamDeckPlusXL(StreamDeck):
    KEY_COUNT = 36
    KEY_COLS = 9
    KEY_ROWS = 4

    DIAL_COUNT = 6

    KEY_PIXEL_WIDTH = 112
    KEY_PIXEL_HEIGHT = 112
    KEY_IMAGE_FORMAT = "JPEG"
    KEY_FLIP = (False, False)
    KEY_ROTATION = 90

    DECK_TYPE = "Stream Deck + XL"
    DECK_VISUAL = True
    DECK_TOUCH = True

    TOUCHSCREEN_PIXEL_HEIGHT = 100
    TOUCHSCREEN_PIXEL_WIDTH = 1200
    TOUCHSCREEN_IMAGE_FORMAT = "JPEG"
    TOUCHSCREEN_FLIP = (False, False)
    TOUCHSCREEN_ROTATION = 0

    _INPUT_REPORT_LENGTH = 64
    _INPUT_READ_TIMEOUT_MS = 100
    _IMG_PACKET_LEN = 1024
    _KEY_PACKET_HEADER = 8
    _LCD_PACKET_HEADER = 16
    _KEY_PACKET_PAYLOAD_LEN = _IMG_PACKET_LEN - _KEY_PACKET_HEADER
    _LCD_PACKET_PAYLOAD_LEN = _IMG_PACKET_LEN - _LCD_PACKET_HEADER

    _DIAL_EVENT_TRANSFORM = {
        DialEventType.TURN: _dials_rotation_transform,
        DialEventType.PUSH: bool,
    }

    def __init__(self, device):
        super().__init__(device)
        self._input_synced = False
        self._latest_key_states = [False] * self.KEY_COUNT
        self._pending_key_edges: list[tuple[int, bool]] = []
        self.BLANK_KEY_IMAGE = PILHelper.to_native_key_format(
            self, PILHelper.create_key_image(self, "black")
        )
        self.BLANK_TOUCHSCREEN_IMAGE = PILHelper.to_native_touchscreen_format(
            self, PILHelper.create_touchscreen_image(self, "black")
        )

    def _reset_key_stream(self):
        payload = bytearray(self._IMG_PACKET_LEN)
        payload[0] = 0x02
        self.device.write(payload)

    def reset(self):
        payload = bytearray(32)
        payload[0:2] = [0x03, 0x02]
        self.device.write_feature(payload)

    def _read_control_states(self):
        raw = self.device.read(self._INPUT_REPORT_LENGTH)
        if raw is None:
            return None

        states = raw[1:]
        payload_len = states[1] | (states[2] << 8) if len(states) >= 3 else 0

        if states[0] == 0x00:
            new_key_states = [bool(s) for s in states[3:3 + self.KEY_COUNT]]
            self._latest_key_states = list(new_key_states)
            pressed = [i for i, down in enumerate(new_key_states) if down]
            _hid_log(
                "KEY "
                f"raw={_format_raw_report(raw)} "
                f"cmd=0x00 len={payload_len} "
                f"pressed={pressed}"
            )
            return {ControlType.KEY: new_key_states}

        if states[0] == 0x02:
            if len(states) < 9:
                _hid_log(
                    "TOUCH short "
                    f"raw={_format_raw_report(raw)} "
                    f"cmd=0x02 len={payload_len}"
                )
                return None
            if states[3] == 1:
                event_type = TouchscreenEventType.SHORT
                event_name = "TAP"
            elif states[3] == 2:
                event_type = TouchscreenEventType.LONG
                event_name = "PRESS"
            elif states[3] == 3:
                event_type = TouchscreenEventType.DRAG
                event_name = "FLICK"
            else:
                _hid_log(
                    "TOUCH unknown "
                    f"raw={_format_raw_report(raw)} "
                    f"type=0x{states[3]:02x}"
                )
                return None

            value = {
                "x": (states[6] << 8) + states[5],
                "y": (states[8] << 8) + states[7],
            }

            if event_type == TouchscreenEventType.DRAG:
                value["x_out"] = (states[10] << 8) + states[9]
                value["y_out"] = (states[12] << 8) + states[11]
                _hid_log(
                    f"TOUCH {event_name} "
                    f"raw={_format_raw_report(raw)} "
                    f"x={value['x']} y={value['y']} "
                    f"x_out={value['x_out']} y_out={value['y_out']}"
                )
            else:
                _hid_log(
                    f"TOUCH {event_name} "
                    f"raw={_format_raw_report(raw)} "
                    f"x={value['x']} y={value['y']}"
                )

            return {ControlType.TOUCHSCREEN: (event_type, value)}

        if states[0] == 0x03:
            if len(states) < 5:
                _hid_log(
                    "DIAL short "
                    f"raw={_format_raw_report(raw)} "
                    f"cmd=0x03 len={payload_len}"
                )
                return None
            if states[3] == 0x01:
                event_type = DialEventType.TURN
                event_name = "ROTATE"
            elif states[3] == 0x00:
                event_type = DialEventType.PUSH
                event_name = "BTN"
            else:
                _hid_log(
                    "DIAL unknown "
                    f"raw={_format_raw_report(raw)} "
                    f"type=0x{states[3]:02x}"
                )
                return None

            values = [
                self._DIAL_EVENT_TRANSFORM[event_type](s)
                for s in states[4:4 + self.DIAL_COUNT]
            ]
            if event_type == DialEventType.PUSH:
                pressed = [i for i, down in enumerate(values) if down]
                _hid_log(
                    f"DIAL {event_name} "
                    f"raw={_format_raw_report(raw)} "
                    f"pressed={pressed}"
                )
            else:
                active = [(i, amount) for i, amount in enumerate(values) if amount]
                _hid_log(
                    f"DIAL {event_name} "
                    f"raw={_format_raw_report(raw)} "
                    f"active={active}"
                )
            return {ControlType.DIAL: {event_type: values}}

        _hid_log(
            "UNKNOWN "
            f"raw={_format_raw_report(raw)} "
            f"cmd=0x{states[0]:02x} len={payload_len}"
        )
        return None

    def _sync_key_states(self, key_states: list[bool]) -> None:
        self._latest_key_states = list(key_states)
        for index, new in enumerate(key_states):
            if index < len(self.last_key_states):
                self.last_key_states[index] = new

    def pop_key_edges(self) -> list[tuple[int, bool]]:
        edges = self._pending_key_edges
        self._pending_key_edges = []
        return edges

    def _queue_key_edge(self, key: int, down: bool) -> None:
        self._pending_key_edges.append((key, down))

    def _dispatch_control_states(self, control_states: dict) -> None:
        if ControlType.KEY in control_states:
            new_key_states = control_states[ControlType.KEY]
            self._latest_key_states = list(new_key_states)
            for key, new in enumerate(new_key_states):
                if key >= len(self.last_key_states):
                    break
                old = self.last_key_states[key]
                self.last_key_states[key] = new
                if old == new:
                    continue
                _hid_log(f"EDGE key {key} {'down' if new else 'up'}")
                self._queue_key_edge(key, new)
                if self.key_callback is not None:
                    self.key_callback(self, key, new)
            return

        if ControlType.DIAL in control_states:
            dial_states = control_states[ControlType.DIAL]
            if DialEventType.PUSH in dial_states:
                for dial_index, (old, new) in enumerate(
                    zip(self.last_dial_states, dial_states[DialEventType.PUSH])
                ):
                    if old == new:
                        continue
                    self.last_dial_states[dial_index] = new
                    _hid_log(f"EDGE dial {dial_index} {'down' if new else 'up'}")
                    if self.dial_callback is not None:
                        self.dial_callback(self, dial_index, DialEventType.PUSH, new)
            if DialEventType.TURN in dial_states:
                for dial_index, amount in enumerate(dial_states[DialEventType.TURN]):
                    if amount == 0:
                        continue
                    _hid_log(f"EDGE dial {dial_index} turn {amount:+d}")
                    if self.dial_callback is not None:
                        self.dial_callback(self, dial_index, DialEventType.TURN, amount)
            return

        if ControlType.TOUCHSCREEN in control_states:
            event_type, value = control_states[ControlType.TOUCHSCREEN]
            _hid_log(f"EDGE touch {event_type.name} {value}")
            if self.touchscreen_callback is not None:
                self.touchscreen_callback(self, event_type, value)

    def seed_input_baseline(self, timeout_sec: float = 0.5) -> None:
        """Drain pending key reports and sync the cache without firing callbacks."""
        self._input_synced = False
        deadline = time.time() + timeout_sec
        latest_keys: list[bool] | None = None
        while time.time() < deadline:
            control_states = self._read_control_states()
            if control_states is None:
                if latest_keys is not None:
                    break
                time.sleep(0.01)
                continue
            if ControlType.KEY in control_states:
                latest_keys = control_states[ControlType.KEY]
        if latest_keys is not None:
            self._sync_key_states(latest_keys)
            pressed = [i for i, down in enumerate(latest_keys) if down]
            _hid_log(f"SYNC baseline pressed={pressed}")
        else:
            self._sync_key_states(list(self._latest_key_states))
            _hid_log("SYNC baseline pressed=[] (no report)")
        self._input_synced = True

    def poll_input(self, max_reads: int = 64) -> int:
        """Drain pending HID input and dispatch callbacks."""
        processed = 0
        for _ in range(max_reads):
            control_states = self._read_control_states()
            if control_states is None:
                break
            processed += 1
            self._process_input_report(control_states)
        if processed:
            _hid_log(f"POLL processed={processed}")
        return processed

    def _process_input_report(self, control_states: dict) -> None:
        self._dispatch_control_states(control_states)

    def _set_input_read_timeout(self, timeout_ms: int) -> None:
        if hasattr(self.device, "input_read_timeout_ms"):
            self.device.input_read_timeout_ms = timeout_ms

    def _read(self) -> None:
        """Background HID reader; block on hid_read_timeout, then drain queue."""
        self._set_input_read_timeout(self._INPUT_READ_TIMEOUT_MS)
        try:
            while self.run_read_thread:
                try:
                    processed = 0
                    while processed < 64:
                        if processed == 1:
                            self._set_input_read_timeout(0)
                        control_states = self._read_control_states()
                        if control_states is None:
                            break
                        processed += 1
                        self._process_input_report(control_states)
                    if processed:
                        _hid_log(f"READ burst={processed}")
                    self._set_input_read_timeout(self._INPUT_READ_TIMEOUT_MS)
                except TransportError:
                    self.run_read_thread = False
                    self.close()
                    break
        finally:
            self._set_input_read_timeout(0)

    def set_brightness(self, percent):
        if isinstance(percent, float):
            percent = int(100.0 * percent)
        percent = min(max(percent, 0), 100)
        payload = bytearray(32)
        payload[0:3] = [0x03, 0x08, percent]
        self.device.write_feature(payload)

    def get_serial_number(self):
        serial = self.device.read_feature(0x06, 32)
        return self._extract_string(serial[2:])

    def get_firmware_version(self):
        version = self.device.read_feature(0x05, 32)
        return self._extract_string(version[6:])

    def set_key_image(self, key, image):
        if min(max(key, 0), self.KEY_COUNT) != key:
            raise IndexError("Invalid key index {}.".format(key))

        image = bytes(image or self.BLANK_KEY_IMAGE)
        page_number = 0
        bytes_remaining = len(image)
        while bytes_remaining > 0:
            this_length = min(bytes_remaining, self._KEY_PACKET_PAYLOAD_LEN)
            header = [
                0x02,
                0x07,
                key & 0xFF,
                1 if this_length == bytes_remaining else 0,
                this_length & 0xFF,
                (this_length >> 8) & 0xFF,
                page_number & 0xFF,
                (page_number >> 8) & 0xFF,
            ]
            bytes_sent = page_number * self._KEY_PACKET_PAYLOAD_LEN
            payload = bytes(header) + image[bytes_sent:bytes_sent + this_length]
            padding = bytearray(self._IMG_PACKET_LEN - len(payload))
            self.device.write(payload + padding)
            bytes_remaining -= this_length
            page_number += 1

    def set_touchscreen_image(self, image, x_pos=0, y_pos=0, width=0, height=0):
        if not image:
            image = self.BLANK_TOUCHSCREEN_IMAGE

        if width == 0:
            width = self.TOUCHSCREEN_PIXEL_WIDTH
        if height == 0:
            height = self.TOUCHSCREEN_PIXEL_HEIGHT
        x_pos = 0
        y_pos = 0

        if min(max(x_pos, 0), self.TOUCHSCREEN_PIXEL_WIDTH) != x_pos:
            raise IndexError("Invalid x position {}.".format(x_pos))
        if min(max(y_pos, 0), self.TOUCHSCREEN_PIXEL_HEIGHT) != y_pos:
            raise IndexError("Invalid y position {}.".format(y_pos))
        if min(max(width, 1), self.TOUCHSCREEN_PIXEL_WIDTH - x_pos) != width:
            raise IndexError("Invalid draw width {}.".format(width))
        if min(max(height, 1), self.TOUCHSCREEN_PIXEL_HEIGHT - y_pos) != height:
            raise IndexError("Invalid draw height {}.".format(height))

        from PIL import Image as PILImage
        import io

        pil_img = PILImage.open(io.BytesIO(image))
        rotated = pil_img.rotate(90, expand=True)
        buf = io.BytesIO()
        rotated.save(buf, format="JPEG", quality=80)
        image = buf.getvalue()

        int_x = y_pos
        int_y = x_pos
        int_w = height
        int_h = width

        page_number = 0
        bytes_remaining = len(image)
        while bytes_remaining > 0:
            this_length = min(bytes_remaining, self._LCD_PACKET_PAYLOAD_LEN)
            bytes_sent = page_number * self._LCD_PACKET_PAYLOAD_LEN
            header = [
                0x02,
                0x0C,
                int_x & 0xFF,
                (int_x >> 8) & 0xFF,
                int_y & 0xFF,
                (int_y >> 8) & 0xFF,
                int_w & 0xFF,
                (int_w >> 8) & 0xFF,
                int_h & 0xFF,
                (int_h >> 8) & 0xFF,
                1 if this_length == bytes_remaining else 0,
                page_number & 0xFF,
                (page_number >> 8) & 0xFF,
                this_length & 0xFF,
                (this_length >> 8) & 0xFF,
                0x00,
            ]
            payload = bytes(header) + image[bytes_sent:bytes_sent + this_length]
            padding = bytearray(self._IMG_PACKET_LEN - len(payload))
            self.device.write(payload + padding)
            bytes_remaining -= this_length
            page_number += 1

    def set_key_color(self, key, r, g, b):
        pass

    def set_screen_image(self, image):
        pass