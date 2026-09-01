"""Tests for advertisement-based RYSE detection."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from ryseble.constants import MANUFACTURER_ID, RYSE_SERVICE_UUID
from ryseble.pairing import (
    filter_ryse_devices_pairing,
    is_pairing_mode,
    is_ryse_advertisement,
)


@dataclass
class FakeAdvertisement:
    """Stand-in for a Home Assistant BluetoothServiceInfoBleak."""

    address: str
    name: str
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)
    service_uuids: list[str] = field(default_factory=list)


def test_is_pairing_mode_detects_flag() -> None:
    assert is_pairing_mode({MANUFACTURER_ID: b"\xcc\x64\x62\x64"}) is True


def test_is_pairing_mode_false_when_flag_clear() -> None:
    assert is_pairing_mode({MANUFACTURER_ID: b"\x8c\x64"}) is False


@pytest.mark.parametrize(
    "manufacturer_data",
    [None, {}, {MANUFACTURER_ID: b""}, {0x004C: b"\xcc"}],
)
def test_is_pairing_mode_handles_missing_data(manufacturer_data) -> None:
    assert is_pairing_mode(manufacturer_data) is False


def test_is_ryse_advertisement_by_manufacturer_id() -> None:
    assert is_ryse_advertisement({MANUFACTURER_ID: b"\x8c"}) is True


def test_is_ryse_advertisement_by_service_uuid() -> None:
    assert is_ryse_advertisement(None, [RYSE_SERVICE_UUID.upper()]) is True


def test_is_ryse_advertisement_by_name() -> None:
    assert is_ryse_advertisement(None, None, "ryse shade 1") is True


def test_is_ryse_advertisement_rejects_other_devices() -> None:
    assert is_ryse_advertisement({0x004C: b"\x01"}, ["1800"], "Some Light") is False


def test_filter_ryse_devices_pairing() -> None:
    pairing = FakeAdvertisement(
        "AA:BB:CC:DD:EE:01", "RYSE 1", {MANUFACTURER_ID: b"\xcc"}
    )
    idle = FakeAdvertisement("AA:BB:CC:DD:EE:02", "RYSE 2", {MANUFACTURER_ID: b"\x8c"})
    unnamed = FakeAdvertisement("AA:BB:CC:DD:EE:03", "", {MANUFACTURER_ID: b"\xcc"})

    assert filter_ryse_devices_pairing([pairing, idle, unnamed]) == {
        "AA:BB:CC:DD:EE:01": "RYSE 1 (AA:BB:CC:DD:EE:01)"
    }


def test_filter_ryse_devices_pairing_skips_configured_addresses() -> None:
    pairing = FakeAdvertisement(
        "AA:BB:CC:DD:EE:01", "RYSE 1", {MANUFACTURER_ID: b"\xcc"}
    )

    assert filter_ryse_devices_pairing([pairing], {"aa:bb:cc:dd:ee:01"}) == {}
