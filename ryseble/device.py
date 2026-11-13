"""RyseBLEDevice: async wrapper around Bleak for RYSE devices.

This module intentionally keeps responsibilities small: connect/disconnect,
read/write GATT characteristics, and deliver notifications via a callback.
"""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient, BleakError, BleakScanner
from bleak.backends.device import BLEDevice

from .bluez_agent import auto_confirm_pairing_agent, bleak_client_dbus_bus
from .constants import (
    BOND_REMOVE_RECONNECT_ATTEMPTS,
    BOND_RETRIES,
    BOND_RETRY_DELAY,
    DEFAULT_CONNECT_ATTEMPTS,
    HARDCODED_UUIDS,
)
from .packets import build_position_packet, build_get_position_packet

_LOGGER = logging.getLogger(__name__)

# D-Bus EOFError is raised when BlueZ drops the socket mid-call (typical after
# the shade disconnects because pairing never completed). It is not an OSError.
_CONNECTION_ERRORS = (BleakError, OSError, TimeoutError, EOFError)


# Matched against the exception text. Timeouts and EOF stay transport errors:
# Home Assistant OS does not put Paired on BLEDevice.details, and a radio
# glitch must not be reported as a rejected bond.
_BOND_REJECTION_MARKERS = (
    "authenticationfailed",
    "authentication failed",
    "insufficient authentication",
    "insufficient encryption",
    "not permitted",
    "not paired",
    "pin or key missing",
    "0x05",
    "0x0f",
)


def _is_bond_rejected(err: BaseException) -> bool:
    """Return True when the error is a rejected or unusable bond."""
    text = str(err).lower()
    return any(marker in text for marker in _BOND_REJECTION_MARKERS)


class RyseAuthenticationFailed(Exception):
    """The shade rejected the bond, or the bond BlueZ already has is unusable.

    Transport failures are not this error. ``pair()`` re-raises those so the
    caller can retry without asking the user to put the shade in pairing mode.
    """


