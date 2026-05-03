"""DongleSerial — UART multiplexer for Dongle/LDPS-Probe communication.

Protocol: {TYPE}:{PAYLOAD}\n
- EN:/en: — ESP-NOW commands/responses
- SX:/sx: — SX1262 playback (future, LDPS-Probe)
- TB:/tb: — Test Board native commands (future, LDPS-Probe)
- DG:/dg: — Board status
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable, Dict, List, Optional

import serial

from app.utils import log

UpstreamHandler = Callable[[str, str], None]


class DongleSerial:
    def __init__(self, port: str, baudrate: int = 115200) -> None:
        self.port = port
        self.baudrate = baudrate
        self._ser: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._handlers: Dict[str, List[UpstreamHandler]] = defaultdict(list)
        self._connected = False
        self._ready = False
        self._dongle_status: Dict[str, str] = {}
        self.tx_log: deque = deque(maxlen=200)
        self.rx_log: deque = deque(maxlen=200)

    def open(self) -> bool:
        with self._lock:
            try:
                self._ser = serial.Serial(port=self.port, baudrate=self.baudrate, timeout=0.05)
                self._connected = True
                log(f"[Dongle] opened {self.port}@{self.baudrate}")
                try:
                    self._ser.write(b"DG:STATUS\n")
                except Exception:
                    pass
                return True
            except Exception as exc:
                log(f"[Dongle] open failed: {exc}", "ERROR")
                self._connected = False
                return False

    def close(self) -> None:
        with self._lock:
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
            self._connected = False
            self._ready = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def ready(self) -> bool:
        return self._ready

    def mark_disconnected(self) -> None:
        with self._lock:
            self._connected = False
            self._ready = False

    def send(self, msg_type: str, payload: str) -> bool:
        with self._lock:
            if not self._connected or self._ser is None:
                return False
            try:
                line = f"{msg_type}:{payload}\n"
                self._ser.write(line.encode("utf-8"))
                self.tx_log.appendleft({"ts": time.time(), "type": msg_type, "payload": payload})
                return True
            except Exception as exc:
                log(f"[Dongle] send error: {exc}", "ERROR")
                self._connected = False
                return False

    def register_handler(self, msg_type: str, handler: UpstreamHandler) -> None:
        self._handlers[msg_type].append(handler)

    def poll(self) -> None:
        if not self._connected or self._ser is None:
            return
        try:
            with self._lock:
                lines: list[bytes] = []
                while self._ser and self._ser.in_waiting:
                    raw = self._ser.readline()
                    if not raw:
                        break
                    lines.append(raw)
        except Exception as exc:
            log(f"[Dongle] poll error: {exc}", "ERROR")
            self._connected = False
            return

        for raw in lines:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                self._dispatch(line)

    def _dispatch(self, line: str) -> None:
        colon = line.find(":")
        if colon < 1:
            return
        msg_type = line[:colon]
        payload = line[colon + 1:]
        self.rx_log.appendleft({"ts": time.time(), "type": msg_type, "payload": payload})

        if msg_type == "dg":
            self._handle_dg(payload)

        for handler in list(self._handlers.get(msg_type, [])):
            try:
                handler(msg_type, payload)
            except Exception as exc:
                log(f"[Dongle] handler error ({msg_type}): {exc}", "ERROR")

    def _handle_dg(self, payload: str) -> None:
        parts = payload.split(",")
        cmd = parts[0] if parts else ""
        if cmd == "READY":
            self._ready = True
            self._dongle_status = {}
            for kv in parts[1:]:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    self._dongle_status[k] = v
            log(f"[Dongle] READY: {self._dongle_status}")
