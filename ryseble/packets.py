"""Build and parse the RYSE BLE wire protocol.

Every frame is ``F5 <length> <group> <command> [payload...] <checksum>`` where the
checksum is the sum of all bytes from the group byte onward, modulo 256.
"""

from __future__ import annotations

from .constants import (
    CMD_GET_POSITION,
    CMD_POSITION_REPORT,
    CMD_SET_POSITION,
    GROUP_ID,
    MAX_POSITION,
    MIN_POSITION,
    PACKET_HEADER,
)

_HEADER_LENGTH = 4


def _with_checksum(data: bytes) -> bytes:
    """Append the protocol checksum to a frame."""
    return data + bytes([sum(data[2:]) % 256])


def build_position_packet(pos: int) -> bytes:
    """Build the frame that moves the shade to ``pos``."""
    if not MIN_POSITION <= pos <= MAX_POSITION:
        raise ValueError(
            f"position must be between {MIN_POSITION} and {MAX_POSITION}, got {pos}"
        )

    return _with_checksum(bytes([PACKET_HEADER, 0x03, GROUP_ID, CMD_SET_POSITION, pos]))


def build_get_position_packet() -> bytes:
    """Build the frame that asks the device to report its current position."""
    return _with_checksum(bytes([PACKET_HEADER, 0x02, GROUP_ID, CMD_GET_POSITION]))


def is_valid_position(position: int) -> bool:
    """Return True if ``position`` is within the device's range."""
    return MIN_POSITION <= position <= MAX_POSITION


def parse_notification(data: bytes) -> int | None:
    """Return the position carried by a notification, or None if it carries none.

    Frames other than a position report are ignored, including the user target
    report the device emits while the user drives the shade by hand.
    """
    if len(data) <= _HEADER_LENGTH:
        return None
    if data[0] != PACKET_HEADER or data[2] != GROUP_ID:
        return None
    if data[3] != CMD_POSITION_REPORT:
        return None

    position = data[4]
    if not is_valid_position(position):
        return None
    return position
