"""Tests for RyseBLEDevice.pair error reporting."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from bleak import BleakError
from bleak.backends.device import BLEDevice

from ryseble.device import RyseAuthenticationFailed, RyseBLEDevice

ADDRESS = "AA:BB:CC:DD:EE:FF"


@asynccontextmanager
async def _no_agent(*_args, **_kwargs):
    yield


@pytest.fixture(autouse=True)
def _skip_bluez_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid registering a D-Bus agent from the unit tests."""
    monkeypatch.setattr("ryseble.device.auto_confirm_pairing_agent", _no_agent)
    monkeypatch.setattr("ryseble.device.BOND_RETRIES", 0)
    monkeypatch.setattr("ryseble.device.asyncio.sleep", AsyncMock())


def _shade(*, paired: bool = False) -> RyseBLEDevice:
    details = {"props": {"Paired": True}} if paired else {}
    return RyseBLEDevice(BLEDevice(ADDRESS, "RYSE", details))


def _attach_client(device: RyseBLEDevice, client: MagicMock) -> AsyncMock:
    async def _connect() -> None:
        device.client = client

    connect = AsyncMock(side_effect=_connect)
    device._connect = connect
    return connect


def _client() -> MagicMock:
    client = MagicMock()
    client.is_connected = True
    client.pair = AsyncMock()
    client.start_notify = AsyncMock()
    client.disconnect = AsyncMock()
    client.unpair = AsyncMock()
    return client


async def test_transport_error_is_reraised(caplog: pytest.LogCaptureFixture) -> None:
    """A connect or bond transport error is not reported as a failed bond."""
    device = _shade()
    client = _client()
    client.pair.side_effect = BleakError("out of connection slots")
    _attach_client(device, client)

    with pytest.raises(BleakError, match="out of connection slots"):
        await device.pair()

    assert not any(record.levelname == "ERROR" for record in caplog.records)
    client.disconnect.assert_awaited()


async def test_authentication_failure_is_typed() -> None:
    """BlueZ AuthenticationFailed becomes RyseAuthenticationFailed."""
    device = _shade()
    client = _client()
    client.pair.side_effect = BleakError("AuthenticationFailed")
    _attach_client(device, client)

    with pytest.raises(RyseAuthenticationFailed):
        await device.pair()


async def test_replace_bond_removes_bluez_device_before_pairing() -> None:
    """Re-pairing removes a stale BlueZ bond before Pair."""
    device = _shade(paired=True)
    first = _client()
    second = _client()
    clients = [first, second]

    async def _connect() -> None:
        device.client = clients.pop(0)

    device._connect = AsyncMock(side_effect=_connect)

    assert await device.pair(replace_bond=True)

    first.unpair.assert_awaited_once()
    first.pair.assert_not_awaited()
    second.pair.assert_awaited_once()
    second.start_notify.assert_awaited_once()


async def test_stale_bond_notify_failure_is_authentication() -> None:
    """A bond BlueZ already has that cannot enable notifications is rejected."""
    device = _shade(paired=True)
    client = _client()
    client.start_notify.side_effect = BleakError("notify failed")
    _attach_client(device, client)

    with pytest.raises(RyseAuthenticationFailed, match="Existing bond"):
        await device.pair()

    client.unpair.assert_not_awaited()
