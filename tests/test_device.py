"""Tests for RyseBLEDevice."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from bleak import BleakError
from bleak.backends.device import BLEDevice

from ryseble.constants import BOND_RETRIES
from ryseble.device import RyseBLEDevice

from .conftest import ADDRESS


def test_requires_ble_device() -> None:
    with pytest.raises(TypeError, match="requires a bleak BLEDevice"):
        RyseBLEDevice(ADDRESS)


def test_exposes_address_and_name(device: RyseBLEDevice) -> None:
    assert device.address == ADDRESS
    assert device.name == "RYSE Shade"
    assert device.is_connected is False
    assert device.position is None


def test_set_ble_device_replaces_route(device: RyseBLEDevice) -> None:
    proxied = BLEDevice(ADDRESS, "RYSE Shade", {"proxy": "esphome"})
    device.set_ble_device(proxied)
    assert device.ble_device is proxied


def test_set_ble_device_rejects_address(device: RyseBLEDevice) -> None:
    with pytest.raises(TypeError):
        device.set_ble_device(ADDRESS)


async def test_connect_subscribes_to_notifications(device, fake_client) -> None:
    assert await device.connect() is True
    assert device.is_connected is True
    assert fake_client.notify_callback is not None
    assert fake_client.ops == ["notify"]


async def test_pair_bonds_before_notifications(device, fake_client) -> None:
    assert await device.pair() is True
    assert fake_client.ops == ["pair", "notify"]


async def test_pair_wraps_bond_with_bluez_agent(device, fake_client) -> None:
    entered: list[str] = []

    @asynccontextmanager
    async def _track():
        entered.append("enter")
        yield
        entered.append("exit")

    with patch("ryseble.device.auto_confirm_pairing_agent", _track):
        assert await device.pair() is True

    assert entered == ["enter", "exit"]
    assert fake_client.ops == ["pair", "notify"]


async def test_pair_gives_up_after_bond_retries(device, fake_client) -> None:
    attempts = 0

    async def _fail_pair() -> bool:
        nonlocal attempts
        attempts += 1
        raise BleakError("Authentication Failed")

    fake_client.pair = _fail_pair
    with patch("ryseble.device.asyncio.sleep", new_callable=AsyncMock):
        assert await device.pair() is False
    assert attempts == BOND_RETRIES + 1
    assert device.is_connected is False


async def test_pair_retries_after_authentication_failed(device, fake_client) -> None:
    attempts = 0

    async def _flaky_pair() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise BleakError(
                "[org.bluez.Error.AuthenticationFailed] Authentication Failed"
            )
        fake_client.ops.append("pair")
        fake_client.is_connected = True
        return True

    fake_client.pair = _flaky_pair
    with patch("ryseble.device.asyncio.sleep", new_callable=AsyncMock):
        assert await device.pair() is True
    assert attempts == 2
    assert fake_client.ops == ["pair", "notify"]


async def test_connect_returns_false_on_disconnect_during_notify(
    device, fake_client
) -> None:
    async def _eof(*args, **kwargs) -> None:
        raise EOFError

    fake_client.start_notify = _eof
    assert await device.connect() is False
    assert device.is_connected is False


async def test_connect_returns_false_on_error(device, ble_device) -> None:
    async def _fail(*args, **kwargs):
        raise BleakError("no route to device")

    with patch("ryseble.device.establish_connection", _fail):
        assert await device.connect() is False
    assert device.is_connected is False


async def test_connect_is_reused(device, fake_client) -> None:
    await device.connect()
    client = device.client
    await device.connect()
    assert device.client is client


async def test_concurrent_connects_share_one_connection(device, fake_client) -> None:
    results = await asyncio.gather(*(device.connect() for _ in range(5)))
    assert all(results)
    assert device.client is fake_client


async def test_failed_notify_subscription_disconnects(device, fake_client) -> None:
    async def _boom(*args, **kwargs):
        raise BleakError("characteristic not found")

    fake_client.start_notify = _boom

    assert await device.connect() is False
    assert device.client is None
    assert fake_client.is_connected is False


async def test_send_set_position_writes_frame(device, fake_client) -> None:
    await device.send_set_position(50)
    assert fake_client.written == [bytes([0xF5, 0x03, 0x01, 0x01, 0x32, 0x34])]


async def test_send_open_and_close(device, fake_client) -> None:
    await device.send_open()
    await device.send_close()
    assert fake_client.written == [
        bytes([0xF5, 0x03, 0x01, 0x01, 0x00, 0x02]),
        bytes([0xF5, 0x03, 0x01, 0x01, 0x64, 0x66]),
    ]


async def test_send_get_position(device, fake_client) -> None:
    await device.send_get_position()
    assert fake_client.written == [bytes([0xF5, 0x02, 0x01, 0x03, 0x04])]


async def test_commands_connect_on_demand(device, fake_client) -> None:
    assert device.is_connected is False
    await device.send_open()
    assert device.is_connected is True


async def test_notification_updates_position_and_callbacks(device, fake_client) -> None:
    positions: list[int] = []
    await device.connect()
    device.register_position_callback(positions.append)

    fake_client.notify(bytes([0xF5, 0x03, 0x01, 0x07, 0x2A, 0x32]))

    assert positions == [42]
    assert device.position == 42


async def test_notification_supports_async_callbacks(device, fake_client) -> None:
    positions: list[int] = []

    async def _async_callback(position: int) -> None:
        positions.append(position)

    await device.connect()
    device.register_position_callback(_async_callback)

    fake_client.notify(bytes([0xF5, 0x03, 0x01, 0x07, 0x2A, 0x32]))
    await asyncio.sleep(0)

    assert positions == [42]


async def test_notification_ignores_user_target_report(device, fake_client) -> None:
    positions: list[int] = []
    await device.connect()
    device.register_position_callback(positions.append)

    fake_client.notify(bytes([0xF5, 0x03, 0x01, 0x18, 0x2A, 0x43]))

    assert positions == []
    assert device.position is None


async def test_failing_callback_does_not_break_notifications(
    device, fake_client
) -> None:
    positions: list[int] = []

    def _explode(position: int) -> None:
        raise RuntimeError("subscriber bug")

    await device.connect()
    device.register_position_callback(_explode)
    device.register_position_callback(positions.append)

    fake_client.notify(bytes([0xF5, 0x03, 0x01, 0x07, 0x2A, 0x32]))

    assert positions == [42]


async def test_unregister_position_callback(device, fake_client) -> None:
    positions: list[int] = []
    await device.connect()
    unregister = device.register_position_callback(positions.append)
    unregister()

    fake_client.notify(bytes([0xF5, 0x03, 0x01, 0x07, 0x2A, 0x32]))

    assert positions == []


async def test_disconnect_callback_fires(device, fake_client) -> None:
    disconnects: list[bool] = []
    await device.connect()
    device.register_disconnected_callback(lambda: disconnects.append(True))

    fake_client.simulate_disconnect()

    assert disconnects == [True]
    assert device.is_connected is False


async def test_disconnect(device, fake_client) -> None:
    await device.connect()
    await device.disconnect()

    assert device.is_connected is False
    assert device.client is None
    assert fake_client.is_connected is False


async def test_disconnect_when_never_connected(device) -> None:
    await device.disconnect()
    assert device.client is None


async def test_read_data(device, fake_client) -> None:
    assert await device.read_data() == bytes([0xF5, 0x03, 0x01, 0x07, 0x2A, 0x32])


def test_position_helpers(device: RyseBLEDevice) -> None:
    assert device.is_valid_position(50) is True
    assert device.is_valid_position(101) is False
    assert device.get_real_position(30) == 70
    assert device.is_closed(100) is True
    assert device.is_closed(0) is False
