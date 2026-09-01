# ryseble

An async Python library for RYSE BLE Smart Shade devices. Designed for the
Home Assistant `ryse` integration but usable from any asyncio project.

## Features

- Pure asyncio, built on [Bleak](https://github.com/hbldh/bleak) and
  [bleak-retry-connector](https://github.com/Bluetooth-Devices/bleak-retry-connector)
- No blocking I/O and no external processes, so it is safe to call from the
  Home Assistant event loop
- Works with local adapters, ESPHome/Shelly Bluetooth proxies, macOS and Windows
- Pairing mode is detected from advertisement data, so no BlueZ-specific tooling
- Position updates delivered over GATT notifications

## Requirements

Python 3.11 or newer. Linux is not required; there is no dependency on BlueZ or
`bluetoothctl`.

## Installation

```bash
pip install ryseble
```

## Usage

### Inside Home Assistant

Always hand the library a `BLEDevice` obtained from the `bluetooth` component.
Passing a bare address would make Bleak run its own scan, which fails on
Bluetooth proxies and on setups without a local adapter.

```python
from homeassistant.components import bluetooth
from ryseble import RyseBLEDevice, is_pairing_mode

ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
if ble_device is None:
    raise ConfigEntryNotReady(f"Could not find RYSE device with address {address}")

device = RyseBLEDevice(ble_device)
await device.connect()

unsubscribe = device.register_position_callback(handle_position)
await device.send_set_position(50)
```

Refresh the route whenever a new advertisement arrives, because a shade can move
between Bluetooth proxies:

```python
device.set_ble_device(service_info.device)
```

To find shades that are currently in pairing mode, use the advertisements Home
Assistant already collected:

```python
from ryseble import filter_ryse_devices_pairing

candidates = bluetooth.async_discovered_service_info(hass, connectable=True)
options = filter_ryse_devices_pairing(candidates, existing_addresses=configured)
```

### Standalone scripts

Outside Home Assistant you may scan yourself:

```python
import asyncio
from ryseble import RyseBLEDevice
from ryseble.discovery import async_discover_ryse_devices


async def main() -> None:
    devices = await async_discover_ryse_devices(pairing_only=True)
    for address, (ble_device, advertisement) in devices.items():
        shade = RyseBLEDevice(ble_device)
        await shade.pair()
        await shade.send_open()
        await shade.disconnect()


asyncio.run(main())
```

## API

| Member | Purpose |
| --- | --- |
| `RyseBLEDevice.connect(pair=False)` | Connect and subscribe to notifications |
| `RyseBLEDevice.pair()` | Connect, bond (Linux auto-confirms BlueZ yes/no), then subscribe |
| `RyseBLEDevice.disconnect()` | Drop the connection |
| `RyseBLEDevice.set_ble_device(ble_device)` | Update the route from a new advertisement |
| `RyseBLEDevice.register_position_callback(cb)` | Subscribe to position reports; returns an unsubscribe callable |
| `RyseBLEDevice.register_disconnected_callback(cb)` | Subscribe to disconnections |
| `RyseBLEDevice.send_open()` / `send_close()` / `send_set_position(pos)` | Move the shade |
| `RyseBLEDevice.send_get_position()` | Ask the device to report its position |
| `is_pairing_mode(manufacturer_data)` | True while the device accepts a new pairing |
| `is_ryse_advertisement(...)` | True if an advertisement came from a RYSE device |
| `filter_ryse_devices_pairing(advertisements, existing_addresses)` | Pairing-mode devices as `{address: label}` |

Position callbacks may be either sync or async.

## Migrating from 1.x

Version 2.0 removes the `bluetoothctl` module. Everything it did by shelling out
to the BlueZ CLI is now done through Bleak, which is what makes proxy and
non-Linux support possible.

| 1.x | 2.x |
| --- | --- |
| `RyseBLEDevice(address)` | `RyseBLEDevice(ble_device)` with a Bleak `BLEDevice` |
| `device.pair()` (connect only) | `device.connect()`, or `device.pair()` to also bond |
| `device.unpair()` | `device.disconnect()` |
| `device.update_callback = cb` | `device.register_position_callback(cb)` |
| `device.scan_and_pair()` | `ryseble.discovery.async_discover_ryse_devices()` |
| `bluetoothctl.pair_with_ble_device(name, address)` | `RyseBLEDevice(ble_device).pair()` |
| `bluetoothctl.is_pairing_ryse_device(address)` | `is_pairing_mode(advertisement.manufacturer_data)` |
| `bluetoothctl.filter_ryse_devices_pairing(...)` (async) | `filter_ryse_devices_pairing(...)` (sync) |
| `bluetoothctl.is_device_connected(address)` | `device.is_connected` |

## Development

```bash
pip install -e ".[test]"
pytest
```
