"""
Ryse BLE Python Library
~~~~~~~~~~~~~~~~~~~~~~~
This library provides tools to communicate with Ryse gears
over Bluetooth Low Energy (BLE).

Modules:
- device: RyseBLEDevice class for managing connections
- packets: Helpers to build BLE packets
- constants: Protocol constants and UUIDs
- bluetoothctl: Bluetoothctl wrapper functions
"""

from .device import RyseBLEDevice
from .packets import build_position_packet, build_get_position_packet
from .constants import HARDCODED_UUIDS
from .pairing import is_pairing_mode
from . import bluetoothctl

__all__ = [
    "RyseBLEDevice",
    "build_position_packet",
    "build_get_position_packet",
    "HARDCODED_UUIDS",
    "bluetoothctl",
    "is_pairing_mode",
]
