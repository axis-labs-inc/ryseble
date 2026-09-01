"""Protocol constants for RYSE BLE devices."""

RYSE_SERVICE_UUID = "a72f2800-b0bd-498b-b4cd-4a3901388238"
RX_CHAR_UUID = "a72f2801-b0bd-498b-b4cd-4a3901388238"
TX_CHAR_UUID = "a72f2802-b0bd-498b-b4cd-4a3901388238"

HARDCODED_UUIDS = {
    "rx_uuid": RX_CHAR_UUID,
    "tx_uuid": TX_CHAR_UUID,
}

# Bluetooth SIG company identifier advertised by RYSE devices (0x0409).
MANUFACTURER_ID = 0x0409
MANUFACTURER_NAME = "RYSE"

# Bit set in the first manufacturer data byte while the device accepts pairing.
PAIRING_MODE_FLAG = 0x40

PACKET_HEADER = 0xF5
GROUP_ID = 0x01

CMD_SET_POSITION = 0x01
CMD_GET_POSITION = 0x03
CMD_POSITION_REPORT = 0x07
CMD_USER_TARGET_REPORT = 0x18

MIN_POSITION = 0
MAX_POSITION = 100

DEFAULT_CONNECT_ATTEMPTS = 4
DEFAULT_COMMAND_TIMEOUT = 10.0
# BlueZ often fails the first Pair() with AuthenticationFailed even when the
# auto-confirm agent is already registered. Retry the bond after a short pause.
BOND_RETRIES = 5
BOND_RETRY_DELAY = 1.0
