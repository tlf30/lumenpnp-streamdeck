"""Register Stream Deck + XL (PID 0x00C6) with python-elgato-streamdeck."""

import streamdeck_app.patch_hid_transport  # noqa: F401 - hid_read_timeout
from StreamDeck import DeviceManager
from StreamDeck.ProductIDs import USBProductIDs, USBVendorIDs

from .devices.streamdeck_plus_xl import StreamDeckPlusXL

USBProductIDs.USB_PID_STREAMDECK_PLUS_XL = 0x00C6

_original_enumerate = DeviceManager.DeviceManager.enumerate


def _patched_enumerate(self):
    decks = _original_enumerate(self)
    found = self.transport.enumerate(
        vid=USBVendorIDs.USB_VID_ELGATO,
        pid=USBProductIDs.USB_PID_STREAMDECK_PLUS_XL,
    )
    decks.extend(StreamDeckPlusXL(device) for device in found)
    return decks


DeviceManager.DeviceManager.enumerate = _patched_enumerate