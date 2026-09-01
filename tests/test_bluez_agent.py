"""Tests for the Linux BlueZ auto-confirm pairing agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ryseble.bluez_agent import (
    AGENT_CAPABILITY,
    AGENT_PATH,
    AutoConfirmAgent,
    _AgentSession,
    _build_interface,
    auto_confirm_pairing_agent,
)


def test_build_interface() -> None:
    pytest.importorskip("dbus_fast")
    interface = _build_interface()
    assert interface.name == "org.bluez.Agent1"


def test_auto_confirm_agent_accepts_yes_no() -> None:
    agent = AutoConfirmAgent(interface=object())
    agent.release()
    agent.cancel()
    agent.request_confirmation("/org/bluez/hci0/dev_AA", 123456)
    agent.request_authorization("/org/bluez/hci0/dev_AA")
    agent.authorize_service(
        "/org/bluez/hci0/dev_AA", "00001800-0000-1000-8000-00805f9b34fb"
    )
    agent.display_pin_code("/org/bluez/hci0/dev_AA", "0000")
    agent.display_passkey("/org/bluez/hci0/dev_AA", 123456, 6)
    assert agent.request_pin_code("/org/bluez/hci0/dev_AA") == "0000"
    assert agent.request_passkey("/org/bluez/hci0/dev_AA") == 0


async def test_agent_is_noop_off_linux() -> None:
    with patch("ryseble.bluez_agent.sys.platform", "darwin"):
        async with auto_confirm_pairing_agent():
            pass


async def test_agent_registers_and_unregisters_on_linux() -> None:
    session = MagicMock()
    session.stop = AsyncMock()
    with (
        patch("ryseble.bluez_agent.sys.platform", "linux"),
        patch(
            "ryseble.bluez_agent._AgentSession.start",
            AsyncMock(return_value=session),
        ),
    ):
        async with auto_confirm_pairing_agent():
            pass
    session.stop.assert_awaited_once()


async def test_agent_yields_if_registration_fails() -> None:
    ran = False
    with (
        patch("ryseble.bluez_agent.sys.platform", "linux"),
        patch(
            "ryseble.bluez_agent._AgentSession.start",
            AsyncMock(side_effect=RuntimeError("no dbus")),
        ),
    ):
        async with auto_confirm_pairing_agent():
            ran = True
    assert ran is True


async def test_agent_session_register_order() -> None:
    pytest.importorskip("dbus_fast")
    bus = MagicMock()
    bus.export = MagicMock()
    bus.unexport = MagicMock()
    bus.disconnect = MagicMock()
    message_bus = MagicMock()
    message_bus.return_value.connect = AsyncMock(return_value=bus)
    bluez_call = AsyncMock()

    with (
        patch("dbus_fast.aio.MessageBus", message_bus),
        patch("dbus_fast.constants.BusType"),
        patch("ryseble.bluez_agent._build_interface", return_value=object()),
        patch("ryseble.bluez_agent._bluez_call", bluez_call),
    ):
        session = await _AgentSession.start()
        await session.stop()

    members = [call.args[1] for call in bluez_call.await_args_list]
    assert members[:2] == ["RegisterAgent", "RequestDefaultAgent"]
    assert members[-1] == "UnregisterAgent"
    assert bluez_call.await_args_list[0].args[3] == [AGENT_PATH, AGENT_CAPABILITY]
    bus.export.assert_called_once()
    assert bus.export.call_args.args[0] == AGENT_PATH
