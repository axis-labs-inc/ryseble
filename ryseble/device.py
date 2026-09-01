"""Async client for a single RYSE BLE shade.

Connections go through ``bleak-retry-connector`` so the same code works against a
local adapter, a remote ESPHome/Shelly Bluetooth proxy, macOS and Windows. Nothing
in this module blocks the event loop or shells out to an external tool.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from bleak import BleakClient, BleakError
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
    retry_bluetooth_connection_error,
)

from .constants import (
    BOND_RETRIES,
    BOND_RETRY_DELAY,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_CONNECT_ATTEMPTS,
    HARDCODED_UUIDS,
    MAX_POSITION,
    MIN_POSITION,
)
from .bluez_agent import auto_confirm_pairing_agent
from .packets import (
    build_get_position_packet,
    build_position_packet,
    is_valid_position,
    parse_notification,
)

_LOGGER = logging.getLogger(__name__)

# D-Bus EOFError is raised when BlueZ drops the socket mid-call (typical after
# the shade disconnects because pairing never completed). It is not an OSError.
_CONNECTION_ERRORS = (BleakError, OSError, TimeoutError, EOFError)


def _is_authentication_failed(err: BaseException) -> bool:
    text = str(err).lower()
    return "authenticationfailed" in text or "authentication failed" in text

PositionCallback = Callable[[int], Awaitable[None] | None]
DisconnectCallback = Callable[[], None]


class RyseBLEDevice:
    """Represent a RYSE shade and provide async methods to interact with it."""

    def __init__(
        self,
        ble_device: BLEDevice,
        rx_uuid: str | None = None,
        tx_uuid: str | None = None,
    ) -> None:
        """Initialise the device.

        ``ble_device`` must be a Bleak ``BLEDevice``. Inside Home Assistant, get one
        from ``homeassistant.components.bluetooth.async_ble_device_from_address``;
        never pass a bare address, as that would force Bleak to run its own scan and
        would break Bluetooth proxy setups.
        """
        self._set_ble_device(ble_device)
        self._rx_uuid = rx_uuid or HARDCODED_UUIDS["rx_uuid"]
        self._tx_uuid = tx_uuid or HARDCODED_UUIDS["tx_uuid"]
        self._client: BleakClient | None = None
        self._connect_lock = asyncio.Lock()
        self._position: int | None = None
        self._position_callbacks: list[PositionCallback] = []
        self._disconnect_callbacks: list[DisconnectCallback] = []
        self._pending_tasks: set[asyncio.Task[None]] = set()

    def _set_ble_device(self, ble_device: BLEDevice) -> None:
        if not isinstance(ble_device, BLEDevice):
            raise TypeError(
                "RyseBLEDevice requires a bleak BLEDevice, got "
                f"{type(ble_device).__name__}. In Home Assistant use "
                "bluetooth.async_ble_device_from_address(hass, address, connectable=True)."
            )
        self._ble_device = ble_device

    @property
    def ble_device(self) -> BLEDevice:
        """Return the BLEDevice this client connects through."""
        return self._ble_device

    @property
    def address(self) -> str:
        """Return the Bluetooth address of the device."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Return the advertised name, falling back to the address."""
        return self._ble_device.name or self._ble_device.address

    @property
    def client(self) -> BleakClient | None:
        """Return the underlying Bleak client, if connected."""
        return self._client

    @property
    def is_connected(self) -> bool:
        """Return True while a GATT connection is up."""
        return self._client is not None and self._client.is_connected

    @property
    def position(self) -> int | None:
        """Return the last position reported by the device."""
        return self._position

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Replace the BLEDevice with a freshly advertised one.

        Callers should do this on every advertisement. A ``BLEDevice`` carries the
        route to the device, which changes when a shade moves between Bluetooth
        proxies, and a stale one makes reconnects fail.
        """
        self._set_ble_device(ble_device)

    def register_position_callback(
        self, callback: PositionCallback
    ) -> Callable[[], None]:
        """Subscribe to position reports and return an unsubscribe function."""
        self._position_callbacks.append(callback)

        def _unregister() -> None:
            if callback in self._position_callbacks:
                self._position_callbacks.remove(callback)

        return _unregister

    def register_disconnected_callback(
        self, callback: DisconnectCallback
    ) -> Callable[[], None]:
        """Subscribe to disconnections and return an unsubscribe function."""
        self._disconnect_callbacks.append(callback)

        def _unregister() -> None:
            if callback in self._disconnect_callbacks:
                self._disconnect_callbacks.remove(callback)

        return _unregister

    async def connect(self, pair: bool = False) -> bool:
        """Connect to the device and subscribe to notifications.

        Set ``pair`` to also establish a bond before enabling notifications.
        Returns True once notifications are flowing.
        """
        try:
            await self._ensure_connected(pair=pair)
        except _CONNECTION_ERRORS as err:
            _LOGGER.debug("Could not connect to %s: %s", self.name, err)
            return False
        return True

    async def pair(self) -> bool:
        """Connect, bond, then subscribe to notifications.

        The RX characteristic's CCCD is encrypted. Enabling notifications
        before BlueZ has a bond makes the shade drop the link (D-Bus EOFError).
        Bond immediately after the GATT connect, then subscribe. On Linux a
        temporary BlueZ Agent1 is registered *before* the connect so it can
        answer the yes/no prompt that released Bleak cannot answer. AuthenticationFailed
        is retried a few times after a short pause.
        """
        try:
            await self._ensure_connected(pair=True)
        except _CONNECTION_ERRORS as err:
            _LOGGER.debug("Could not pair with %s: %s", self.name, err)
            return False
        return True

    async def disconnect(self) -> None:
        """Drop the GATT connection."""
        async with self._connect_lock:
            client = self._client
            self._client = None
            if client is None:
                return
            try:
                await client.disconnect()
            except _CONNECTION_ERRORS as err:
                _LOGGER.debug("Error disconnecting from %s: %s", self.name, err)

    async def _ensure_connected(self, pair: bool = False) -> BleakClient:
        """Return a live client, connecting first if needed."""
        if self._client is not None and self._client.is_connected:
            return self._client

        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                return self._client

            if pair:
                async with auto_confirm_pairing_agent():
                    return await self._connect_locked(pair=True)
            return await self._connect_locked(pair=False)

    async def _connect_locked(self, pair: bool) -> BleakClient:
        """Connect, optionally bond, and subscribe. Caller holds ``_connect_lock``."""
        _LOGGER.debug("Connecting to %s", self.name)
        client = await self._establish()
        try:
            if pair:
                await self._bond(client)
                client = self._client
                if client is None or not client.is_connected:
                    raise BleakError(f"Lost connection to {self.name} during pairing")
            await client.start_notify(self._rx_uuid, self._notification_handler)
        except _CONNECTION_ERRORS:
            live = self._client
            await self._drop_client(client)
            if live is not None and live is not client:
                await self._drop_client(live)
            raise

        _LOGGER.debug("Connected to %s", self.name)
        return client

    async def _establish(self) -> BleakClient:
        client = await establish_connection(
            BleakClientWithServiceCache,
            self._ble_device,
            self.name,
            disconnected_callback=self._on_disconnected,
            ble_device_callback=lambda: self._ble_device,
            max_attempts=DEFAULT_CONNECT_ATTEMPTS,
        )
        self._client = client
        return client

    async def _bond(self, client: BleakClient) -> None:
        """Establish an OS-level bond, retrying AuthenticationFailed."""
        last_error: BaseException | None = None
        for attempt in range(1, BOND_RETRIES + 2):
            try:
                await client.pair()
                return
            except _CONNECTION_ERRORS as err:
                if not _is_authentication_failed(err):
                    raise
                last_error = err
                if attempt > BOND_RETRIES:
                    break
                _LOGGER.debug(
                    "Bonding with %s failed (%s); retry %s/%s",
                    self.name,
                    err,
                    attempt,
                    BOND_RETRIES,
                )
                await self._drop_client(client)
                await asyncio.sleep(BOND_RETRY_DELAY)
                client = await self._establish()
        assert last_error is not None
        raise last_error

    async def _drop_client(self, client: BleakClient) -> None:
        if self._client is client:
            self._client = None
        try:
            await client.disconnect()
        except _CONNECTION_ERRORS:
            pass

    def _on_disconnected(self, client: BleakClient) -> None:
        """Handle the device going away."""
        if client is not self._client:
            return
        _LOGGER.debug("Disconnected from %s", self.name)
        self._client = None
        for callback in list(self._disconnect_callbacks):
            callback()

    def _notification_handler(
        self, _characteristic: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Handle a GATT notification from the device."""
        position = parse_notification(bytes(data))
        if position is None:
            return

        _LOGGER.debug("%s reported position %d", self.name, position)
        self._position = position
        for callback in list(self._position_callbacks):
            self._run_callback(callback, position)

    def _run_callback(self, callback: PositionCallback, position: int) -> None:
        """Invoke a subscriber, accepting both sync and async callables."""
        try:
            result = callback(position)
        except Exception:  # a broken subscriber must not kill the notification stream
            _LOGGER.exception("Error in RYSE position callback")
            return

        if result is None:
            return

        task = asyncio.get_running_loop().create_task(self._await_callback(result))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _await_callback(self, result: Awaitable[None]) -> None:
        try:
            await result
        except Exception:  # a broken subscriber must not kill the notification stream
            _LOGGER.exception("Error in RYSE position callback")

    @retry_bluetooth_connection_error(DEFAULT_CONNECT_ATTEMPTS)
    async def _write(self, data: bytes) -> None:
        """Write a frame to the TX characteristic."""
        client = await self._ensure_connected()
        async with asyncio.timeout(DEFAULT_COMMAND_TIMEOUT):
            await client.write_gatt_char(self._tx_uuid, data)

    async def read_data(self) -> bytes | None:
        """Read the RX characteristic directly, bypassing notifications."""
        client = await self._ensure_connected()
        async with asyncio.timeout(DEFAULT_COMMAND_TIMEOUT):
            return bytes(await client.read_gatt_char(self._rx_uuid))

    async def write_data(self, data: bytes) -> None:
        """Send a raw frame to the device."""
        await self._write(data)

    async def send_set_position(self, position: int) -> None:
        """Move the shade to ``position``."""
        await self._write(build_position_packet(position))

    async def send_get_position(self) -> None:
        """Ask the device to report its position over notifications."""
        await self._write(build_get_position_packet())

    async def send_open(self) -> None:
        """Fully open the shade."""
        await self.send_set_position(MIN_POSITION)

    async def send_close(self) -> None:
        """Fully close the shade."""
        await self.send_set_position(MAX_POSITION)

    def is_valid_position(self, position: int) -> bool:
        """Return True if ``position`` is within the device's range."""
        return is_valid_position(position)

    def get_real_position(self, position: int) -> int:
        """Convert between the device scale and the cover scale, which are inverted."""
        return MAX_POSITION - position

    def is_closed(self, position: int) -> bool:
        """Return True if ``position`` means fully closed on the device scale."""
        return position == MAX_POSITION

    async def get_device_info(self) -> Any:
        """Return the resolved GATT service collection."""
        if self._client is None:
            return None
        return self._client.services
