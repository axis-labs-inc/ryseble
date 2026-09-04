"""RyseBLEDevice: async wrapper around Bleak for RYSE devices.

This module intentionally keeps responsibilities small: connect/disconnect,
read/write GATT characteristics, and deliver notifications via a callback.
"""

from __future__ import annotations

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from .constants import HARDCODED_UUIDS
from .packets import build_position_packet, build_get_position_packet
import logging

_LOGGER = logging.getLogger(__name__)


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

    async def pair(self):
        """Connect to the device and subscribe to notifications."""
        target = self._ble_device or self.address
        if not target:
            _LOGGER.error("No BLEDevice or address provided for pairing.")
            return False
        _LOGGER.debug(
            "Pairing with device %s",
            self.address,
        )
        # Prefer the resolved BLEDevice so Bleak/habluetooth can route through
        # the Home Assistant-selected adapter or ESPHome/Shelly proxy.
        self.client = BleakClient(target)
        try:
            await self.client.connect(timeout=30.0)
            if self.client.is_connected:
                _LOGGER.debug(
                    "Successfully paired with %s",
                    self.address,
                )
                # Subscribe to notifications
                await self.client.start_notify(self.rx_uuid, self._notification_handler)
                return True
        except Exception as e:
            _LOGGER.error(
                "Error pairing with device %s: %s",
                self.address,
                e,
            )
        return False

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

            # Notify cover.py about the position update
            if hasattr(self, "update_callback"):
                await self.update_callback(new_position)

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
        if self.client:
            await self.client.disconnect()
            _LOGGER.debug("Device disconnected")
            self.client = None

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
