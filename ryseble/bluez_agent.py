"""Temporary BlueZ pairing agent that auto-confirms Just Works / yes-no.

Released Bleak does not register an ``org.bluez.Agent1``, so ``client.pair()``
fails with ``AuthenticationFailed`` on devices that ask for confirmation.
This module fills that gap on Linux only: it registers a DisplayYesNo agent
on the Bleak client's D-Bus connection (the same unique name that sends
``Device.Pair``), then unregisters it.

The agent is *not* made the BlueZ default. BlueZ uses the caller's registered
agent for Pair, so GNOME/bluetoothctl and other D-Bus connections keep the
host default. Confirmation handlers still reject requests whose BlueZ object
path is not the intended RYSE device.

ESPHome/Shelly proxies, macOS and Windows never hit this code.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

_LOGGER = logging.getLogger(__name__)

# BlueZ allows only one agent per D-Bus unique name. Serialize RYSE pairing
# so two shades cannot race RegisterAgent on a reused client bus.
_agent_lock: asyncio.Lock | None = None

BLUEZ_SERVICE = "org.bluez"
AGENT_MANAGER_PATH = "/org/bluez"
AGENT_MANAGER_INTERFACE = "org.bluez.AgentManager1"
AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_PATH = "/com/ryseble/agent"
AGENT_CAPABILITY = "DisplayYesNo"
_REJECTED = "org.bluez.Error.Rejected"
_REJECTED_TEXT = "Not the intended RYSE device"


def _get_agent_lock() -> asyncio.Lock:
    """Return the process-wide pairing lock, creating it on first use."""
    global _agent_lock
    if _agent_lock is None:
        _agent_lock = asyncio.Lock()
    return _agent_lock


def bluez_device_path_matches(device_path: str, address: str) -> bool:
    """Return True if *device_path* is the BlueZ object for *address*.

    BlueZ device paths look like ``/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF``.
    The adapter segment is ignored so a second controller still matches.
    """
    if not device_path or not address:
        return False
    expected = "dev_" + address.replace("-", ":").replace(":", "_").upper()
    last = device_path.rstrip("/").rsplit("/", 1)[-1].upper()
    return last == expected


class AutoConfirmAgent:
    """BlueZ Agent1 that accepts pairing only for one MAC address."""

    def __init__(self, address: str) -> None:
        self._address = address

    def allows(self, device: str) -> bool:
        """Return True if *device* is the BlueZ path for the intended shade."""
        allowed = bluez_device_path_matches(device, self._address)
        if not allowed:
            _LOGGER.warning(
                "Rejected BlueZ pairing request for %s (expected %s)",
                device,
                self._address,
            )
        return allowed

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


def _build_interface(address: str) -> Any:
    """Create a dbus-fast ServiceInterface bound to :class:`AutoConfirmAgent`."""
    from dbus_fast.errors import DBusError
    from dbus_fast.service import ServiceInterface, method

    class AgentInterface(ServiceInterface):
        def __init__(self) -> None:
            super().__init__(AGENT_INTERFACE)
            self.agent = AutoConfirmAgent(address)

        def _reject_unless_expected(self, device: str) -> None:
            if not self.agent.allows(device):
                raise DBusError(_REJECTED, _REJECTED_TEXT)

        @method()
        def Release(self):  # noqa: N802
            self.agent.release()

        @method()
        def Cancel(self):  # noqa: N802
            self.agent.cancel()

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: F821,N802
            self._reject_unless_expected(device)
            self.agent.request_confirmation(device, passkey)

        @method()
        def RequestAuthorization(self, device: "o"):  # noqa: F821,N802
            self._reject_unless_expected(device)
            self.agent.request_authorization(device)

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: F821,N802
            self._reject_unless_expected(device)
            self.agent.authorize_service(device, uuid)

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: F821,N802
            self._reject_unless_expected(device)
            self.agent.display_pin_code(device, pincode)

        @method()
        def DisplayPasskey(  # noqa: N802
            self, device: "o", passkey: "u", entered: "q"  # noqa: F821
        ):
            # DisplayPasskey cannot return Rejected; ignore other devices.
            if not self.agent.allows(device):
                return
            self.agent.display_passkey(device, passkey, entered)

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # noqa: F821,N802
            self._reject_unless_expected(device)
            return self.agent.request_pin_code(device)

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # noqa: F821,N802
            self._reject_unless_expected(device)
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


def bleak_client_dbus_bus(client: Any) -> Any | None:
    """Return the D-Bus connection Bleak uses for ``Device.Pair``, if any.

    Bleak opens a per-connection ``MessageBus`` on the BlueZ backend. Pair
    is sent on that unique name, so the agent must be registered there rather
    than on the global manager bus or as the host default.
    """
    if client is None:
        return None
    backend = getattr(client, "_backend", client)
    bus = getattr(backend, "_bus", None)
    if bus is None:
        bus = getattr(client, "_bus", None)
    if bus is None or not getattr(bus, "connected", False):
        return None
    return bus


class _AgentSession:
    """Registered BlueZ agent on a Bleak client's D-Bus connection."""

    def __init__(self, bus: Any, interface: Any) -> None:
        self._bus = bus
        self._interface = interface

    @classmethod
    async def start(cls, address: str, bus: Any) -> _AgentSession:
        if bus is None or not getattr(bus, "connected", False):
            raise RuntimeError("Bleak client has no D-Bus connection")

        interface = _build_interface(address)
        try:
            bus.unexport(AGENT_PATH)
        except Exception:
            pass
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
            await _bluez_call(
                self._bus, "RegisterAgent", "os", [AGENT_PATH, AGENT_CAPABILITY]
            )
        except RuntimeError as err:
            if "AlreadyExists" not in str(err):
                raise
            await _bluez_call(self._bus, "UnregisterAgent", "o", [AGENT_PATH])
            await _bluez_call(
                self._bus, "RegisterAgent", "os", [AGENT_PATH, AGENT_CAPABILITY]
            )
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


@asynccontextmanager
async def auto_confirm_pairing_agent(
    address: str | None, bus: Any | None = None
) -> AsyncIterator[None]:
    """Register a confirming BlueZ agent around a ``Pair`` call.

    On non-Linux platforms, or if *bus* is missing, this is a no-op so pairing
    can still be attempted with the host stack's own agent.

    Register on the Bleak client bus so ``Device.Pair`` is confirmed without
    calling RequestDefaultAgent. Hold a shared lock because BlueZ allows only
    one agent per D-Bus unique name.
    """
    async with _get_agent_lock():
        if sys.platform != "linux" or not address:
            yield
            return

        if bus is None:
            _LOGGER.warning(
                "Could not find Bleak's D-Bus connection; Pair may fail without "
                "a yes/no confirmation"
            )
            yield
            return

        session: _AgentSession | None = None
        try:
            session = await _AgentSession.start(address, bus)
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
