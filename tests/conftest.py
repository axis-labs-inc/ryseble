"""Shared fixtures for the ryseble test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from bleak.backends.device import BLEDevice

from ryseble.device import RyseBLEDevice

ADDRESS = "AA:BB:CC:DD:EE:FF"


@asynccontextmanager
async def _noop_pairing_agent() -> AsyncIterator[None]:
    """Stand in for the BlueZ agent so unit tests never touch D-Bus."""
    yield


class FakeBleakClient:
    """Minimal stand-in for a connected BleakClient."""

    def __init__(self, ble_device: BLEDevice) -> None:
        self.ble_device = ble_device
        self.is_connected = True
        self.services = "services"
        self.written: list[bytes] = []
        self.notify_callback: Callable[[object, bytearray], None] | None = None
        self.read_value = bytearray(b"\xf5\x03\x01\x07\x2a\x32")
        self.disconnected_callback: Callable[[FakeBleakClient], None] | None = None
        self.ops: list[str] = []

    async def start_notify(self, _char, callback) -> None:
        self.ops.append("notify")
        self.notify_callback = callback

    async def write_gatt_char(self, _char, data, response=None) -> None:
        self.written.append(bytes(data))

    async def read_gatt_char(self, _char) -> bytearray:
        return self.read_value

    async def pair(self) -> bool:
        self.ops.append("pair")
        return True

    async def disconnect(self) -> None:
        self.is_connected = False

    def notify(self, data: bytes) -> None:
        """Simulate an incoming GATT notification."""
        assert self.notify_callback is not None
        self.notify_callback(object(), bytearray(data))

    def simulate_disconnect(self) -> None:
        """Simulate the device dropping the connection."""
        self.is_connected = False
        assert self.disconnected_callback is not None
        self.disconnected_callback(self)


@pytest.fixture
def ble_device() -> BLEDevice:
    """Return a BLEDevice for a RYSE shade."""
    return BLEDevice(ADDRESS, "RYSE Shade", {})


@pytest.fixture
def fake_client(ble_device: BLEDevice):
    """Patch establish_connection so no real BLE stack is touched."""
    client = FakeBleakClient(ble_device)

    async def _establish_connection(_client_class, _device, _name, **kwargs):
        client.disconnected_callback = kwargs.get("disconnected_callback")
        client.is_connected = True
        return client

    with (
        patch("ryseble.device.establish_connection", _establish_connection),
        patch("ryseble.device.auto_confirm_pairing_agent", _noop_pairing_agent),
    ):
        yield client


@pytest.fixture
def device(ble_device: BLEDevice) -> RyseBLEDevice:
    """Return a RyseBLEDevice bound to the fake BLEDevice."""
    return RyseBLEDevice(ble_device)
