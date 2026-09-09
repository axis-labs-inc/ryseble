"""Identify RYSE pairing state from advertisement data."""

from __future__ import annotations

from collections.abc import Mapping

from .constants import MANUFACTURER_ID, PAIRING_MODE_FLAG


def is_pairing_mode(manufacturer_data: Mapping[int, bytes] | None) -> bool:
    """Return True if the shade is advertising that it accepts a new pairing.

    RYSE devices set :data:`~ryseble.constants.PAIRING_MODE_FLAG` in the first
    byte of their manufacturer data while the user holds the pairing button.
    """
    if not manufacturer_data:
        return False
    payload = manufacturer_data.get(MANUFACTURER_ID)
    if not payload:
        return False
    return bool(payload[0] & PAIRING_MODE_FLAG)
