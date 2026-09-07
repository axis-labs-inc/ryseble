"""Async wrappers around bluetoothctl for RYSE pairing helpers.

All process management uses asyncio subprocess APIs so callers can safely
await these helpers from Home Assistant's event loop without blocking it.
"""

from __future__ import annotations

import asyncio
import logging
import re

_LOGGER = logging.getLogger(__name__)

_PROCESS_EXIT_TIMEOUT = 5.0
_COMMAND_TIMEOUT = 10.0


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """Cancel-safe shutdown of a bluetoothctl process."""
    if process.returncode is not None:
        return

    if process.stdin and not process.stdin.is_closing():
        process.stdin.close()
        try:
            await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass

    try:
        await asyncio.wait_for(process.wait(), timeout=_PROCESS_EXIT_TIMEOUT)
        return
    except asyncio.TimeoutError:
        pass

    process.kill()
    await process.wait()


async def run_command(command: str) -> dict[str, object]:
    """Run a one-shot bluetoothctl command and return the output."""
    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl",
        *command.split(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_COMMAND_TIMEOUT
        )
        return {"stdout": stdout, "stderr": stderr, "returncode": proc.returncode}
    except asyncio.TimeoutError:
        await _terminate_process(proc)
        raise TimeoutError(f"bluetoothctl command timed out: {command}") from None
    except asyncio.CancelledError:
        await _terminate_process(proc)
        raise
    except Exception as err:
        await _terminate_process(proc)
        raise RuntimeError(f"Command failed: {command} - {err}") from err


async def start_bluetoothctl() -> asyncio.subprocess.Process:
    """Start bluetoothctl as an interactive asyncio subprocess."""
    return await asyncio.create_subprocess_exec(
        "bluetoothctl",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def send_command_in_process(
    process: asyncio.subprocess.Process, command: str, delay: float = 2
) -> None:
    """Send a command to the bluetoothctl process and wait briefly."""
    if process.stdin is None:
        raise RuntimeError("bluetoothctl stdin is not available")

    process.stdin.write(f"{command}\n".encode())
    await process.stdin.drain()
    await asyncio.sleep(delay)


async def is_device_connected(address: str) -> bool:
    """Check if a Bluetooth device is connected by its MAC address."""
    cmdout = await run_command("devices Connected")
    target_address = address.lower().encode()
    stdout = cmdout["stdout"]
    assert isinstance(stdout, bytes)

    for line in stdout.splitlines():
        if line.lower().startswith(b"device " + target_address):
            return True
    return False


async def is_device_bonded(address: str) -> bool:
    """Check if a Bluetooth device is bonded by its MAC address."""
    cmdout = await run_command("devices Bonded")
    target_address = address.lower().encode()
    stdout = cmdout["stdout"]
    assert isinstance(stdout, bytes)

    for line in stdout.splitlines():
        if line.lower().startswith(b"device " + target_address):
            return True
    return False


async def is_device_paired(address: str) -> bool:
    """Check if a Bluetooth device is paired by its MAC address."""
    cmdout = await run_command("devices Paired")
    target_address = address.lower().encode()
    stdout = cmdout["stdout"]
    assert isinstance(stdout, bytes)

    for line in stdout.splitlines():
        if line.lower().startswith(b"device " + target_address):
            return True
    return False


async def get_first_manufacturer_data_byte(mac_address: str) -> int | None:
    """Return the first byte of ManufacturerData.Value for a BLE device."""
    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl",
        "info",
        mac_address,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, _stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_COMMAND_TIMEOUT
        )
    except asyncio.TimeoutError:
        await _terminate_process(proc)
        _LOGGER.error("bluetoothctl command timed out")
        return None
    except asyncio.CancelledError:
        await _terminate_process(proc)
        raise

    lines = stdout.decode().splitlines()

    for i, line in enumerate(lines):
        if "ManufacturerData.Value" in line:
            if (i + 1) < len(lines):
                hex_str = re.search(r"([0-9a-fA-F]{2})", lines[i + 1].strip())
                if hex_str:
                    return int(hex_str.group(1), 16)
    return None


async def pair_with_ble_device(device_name: str, device_address: str) -> bool:
    """Attempt to pair with a BLE device using bluetoothctl with retries."""
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await start_bluetoothctl()
            await send_command_in_process(process, f"trust {device_address}", delay=1)
            await send_command_in_process(process, f"connect {device_address}", delay=2)
            await send_command_in_process(process, "yes", delay=7)

            idc = await is_device_connected(device_address)
            idp = await is_device_paired(device_address)

            if idc and not idp:
                await send_command_in_process(
                    process,
                    f"pair {device_address}",
                    delay=7,
                )

            await send_command_in_process(process, "exit", delay=1)

            # Verify connection/bond status
            idc = await is_device_connected(device_address)
            idb = await is_device_bonded(device_address)
            idp = await is_device_paired(device_address)

            if idc and idb and idp:
                _LOGGER.debug(
                    "Connected, Paired and Bonded to %s",
                    device_address,
                )
                return True

            _LOGGER.error(
                "Failed to connect and bond(attempt %d)",
                retry_count + 1,
            )
            _LOGGER.error(
                "Connected? %s \t Paired? %s \t Bonded? %s",
                idc,
                idp,
                idb,
            )

        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.error(
                "Connection error (attempt %d): %s",
                retry_count + 1,
                err,
            )
        finally:
            if process is not None:
                await _terminate_process(process)

        retry_count += 1
        await asyncio.sleep(3)

    return False


async def filter_ryse_devices_pairing(
    devices, existing_addresses: set[str]
) -> dict[str, str]:
    """Filter BLE RYSE devices and return only those in pairing mode."""
    device_options = {}

    for device in devices:
        if not device.name:
            continue
        if device.address in existing_addresses:
            _LOGGER.debug(
                "Skipping already configured device: %s (%s)",
                device.name,
                device.address,
            )
            continue

        manufacturer_data = getattr(device, "manufacturer_data", None)
        raw_data = manufacturer_data.get(0x0409) if manufacturer_data else None
        if raw_data is None:
            continue

        btctl_mfgdata0 = await get_first_manufacturer_data_byte(device.address)
        if (
            len(raw_data) > 0
            and btctl_mfgdata0 is not None
            and (btctl_mfgdata0 & 0x40)
        ):
            device_options[device.address] = f"{device.name} ({device.address})"
            _LOGGER.debug(
                "Found RYSE in pairing mode: %s (%s) btctlMfgdata0=%02X",
                device.name,
                device.address,
                btctl_mfgdata0,
            )

    return device_options


async def is_pairing_ryse_device(address: str) -> bool:
    """Return True if the device has valid RYSE manufacturer data."""
    try:
        btctl_mfgdata0 = await get_first_manufacturer_data_byte(address)
    except Exception:
        return False

    if btctl_mfgdata0 is None:
        return False

    return bool(btctl_mfgdata0 & 0x40)
