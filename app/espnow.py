"""ESP-NOW channel — simplified for Provisioning Station.

Only needs: DISCOVER, IDENTIFY, HW_TEST, CFG, SYNC_PACKS, STATUS_REQ
"""
from __future__ import annotations

import time
from typing import Callable, List

from app.dongle import DongleSerial
from app.utils import log

BROADCAST_MAC = "FF"


class ESPNowChannel:
    def __init__(self, dongle: DongleSerial) -> None:
        self._dongle = dongle
        self._rx_callbacks: List[Callable[[str, str], None]] = []
        self._last_send_ts = 0.0
        dongle.register_handler("en", self._on_upstream)

    def send_command(self, mac: str, command: str, args: str = "") -> bool:
        now = time.time()
        if now - self._last_send_ts < 0.05:
            time.sleep(0.05 - (now - self._last_send_ts))
        self._last_send_ts = time.time()

        parts = [mac, command]
        if args:
            parts.append(args)
        return self._dongle.send("EN", ",".join(parts))

    # Commands
    def discover(self) -> bool:
        return self.send_command(BROADCAST_MAC, "DISCOVER")

    def status_request(self, mac: str = BROADCAST_MAC) -> bool:
        return self.send_command(mac, "STATUS_REQ")

    def identify(self, mac: str) -> bool:
        return self.send_command(mac, "IDENTIFY")

    def hw_test(self, mac: str) -> bool:
        return self.send_command(mac, "HW_TEST")

    def push_config(self, mac: str, key: str, value: str) -> bool:
        return self.send_command(mac, "CFG", f"{key}={value}")

    def sync_packs(self, mac: str) -> bool:
        return self.send_command(mac, "SYNC_PACKS")

    def switch_pack(self, mac: str, pack_index: int) -> bool:
        return self.send_command(mac, "SWITCH_PACK", str(pack_index))

    # Callbacks
    def on_receive(self, callback: Callable[[str, str], None]) -> None:
        self._rx_callbacks.append(callback)

    def _on_upstream(self, msg_type: str, payload: str) -> None:
        comma = payload.find(",")
        if comma < 1:
            return
        mac = payload[:comma]
        rest = payload[comma + 1:]
        for cb in list(self._rx_callbacks):
            try:
                cb(mac, rest)
            except Exception as exc:
                log(f"[ESPNow] rx callback error: {exc}", "ERROR")
