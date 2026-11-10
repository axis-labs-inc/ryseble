"""
Ryse BLE Python Library
~~~~~~~~~~~~~~~~~~~~~~~
This library provides tools to communicate with Ryse gears
over Bluetooth Low Energy (BLE).

Modules:
- device: RyseBLEDevice class for managing connections
- packets: Helpers to build BLE packets
- constants: Protocol constants and UUIDs
- pairing: Advertisement pairing-mode detection
"""

from .constants import HARDCODED_UUIDS
from .device import RyseAuthenticationFailed, RyseBLEDevice
from .packets import build_get_position_packet, build_position_packet
from .pairing import is_pairing_mode

__all__ = [
    "HARDCODED_UUIDS",
    "RyseAuthenticationFailed",
    "RyseBLEDevice",
    "build_get_position_packet",
    "build_position_packet",
    "is_pairing_mode",
]
