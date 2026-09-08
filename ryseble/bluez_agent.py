"""Temporary BlueZ pairing agent that auto-confirms Just Works / yes-no.

Released Bleak does not register an ``org.bluez.Agent1``, so ``client.pair()``
fails with ``AuthenticationFailed`` on devices that ask for confirmation.
This module fills that gap on Linux only: it registers a DisplayYesNo agent
for the duration of connect+pair, then unregisters it.

ESPHome/Shelly proxies, macOS and Windows never hit this code.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

_LOGGER = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
AGENT_MANAGER_PATH = "/org/bluez"
AGENT_MANAGER_INTERFACE = "org.bluez.AgentManager1"
AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_PATH = "/com/ryseble/agent"
AGENT_CAPABILITY = "DisplayYesNo"


class AutoConfirmAgent:
    """BlueZ Agent1 that accepts pairing without prompting."""

    def __init__(self, interface: Any) -> None:
        self._interface = interface

    def release(self) -> None:
        _LOGGER.debug("BlueZ agent Release")

    def cancel(self) -> None:
        _LOGGER.debug("BlueZ agent Cancel")

    def request_confirmation(self, device: str, passkey: int) -> None:
        _LOGGER.debug(
            "BlueZ RequestConfirmation %s passkey=%s (auto-confirm)",
            device,
            passkey,
        )

    def request_authorization(self, device: str) -> None:
        _LOGGER.debug("BlueZ RequestAuthorization %s (auto-confirm)", device)

    def authorize_service(self, device: str, uuid: str) -> None:
        _LOGGER.debug(
            "BlueZ AuthorizeService %s uuid=%s (auto-confirm)", device, uuid
        )

    def display_pin_code(self, device: str, pincode: str) -> None:
        _LOGGER.debug("BlueZ DisplayPinCode %s pin=%s", device, pincode)

    def display_passkey(self, device: str, passkey: int, entered: int) -> None:
        _LOGGER.debug(
            "BlueZ DisplayPasskey %s passkey=%s entered=%s",
            device,
            passkey,
            entered,
        )

    def request_pin_code(self, device: str) -> str:
        _LOGGER.debug("BlueZ RequestPinCode %s (auto-confirm 0000)", device)
        return "0000"

    def request_passkey(self, device: str) -> int:
        _LOGGER.debug("BlueZ RequestPasskey %s (auto-confirm 0)", device)
        return 0


def _build_interface() -> Any:
    """Create a dbus-fast ServiceInterface bound to :class:`AutoConfirmAgent`."""
    from dbus_fast.service import ServiceInterface, method

    class AgentInterface(ServiceInterface):
        def __init__(self) -> None:
            super().__init__(AGENT_INTERFACE)
            self.agent = AutoConfirmAgent(self)

        @method()
        def Release(self):  # noqa: N802
            self.agent.release()

        @method()
        def Cancel(self):  # noqa: N802
            self.agent.cancel()

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821,N802
            self.agent.request_confirmation(device, passkey)

        @method()
        def RequestAuthorization(self, device: "o"):  # noqa: F821,N802
            self.agent.request_authorization(device)

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821,N802
            self.agent.authorize_service(device, uuid)

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821,N802
            self.agent.display_pin_code(device, pincode)

        @method()
        def DisplayPasskey(  # noqa: N802
            self, device: "o", passkey: "u", entered: "q"  # noqa: F821
        ):
            self.agent.display_passkey(device, passkey, entered)

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # noqa: F821,N802
            return self.agent.request_pin_code(device)

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # noqa: F821,N802
            return self.agent.request_passkey(device)

    return AgentInterface()


async def _bluez_call(bus: Any, member: str, signature: str, body: list[Any]) -> None:
    from dbus_fast import Message, MessageType

    reply = await bus.call(
        Message(
            destination=BLUEZ_SERVICE,
            path=AGENT_MANAGER_PATH,
            interface=AGENT_MANAGER_INTERFACE,
            member=member,
            signature=signature,
            body=body,
        )
    )
    if reply is None or reply.message_type == MessageType.ERROR:
        error_name = getattr(reply, "error_name", None) if reply else "no reply"
        error_text = reply.body[0] if reply and reply.body else ""
        raise RuntimeError(f"{member} failed: {error_name} {error_text}".strip())


class _AgentSession:
    """Registered BlueZ agent living on its own system-bus connection."""

    def __init__(self, bus: Any, interface: Any) -> None:
        self._bus = bus
        self._interface = interface

    @classmethod
    async def start(cls) -> _AgentSession:
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import BusType

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        interface = _build_interface()
        bus.export(AGENT_PATH, interface)
        session = cls(bus, interface)
        try:
            await session._register()
        except Exception:
            await session.stop()
            raise
        return session

    async def _register(self) -> None:
        try:
            await _bluez_call(self._bus, "RegisterAgent", "os", [AGENT_PATH, AGENT_CAPABILITY])
        except RuntimeError as err:
            if "AlreadyExists" not in str(err):
                raise
            await _bluez_call(self._bus, "UnregisterAgent", "o", [AGENT_PATH])
            await _bluez_call(self._bus, "RegisterAgent", "os", [AGENT_PATH, AGENT_CAPABILITY])
        await _bluez_call(self._bus, "RequestDefaultAgent", "o", [AGENT_PATH])
        _LOGGER.debug("Registered BlueZ auto-confirm agent at %s", AGENT_PATH)

    async def stop(self) -> None:
        try:
            await _bluez_call(self._bus, "UnregisterAgent", "o", [AGENT_PATH])
        except Exception as err:  # best-effort teardown
            _LOGGER.debug("UnregisterAgent failed: %s", err)
        try:
            self._bus.unexport(AGENT_PATH)
        except Exception:
            pass
        try:
            self._bus.disconnect()
        except Exception:
            pass


@asynccontextmanager
async def auto_confirm_pairing_agent() -> AsyncIterator[None]:
    """Register a confirming BlueZ agent around a ``Pair`` call.

    On non-Linux platforms, or if dbus-fast / BlueZ is unavailable, this is a
    no-op so pairing can still be attempted with the host stack's own agent.
    """
    if sys.platform != "linux":
        yield
        return

    session: _AgentSession | None = None
    try:
        session = await _AgentSession.start()
    except Exception as err:
        _LOGGER.warning(
            "Could not register a BlueZ pairing agent; Pair may fail without "
            "a yes/no confirmation: %s",
            err,
        )

    try:
        yield
    finally:
        if session is not None:
            await session.stop()
