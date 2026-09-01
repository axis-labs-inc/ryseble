"""Identify RYSE devices and their pairing state from advertisement data.

Everything here is a pure function over data that the caller already received from
its own Bluetooth stack, so it works identically with a local adapter, a remote
ESPHome/Shelly proxy, macOS or Windows. Home Assistant callers should pass the
fields of a ``BluetoothServiceInfoBleak``; plain Bleak callers should pass the
fields of an ``AdvertisementData``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import Protocol, runtime_checkable

from .constants import (
    MANUFACTURER_ID,
    MANUFACTURER_NAME,
    PAIRING_MODE_FLAG,
    RYSE_SERVICE_UUID,
)


@runtime_checkable
class AdvertisementLike(Protocol):
    """The subset of ``BluetoothServiceInfoBleak`` this module needs."""

    address: str
    name: str
    manufacturer_data: Mapping[int, bytes]
    service_uuids: list[str]


def is_ryse_advertisement(
    manufacturer_data: Mapping[int, bytes] | None = None,
    service_uuids: Iterable[str] | None = None,
    name: str | None = None,
) -> bool:
    """Return True if the advertisement looks like it came from a RYSE device."""
    if manufacturer_data and MANUFACTURER_ID in manufacturer_data:
        return True
    if service_uuids and RYSE_SERVICE_UUID in {uuid.lower() for uuid in service_uuids}:
        return True
    return bool(name and MANUFACTURER_NAME in name.upper())


def is_pairing_mode(manufacturer_data: Mapping[int, bytes] | None) -> bool:
    """Return True if the device is advertising that it accepts a new pairing.

    RYSE devices set :data:`~ryseble.constants.PAIRING_MODE_FLAG` in the first byte
    of their manufacturer data while the user holds the pairing button.
    """
    if not manufacturer_data:
        return False

    payload = manufacturer_data.get(MANUFACTURER_ID)
    if not payload:
        return False

    return bool(payload[0] & PAIRING_MODE_FLAG)


def filter_ryse_devices_pairing(
    advertisements: Iterable[AdvertisementLike],
    existing_addresses: Collection[str] = (),
) -> dict[str, str]:
    """Map address to display label for every RYSE device currently in pairing mode.

    ``existing_addresses`` lets callers hide devices they have already set up.
    """
    already_known = {address.upper() for address in existing_addresses}

    return {
        advertisement.address: f"{advertisement.name} ({advertisement.address})"
        for advertisement in advertisements
        if advertisement.name
        and advertisement.address.upper() not in already_known
        and is_pairing_mode(advertisement.manufacturer_data)
    }
