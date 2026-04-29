"""
Hardware driver: SSH (Paramiko) + Serial relay control.

Responsibilities:
  - Execute CLI commands on the executor PC or a remote host via SSH
  - Control USB relay boards over serial port (power-cycle devices)
  - Push/pull files over SSH (SFTP)
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional, Tuple

from .base_driver import BaseDriver

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

logger = logging.getLogger("HardwareDriver")


class SSHClient:
    """
    Thin Paramiko wrapper with connection pooling (single persistent session).

    Thread safety: caller is responsible for locking (DeviceManager handles this).
    """

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "runner",
        password: Optional[str] = None,
        key_path: Optional[str] = None,
        keepalive: int = 30,
    ):
        if not PARAMIKO_AVAILABLE:
            raise ImportError("paramiko is required. Run: pip install paramiko")
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.key_path = key_path
        self.keepalive = keepalive
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: Dict[str, Any] = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": 30,
        }
        if self.key_path:
            connect_kwargs["key_filename"] = self.key_path
        elif self.password:
            connect_kwargs["password"] = self.password
        client.connect(**connect_kwargs)
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(self.keepalive)
        self._client = client
        logger.info("SSH connected to %s@%s:%d", self.username, self.host, self.port)

    def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def run(self, command: str, timeout: int = 30) -> Tuple[str, str, int]:
        """
        Execute command, return (stdout, stderr, exit_code).
        """
        if not self._client:
            raise RuntimeError("SSH not connected. Call connect() first.")
        _, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        return stdout.read().decode(), stderr.read().decode(), exit_code

    def push_file(self, local_path: str, remote_path: str) -> None:
        sftp = self._client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()

    def pull_file(self, remote_path: str, local_path: str) -> None:
        sftp = self._client.open_sftp()
        sftp.get(remote_path, local_path)
        sftp.close()


class RelayController:
    """
    USB relay board control via serial port.

    Supports common HID relay boards with AT-command protocol.
    If you have a different board, override _send_command().
    """

    # Common relay board command format: open=0xA0,ch,0x01,checksum; close=0xA0,ch,0x00,checksum
    _CMD_ON = 0x01
    _CMD_OFF = 0x00

    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0):
        if not SERIAL_AVAILABLE:
            raise ImportError("pyserial is required. Run: pip install pyserial")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial: Optional[serial.Serial] = None

    def open(self) -> None:
        self._serial = serial.Serial(
            self.port, baudrate=self.baudrate, timeout=self.timeout
        )
        logger.info("Serial relay opened on %s at %d baud", self.port, self.baudrate)

    def close(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    def set_channel(self, channel: int, state: bool) -> None:
        """
        Set relay channel (1-based) to on (True) or off (False).
        """
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Serial port not open. Call open() first.")
        cmd = self._build_command(channel, self._CMD_ON if state else self._CMD_OFF)
        self._serial.write(cmd)
        logger.debug("Relay ch%d → %s", channel, "ON" if state else "OFF")

    def power_cycle(self, channel: int, delay: float = 2.0) -> None:
        """Turn off, wait, turn on."""
        self.set_channel(channel, False)
        time.sleep(delay)
        self.set_channel(channel, True)

    @staticmethod
    def _build_command(channel: int, state: int) -> bytes:
        header = 0xA0
        checksum = (header + channel + state) & 0xFF
        return bytes([header, channel, state, checksum])


class HardwareDriver(BaseDriver):
    """
    Combined SSH + Serial relay driver.

    One HardwareDriver per executor PC; individual phones are addressed
    by their serial numbers via adb over SSH.
    """

    def __init__(
        self,
        device_serial: str,
        ssh_host: str,
        ssh_port: int = 22,
        ssh_username: str = "runner",
        ssh_key_path: Optional[str] = None,
        ssh_password: Optional[str] = None,
        serial_port: Optional[str] = None,
        serial_baudrate: int = 9600,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(device_serial, config)
        self._ssh = SSHClient(
            host=ssh_host,
            port=ssh_port,
            username=ssh_username,
            password=ssh_password,
            key_path=ssh_key_path,
        )
        self._relay: Optional[RelayController] = None
        if serial_port:
            self._relay = RelayController(serial_port, serial_baudrate)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._ssh.connect()
        if self._relay:
            self._relay.open()
        self._connected = True

    def disconnect(self) -> None:
        self._ssh.disconnect()
        if self._relay:
            self._relay.close()
        self._connected = False

    # ------------------------------------------------------------------
    # CLI operations
    # ------------------------------------------------------------------

    def run_command(self, command: str, timeout: int = 30) -> str:
        stdout, stderr, code = self._ssh.run(command, timeout=timeout)
        if code != 0:
            raise RuntimeError(
                f"Command failed (exit {code}): {command!r}\nSTDERR: {stderr}"
            )
        return stdout

    def run_adb(self, adb_command: str, timeout: int = 30) -> str:
        """Run an adb command targeting this driver's device serial."""
        return self.run_command(
            f"adb -s {self.device_serial} {adb_command}", timeout=timeout
        )

    def push_file(self, local_path: str, remote_path: str) -> None:
        self._ssh.push_file(local_path, remote_path)

    def pull_file(self, remote_path: str, local_path: str) -> None:
        self._ssh.pull_file(remote_path, local_path)

    def screenshot(self, path: str) -> str:
        """Save screenshot to a remote temp file, then pull locally."""
        remote_tmp = f"/tmp/screenshot_{self.device_serial}.png"
        self.run_adb(f"exec-out screencap -p > {remote_tmp}")
        self.pull_file(remote_tmp, path)
        return path

    # ------------------------------------------------------------------
    # Hardware / relay operations
    # ------------------------------------------------------------------

    def relay_set(self, channel: int, state: bool) -> None:
        if not self._relay:
            raise RuntimeError("No serial relay configured for this HardwareDriver.")
        self._relay.set_channel(channel, state)

    def power_cycle(self, channel: int, delay: float = 2.0) -> None:
        if not self._relay:
            raise RuntimeError("No serial relay configured for this HardwareDriver.")
        self._relay.power_cycle(channel, delay)