class RyseBLEDevice:
    """Represent a RYSE device and provide async methods to interact with it."""

    def __init__(self, ble_device=None, address=None, rx_uuid=None, tx_uuid=None):
        """Initialise the device.

        Prefer a Bleak ``BLEDevice`` from Home Assistant's
        ``async_ble_device_from_address`` so connections follow the selected
        adapter or Bluetooth proxy. A bare address string is accepted only for
        standalone use outside Home Assistant.
        """
        if isinstance(ble_device, str):
            # Backward-compatible positional address: RyseBLEDevice("AA:BB:...")
            address = ble_device
            ble_device = None

        self._ble_device: BLEDevice | None = None
        self.address = address
        if ble_device is not None:
            self.set_ble_device(ble_device)

        self.rx_uuid = rx_uuid or HARDCODED_UUIDS["rx_uuid"]
        self.tx_uuid = tx_uuid or HARDCODED_UUIDS["tx_uuid"]
        self.client = None
        self.disconnected_callback = None

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Replace the BLEDevice with a freshly advertised one.

        Call this on every advertisement so reconnects keep using the current
        adapter/proxy route instead of a stale one.
        """
        if not isinstance(ble_device, BLEDevice):
            raise TypeError(
                "set_ble_device requires a bleak BLEDevice, got "
                f"{type(ble_device).__name__}"
            )
        self._ble_device = ble_device
        self.address = ble_device.address

    @property
    def ble_device(self) -> BLEDevice | None:
        """Return the BLEDevice the next connection will use."""
        return self._ble_device

    async def pair(self, *, replace_bond: bool = False) -> bool:
        """Connect, bond, then subscribe to notifications.

        The RX characteristic's CCCD is encrypted. Enabling notifications
        before BlueZ has a bond makes the shade drop the link (ATT 0x0e).
        Bond immediately after the GATT connect, then subscribe. On Linux a
        temporary BlueZ Agent1 is registered on the Bleak client bus around
        ``Pair`` so it can answer the yes/no prompt without becoming the host
        default agent.

        Returns True on success. A rejected bond raises
        ``RyseAuthenticationFailed``. A timeout, disconnect, or other
        transport error is logged at debug and re-raised.

        ``replace_bond`` removes the BlueZ device before pairing. Bleak's
        ``pair()`` returns immediately when BlueZ already reports the device
        as paired, which leaves a reset shade with a stale bond.
        """
        if not self._ble_device and not self.address:
            _LOGGER.debug("No BLEDevice or address provided for pairing.")
            return False
        _LOGGER.debug("Pairing with device %s", self.address)
        try:
            await self._connect()
            if not self.client or not self.client.is_connected:
                raise BleakError(f"Could not connect to {self.address}")
            if replace_bond:
                await self._remove_stale_bond()
            await self._bond()
            if not self.client or not self.client.is_connected:
                raise BleakError(
                    f"Lost connection to {self.address} during pairing"
                )
            try:
                await self.client.start_notify(
                    self.rx_uuid, self._notification_handler
                )
            except _CONNECTION_ERRORS as err:
                if _is_bond_rejected(err):
                    raise RyseAuthenticationFailed(str(err)) from err
                raise
            _LOGGER.debug("Successfully paired with %s", self.address)
            return True
        except RyseAuthenticationFailed:
            _LOGGER.debug("Bond rejected for %s", self.address)
            await self.unpair()
            raise
        except _CONNECTION_ERRORS as err:
            await self.unpair()
            if _is_bond_rejected(err):
                _LOGGER.debug("Bond rejected for %s: %s", self.address, err)
                raise RyseAuthenticationFailed(str(err)) from err
            _LOGGER.debug("Could not pair with %s: %s", self.address, err)
            raise
        except Exception:
            _LOGGER.exception("Unexpected error pairing with %s", self.address)
            await self.unpair()
            raise

    def _forward_disconnect(self, client) -> None:
        """Forward a GATT disconnect to the callback registered by the caller."""
        callback = self.disconnected_callback
        if callable(callback):
            callback(client)

    async def _connect(self) -> None:
        """Establish a GATT connection, preferring bleak-retry-connector."""
        if self._ble_device is not None:
            try:
                from bleak_retry_connector import (
                    BleakClientWithServiceCache,
                    establish_connection,
                )
            except ImportError:
                self.client = BleakClient(
                    self._ble_device, disconnected_callback=self._forward_disconnect
                )
                await self.client.connect(timeout=30.0)
                return
            self.client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self.address or "RYSE",
                disconnected_callback=self._forward_disconnect,
                ble_device_callback=lambda: self._ble_device,
                max_attempts=DEFAULT_CONNECT_ATTEMPTS,
            )
            return

        self.client = BleakClient(
            self.address, disconnected_callback=self._forward_disconnect
        )
        await self.client.connect(timeout=30.0)

    async def _remove_stale_bond(self) -> None:
        """Drop BlueZ's device record so the next ``Pair`` is not a no-op."""
        client = self.client
        if client is None:
            return
        remove = getattr(client, "unpair", None)
        if not callable(remove):
            return
        try:
            await remove()
        except _CONNECTION_ERRORS as err:
            _LOGGER.warning(
                "Could not remove the existing bond for %s: %s", self.address, err
            )
        last_error: BaseException | None = None
        for attempt in range(1, BOND_REMOVE_RECONNECT_ATTEMPTS + 1):
            try:
                await self._connect()
            except _CONNECTION_ERRORS as err:
                last_error = err
            else:
                if self.client and self.client.is_connected:
                    return
                last_error = BleakError(
                    f"Could not reconnect to {self.address} after removing the bond"
                )
            if attempt == BOND_REMOVE_RECONNECT_ATTEMPTS:
                break
            _LOGGER.debug(
                "Reconnect to %s after removing the bond failed (%s); retry %s/%s",
                self.address,
                last_error,
                attempt,
                BOND_REMOVE_RECONNECT_ATTEMPTS,
            )
            await asyncio.sleep(BOND_RETRY_DELAY)
        assert last_error is not None
        raise last_error

    async def _bond(self) -> None:
        """Establish an OS-level bond, retrying AuthenticationFailed."""
        last_error: BaseException | None = None
        for attempt in range(1, BOND_RETRIES + 2):
            try:
                async with auto_confirm_pairing_agent(
                    self.address, bleak_client_dbus_bus(self.client)
                ):
                    await self.client.pair()
                return
            except _CONNECTION_ERRORS as err:
                if not _is_bond_rejected(err):
                    raise
                last_error = err
                if attempt > BOND_RETRIES:
                    break
                _LOGGER.debug(
                    "Bonding with %s failed (%s); retry %s/%s",
                    self.address,
                    err,
                    attempt,
                    BOND_RETRIES,
                )
                await self.unpair()
                await asyncio.sleep(BOND_RETRY_DELAY)
                await self._connect()
                if not self.client or not self.client.is_connected:
                    raise BleakError(
                        f"Lost connection to {self.address} during pairing retry"
                    )
        assert last_error is not None
        raise last_error

    async def _notification_handler(self, sender, data):
        """Callback function for handling received BLE notifications."""
        if len(data) >= 5 and data[0] == 0xF5 and data[2] == 0x01 and data[3] == 0x18:
            # ignore REPORT USER TARGET data
            return
        _LOGGER.debug("Received notification")
        if len(data) >= 5 and data[0] == 0xF5 and data[2] == 0x01 and data[3] == 0x07:
            new_position = data[4]  # Extract the position byte
            _LOGGER.debug(
                "Received valid notification, updating position: %d",
                new_position,
            )

            callback = getattr(self, "update_callback", None)
            if callable(callback):
                await callback(new_position)

    async def get_device_info(self):
        if self.client:
            try:
                manufacturer_data = self.client.services
                _LOGGER.debug("Getting Manufacturer Data")
                return manufacturer_data
            except Exception as e:
                _LOGGER.error("Failed to get device info: %s", e)
        return None

    async def unpair(self):
        client = self.client
        self.client = None
        if client is None:
            return
        try:
            await client.disconnect()
        except _CONNECTION_ERRORS as err:
            _LOGGER.debug("Error disconnecting from %s: %s", self.address, err)
        _LOGGER.debug("Device disconnected")

    async def read_data(self):
        if self.client:
            data = await self.client.read_gatt_char(self.rx_uuid)
            if len(data) < 5 or data[0] != 0xF5 or data[2] != 0x01 or data[3] != 0x18:
                # ignore REPORT USER TARGET data
                _LOGGER.debug("Received Position Report Data")
                return data
            return None

    async def write_data(self, data):
        if self.client:
            await self.client.write_gatt_char(self.tx_uuid, data)
            _LOGGER.debug("Sending data to tx uuid")

    async def send_set_position(self, position):
        pdata = build_position_packet(position)
        await self.write_data(pdata)

    async def send_get_position(self):
        bytesinfo = build_get_position_packet()
        await self.write_data(bytesinfo)

    async def scan_and_pair(self):
        _LOGGER.debug("Scanning for BLE devices...")
        devices = await BleakScanner.discover()
        for device in devices:
            _LOGGER.debug(
                "Found device: %s (%s)",
                device.name,
                device.address,
            )
            if device.name and "target-device-name" in device.name.lower():
                _LOGGER.debug(
                    "Attempting to pair with %s (%s)",
                    device.name,
                    device.address,
                )
                self.address = device.address
                return await self.pair()
        _LOGGER.warning("No suitable devices found to pair")
        return False

    def is_valid_position(self, position):
        return (0 <= position <= 100)
    
    def get_real_position(self, position):
        return (100 - position)
    
    def is_closed(self, position):
        return (position == 100)

    async def send_open(self):
        await self.send_set_position(0)

    async def send_close(self):
        await self.send_set_position(100)
