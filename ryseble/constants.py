# Hardcoded UUIDs used by Ryse devices
HARDCODED_UUIDS = {
    "rx_uuid": "a72f2801-b0bd-498b-b4cd-4a3901388238",
    "tx_uuid": "a72f2802-b0bd-498b-b4cd-4a3901388238",
}

# Bluetooth SIG company identifier advertised by RYSE devices (0x0409).
MANUFACTURER_ID = 0x0409

# Bit set in the first manufacturer data byte while the device accepts pairing.
PAIRING_MODE_FLAG = 0x40

# BlueZ AuthenticationFailed is common until a confirming Agent1 is in place.
BOND_RETRIES = 5
BOND_RETRY_DELAY = 1.0
# BlueZ deletes the device object in RemoveDevice. The next connect can raise
# UnknownObject until a new advertisement recreates it.
BOND_REMOVE_RECONNECT_ATTEMPTS = 3
DEFAULT_CONNECT_ATTEMPTS = 4
