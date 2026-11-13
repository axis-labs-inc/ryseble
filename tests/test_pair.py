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


def _shade(*, paired: bool = False, mgmt: bool = False) -> RyseBLEDevice:
    """Build a shade.

    ``mgmt`` matches Home Assistant OS: the advertisement props name the
    adapter and never include Paired.
    """
    if mgmt:
        details = {
            "path": "/org/bluez/hci0/dev_AA",
            "props": {"Adapter": "/org/bluez/hci0"},
        }
    elif paired:
        details = {"props": {"Paired": True}}
    else:
        details = {}
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


async def test_replace_bond_retries_until_bluez_recreates_the_device() -> None:
    """RemoveDevice can delete the BlueZ object before the next advertisement."""
    device = _shade(paired=True)
    calls = 0

    async def _connect() -> None:
        nonlocal calls
        calls += 1
        device.client = _client()
        if calls == 2:
            raise BleakError("UnknownObject")

    device._connect = AsyncMock(side_effect=_connect)

    assert await device.pair(replace_bond=True)
    assert calls == 3


async def test_replace_bond_logs_a_failed_removal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed RemoveDevice is visible, and pairing still reconnects."""
    device = _shade(paired=True)
    client = _client()
    client.unpair.side_effect = BleakError("RemoveDevice failed")
    _attach_client(device, client)

    assert await device.pair(replace_bond=True)

    assert any(
        record.levelname == "WARNING"
        and "Could not remove the existing bond" in record.message
        for record in caplog.records
    )


@pytest.mark.parametrize(
    ("shade", "error"),
    [
        pytest.param("plain", BleakError("Insufficient Encryption"), id="encryption"),
        pytest.param("plain", BleakError("Not paired"), id="not-paired"),
        pytest.param("mgmt", BleakError("ATT error: 0x05"), id="mgmt-att"),
        pytest.param("paired", BleakError("PIN or Key Missing"), id="paired-pin"),
    ],
)
async def test_notify_bond_rejection_is_authentication(
    shade: str, error: Exception
) -> None:
    """An authentication-shaped notify failure is a rejected bond.

    The advertisement's Paired property is not consulted. Home Assistant OS
    never sets it, and a host that does set it must not treat every later
    notify failure as a rejected bond.
    """
    device = _shade(paired=shade == "paired", mgmt=shade == "mgmt")
    client = _client()
    client.start_notify.side_effect = error
    _attach_client(device, client)

    with pytest.raises(RyseAuthenticationFailed):
        await device.pair()

    client.unpair.assert_not_awaited()


@pytest.mark.parametrize(
    ("shade", "error"),
    [
        pytest.param("mgmt", EOFError("dbus closed"), id="mgmt-eof"),
        pytest.param("mgmt", TimeoutError("timed out"), id="mgmt-timeout"),
        pytest.param("paired", TimeoutError("timed out"), id="paired-timeout"),
        pytest.param("paired", BleakError("notify failed"), id="paired-generic"),
    ],
)
async def test_notify_transport_error_stays_transport(
    shade: str, error: Exception
) -> None:
    """Timeouts, disconnects, and generic notify errors stay transport errors."""
    device = _shade(paired=shade == "paired", mgmt=shade == "mgmt")
    client = _client()
    client.start_notify.side_effect = error
    _attach_client(device, client)

    with pytest.raises(type(error)):
        await device.pair()
