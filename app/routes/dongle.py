"""Dongle / Test Board connection routes."""
import time

from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse

from app.utils import log

router = APIRouter()


def _s(r: Request):
    return r.app.state.app_state


def _connect_on_port(s, port: str, auto: bool = False):
    """Open `port` as the dongle, wire the ESP-NOW + Test-Board handlers, start the
    rx worker and broadcast status. Shared by manual /connect and auto /detect."""
    if s.dongle and s.dongle.connected:
        s.dongle.close()

    from app.dongle import DongleSerial
    from app.espnow import ESPNowChannel

    dongle = DongleSerial(port)
    if not dongle.open():
        return JSONResponse({"error": f"Failed to open {port}"}, 500)

    espnow = ESPNowChannel(dongle)
    s.dongle = dongle
    s.espnow = espnow
    s.dongle_port = port
    s.dongle_connected = True

    from app import _setup_espnow_handler
    _setup_espnow_handler(s, espnow)

    def _on_tb_msg(msg_type: str, payload: str):
        if payload.startswith("CAPTURE_RESULT,"):
            from app.routes.provision import handle_capture_result
            handle_capture_result(payload[len("CAPTURE_RESULT,"):])
    dongle.register_handler("tb", _on_tb_msg)

    if not hasattr(s, '_rx_thread') or s._rx_thread is None or not s._rx_thread.is_alive():
        import threading
        from app.workers.dongle_receiver import dongle_rx_worker
        t = threading.Thread(target=dongle_rx_worker, args=(s,), daemon=True)
        t.start()
        s._rx_thread = t

    if s.ws:
        s.ws.broadcast("dongle", {"connected": True, "ready": dongle.ready, "port": port})

    log(f"[Route] Dongle connected: {port}{' (auto-detected)' if auto else ''}")
    return {"ok": True, "port": port, "auto": auto}


def _probe_is_dongle(port: str, timeout: float = 2.5) -> bool:
    """Does a real Dongle answer the DG:STATUS handshake (dg:READY) on `port`? An
    Edge-Node does not. Opens + polls a throwaway handle, then closes it either way —
    so /detect can tell the dongle apart from the node without the operator guessing."""
    from app.dongle import DongleSerial
    d = DongleSerial(port)
    if not d.open():
        return False
    try:
        end = time.time() + timeout
        while time.time() < end:
            d.poll()
            if d.ready:
                return True
            time.sleep(0.1)
        return False
    finally:
        d.close()


@router.post("/connect")
def connect_dongle(request: Request, data: dict = Body(...)):
    """Manual connect (fallback): the operator picked a port explicitly."""
    s = _s(request)
    port = data.get("port", "")
    if not port:
        return JSONResponse({"error": "port required"}, 400)
    return _connect_on_port(s, port)


@router.post("/detect")
def detect_dongle(request: Request):
    """Auto-detect + connect the Test Board so the operator never picks a port.
    Probes each ESP32-S3 USB port with the DG:STATUS handshake — the dongle answers
    dg:READY, an Edge-Node doesn't — and connects the one that does."""
    s = _s(request)
    if s.dongle and getattr(s.dongle, "connected", False) and s.dongle.ready:
        return {"ok": True, "port": s.dongle_port, "already": True}

    from app.utils import list_serial_ports
    cand = [p["device"] for p in list_serial_ports()
            if any(k in p["device"] for k in ("usbmodem", "ttyACM", "ttyUSB", "wchusbserial", "SLAB"))]
    for port in cand:
        if _probe_is_dongle(port):
            return _connect_on_port(s, port, auto=True)
    return JSONResponse({"error": "No Test Board found — plug the dongle into USB, then retry.",
                         "candidates": cand}, 404)


@router.post("/disconnect")
def disconnect_dongle(request: Request):
    s = _s(request)
    if s.dongle:
        s.dongle.close()
    s.dongle_connected = False
    s.dongle_port = ""
    if s.ws:
        s.ws.broadcast("dongle", {"connected": False, "ready": False})
    return {"ok": True}


@router.get("/status")
def dongle_status(request: Request):
    s = _s(request)
    return {
        "connected": s.dongle_connected and s.dongle and s.dongle.connected,
        "ready": s.dongle.ready if s.dongle else False,
        "port": s.dongle_port,
    }
