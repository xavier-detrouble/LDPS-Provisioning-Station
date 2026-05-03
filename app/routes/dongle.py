"""Dongle / Test Board connection routes."""
from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse

from app.utils import log

router = APIRouter()


def _s(r: Request):
    return r.app.state.app_state


@router.post("/connect")
def connect_dongle(request: Request, data: dict = Body(...)):
    s = _s(request)
    port = data.get("port", "")
    if not port:
        return JSONResponse({"error": "port required"}, 400)

    # Close existing connection
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

    # Wire ESP-NOW message handler
    from app import _setup_espnow_handler
    _setup_espnow_handler(s, espnow)

    # Start receiver worker if not running
    if not hasattr(s, '_rx_thread') or s._rx_thread is None or not s._rx_thread.is_alive():
        import threading
        from app.workers.dongle_receiver import dongle_rx_worker
        t = threading.Thread(target=dongle_rx_worker, args=(s,), daemon=True)
        t.start()
        s._rx_thread = t

    if s.ws:
        s.ws.broadcast("dongle", {"connected": True, "ready": dongle.ready, "port": port})

    log(f"[Route] Dongle connected: {port}")
    return {"ok": True, "port": port}


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
