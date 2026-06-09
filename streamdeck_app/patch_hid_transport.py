"""Use hid_read_timeout for event-driven HID input instead of poll+sleep."""

from __future__ import annotations

import ctypes

from StreamDeck.Transport.LibUSBHIDAPI import LibUSBHIDAPI
from StreamDeck.Transport.Transport import TransportError

_DEFAULT_INPUT_READ_TIMEOUT_MS = 100


def _bind_hid_read_timeout(library: LibUSBHIDAPI.Library) -> None:
    if getattr(library, "_hid_read_timeout_bound", False):
        return
    library.hidapi.hid_read_timeout.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_char),
        ctypes.c_size_t,
        ctypes.c_int,
    ]
    library.hidapi.hid_read_timeout.restype = ctypes.c_int
    library._hid_read_timeout_bound = True


def _library_init(self):
    LibUSBHIDAPI.Library.__original_init__(self)
    _bind_hid_read_timeout(self)


def _library_read_timeout(self, handle, length: int, timeout_ms: int):
    if not handle:
        raise TransportError("No HID device.")

    data = ctypes.create_string_buffer(length)
    # Reads run outside device.mutex (node-hid pushes input independently of writes).
    result = self.hidapi.hid_read_timeout(
        handle, data, len(data), int(timeout_ms)
    )

    if result < 0:
        raise TransportError("Failed to read in report (%d)" % result)
    if result == 0:
        return None
    return data.raw[:length]


def _device_init(self, hidapi, device_info):
    LibUSBHIDAPI.Device.__original_init__(self, hidapi, device_info)
    self.input_read_timeout_ms = 0


def _device_read(self, length: int):
    timeout_ms = getattr(self, "input_read_timeout_ms", 0)
    handle = self.device_handle
    if not handle:
        raise TransportError("No HID device.")
    if timeout_ms != 0 and hasattr(self.hidapi, "read_timeout"):
        return self.hidapi.read_timeout(handle, length, timeout_ms)
    with self.mutex:
        return self.hidapi.read(handle, length)


def apply() -> None:
    library = LibUSBHIDAPI.Library
    if getattr(library, "__hid_transport_patched__", False):
        return

    library.__original_init__ = library.__init__
    library.__init__ = _library_init
    library.read_timeout = _library_read_timeout

    device = LibUSBHIDAPI.Device
    device.__original_init__ = device.__init__
    device.__init__ = _device_init
    device.__original_read__ = device.read
    device.read = _device_read

    library.__hid_transport_patched__ = True


apply()