"""Ryse BLE Python Library.

Talk to RYSE BLE Smart Shades over Bluetooth Low Energy. The library is fully
async and built on Bleak plus bleak-retry-connector, so it never blocks the event
loop and works with local adapters, ESPHome/Shelly Bluetooth proxies, macOS and
Windows alike.

Modules:
- device: RyseBLEDevice, the connection and command client
- pairing: identify RYSE devices and pairing mode from advertisement data
- packets: build and parse protocol frames
- discovery: Bleak scanning helpers for standalone scripts (not for Home Assistant)
- constants: protocol constants and UUIDs
"""

from .constants import (
    HARDCODED_UUIDS,
    MANUFACTURER_ID,
    MANUFACTURER_NAME,
    MAX_POSITION,
    MIN_POSITION,
    PAIRING_MODE_FLAG,
    RX_CHAR_UUID,
    RYSE_SERVICE_UUID,
    TX_CHAR_UUID,
)
from .device import RyseBLEDevice
from .packets import (
    build_get_position_packet,
    build_position_packet,
    is_valid_position,
    parse_notification,
)
from .pairing import filter_ryse_devices_pairing, is_pairing_mode, is_ryse_advertisement

__all__ = [
    "HARDCODED_UUIDS",
    "MANUFACTURER_ID",
    "MANUFACTURER_NAME",
    "MAX_POSITION",
    "MIN_POSITION",
    "PAIRING_MODE_FLAG",
    "RX_CHAR_UUID",
    "RYSE_SERVICE_UUID",
    "TX_CHAR_UUID",
    "RyseBLEDevice",
    "build_get_position_packet",
    "build_position_packet",
    "filter_ryse_devices_pairing",
    "is_pairing_mode",
    "is_ryse_advertisement",
    "is_valid_position",
    "parse_notification",
]
