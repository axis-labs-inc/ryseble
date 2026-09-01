"""Standalone scanning helpers for scripts and tests.

Do not use this module inside Home Assistant. Home Assistant owns the only
Bluetooth scanner in the process; integrations must take advertisements from
``homeassistant.components.bluetooth`` and feed them to :mod:`ryseble.pairing`
instead. Starting a second scanner there breaks Bluetooth proxies.
"""

from __future__ import annotations

import logging

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .pairing import is_pairing_mode, is_ryse_advertisement

_LOGGER = logging.getLogger(__name__)

DEFAULT_SCAN_TIMEOUT = 10.0


async def async_discover_ryse_devices(
    timeout: float = DEFAULT_SCAN_TIMEOUT,
    pairing_only: bool = False,
) -> dict[str, tuple[BLEDevice, AdvertisementData]]:
    """Scan for RYSE devices and return them keyed by address."""
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)

    results: dict[str, tuple[BLEDevice, AdvertisementData]] = {}
    for address, (device, advertisement) in discovered.items():
        if not is_ryse_advertisement(
            advertisement.manufacturer_data,
            advertisement.service_uuids,
            advertisement.local_name or device.name,
        ):
            continue
        if pairing_only and not is_pairing_mode(advertisement.manufacturer_data):
            continue
        results[address] = (device, advertisement)

    _LOGGER.debug("Discovered %d RYSE device(s)", len(results))
    return results
