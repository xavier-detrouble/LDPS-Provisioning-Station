"""Dongle serial poll worker thread."""
import time

from app.state import AppState
from app.utils import log


def dongle_rx_worker(state: AppState) -> None:
    """Continuously poll DongleSerial for incoming lines (~200Hz)."""
    log("[Dongle-RX] Worker started")
    while not state.stop_event.is_set():
        if state.dongle and state.dongle.connected:
            try:
                state.dongle.poll()
            except Exception as exc:
                log(f"[Dongle-RX] poll error: {exc}", "ERROR")
                if state.dongle:
                    state.dongle.mark_disconnected()
                    if state.ws:
                        state.ws.broadcast("dongle", {"connected": False, "ready": False})
        time.sleep(0.005)
    log("[Dongle-RX] Worker stopped")
