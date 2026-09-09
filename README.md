
# ryseble

A small Python library to interact with RYSE BLE Smart Shade devices. Designed to be
used by Home Assistant integrations but reusable in other projects.

## Features
- Pair/connect/disconnect with Bleak (BlueZ Agent1 on Linux to confirm bonding)
- Read/write raw packets
- Helpers to build position/get-position packets

## Installation

```bash
pip install ryseble
```

## Usage (example)

```python
from ryseble.device import RyseBLEDevice
from ryseble.packets import build_position_packet, build_get_position_packet

device = RyseBLEDevice(address="AA:BB:CC:DD:EE:FF", rx_uuid="...", tx_uuid="...")
await device.pair()
await device.write_data(build_position_packet(50))
```
