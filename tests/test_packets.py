"""Tests for the RYSE wire protocol."""

from __future__ import annotations

import pytest

from ryseble.packets import (
    build_get_position_packet,
    build_position_packet,
    is_valid_position,
    parse_notification,
)


def test_build_position_packet() -> None:
    assert build_position_packet(50) == bytes([0xF5, 0x03, 0x01, 0x01, 0x32, 0x34])


def test_build_position_packet_checksum_wraps() -> None:
    packet = build_position_packet(100)
    assert packet[-1] == sum(packet[2:-1]) % 256


@pytest.mark.parametrize("position", [-1, 101, 255])
def test_build_position_packet_rejects_out_of_range(position: int) -> None:
    with pytest.raises(ValueError, match="position must be between"):
        build_position_packet(position)


def test_build_get_position_packet() -> None:
    assert build_get_position_packet() == bytes([0xF5, 0x02, 0x01, 0x03, 0x04])


def test_parse_notification_returns_position() -> None:
    assert parse_notification(bytes([0xF5, 0x03, 0x01, 0x07, 0x2A, 0x32])) == 42


def test_parse_notification_ignores_user_target_report() -> None:
    assert parse_notification(bytes([0xF5, 0x03, 0x01, 0x18, 0x2A, 0x43])) is None


@pytest.mark.parametrize(
    "frame",
    [
        bytes([0xF5, 0x03, 0x01, 0x07]),  # truncated, no position byte
        bytes([0xA0, 0x03, 0x01, 0x07, 0x2A, 0x32]),  # wrong header
        bytes([0xF5, 0x03, 0x02, 0x07, 0x2A, 0x32]),  # wrong group
        bytes([0xF5, 0x03, 0x01, 0x07, 0xFF, 0x32]),  # position out of range
        b"",
    ],
)
def test_parse_notification_ignores_invalid_frames(frame: bytes) -> None:
    assert parse_notification(frame) is None


@pytest.mark.parametrize(
    ("position", "expected"), [(0, True), (100, True), (-1, False), (101, False)]
)
def test_is_valid_position(position: int, expected: bool) -> None:
    assert is_valid_position(position) is expected
