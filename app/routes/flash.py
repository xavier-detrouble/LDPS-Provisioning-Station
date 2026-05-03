"""Firmware flash routes."""
from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse

from app.flasher import Flasher
from app.utils import log

router = APIRouter()


def _s(r: Request):
    return r.app.state.app_state


@router.get("/firmware-list")
def firmware_list(request: Request):
    return {"files": Flasher.list_firmware()}


@router.post("/start")
def flash_start(request: Request, data: dict = Body(...)):
    s = _s(request)
    port = data.get("port", "")
    if not port:
        return JSONResponse({"error": "port required"}, 400)

    if not hasattr(s, 'flasher') or s.flasher is None:
        s.flasher = Flasher(ws_manager=s.ws)

    if s.flasher.running:
        return JSONResponse({"error": "Flash already in progress"}, 409)

    if not s.flasher.start(port):
        return JSONResponse({"error": s.flasher.error or "Failed to start flash"}, 500)

    log(f"[Route] Flash started on {port}")
    return {"ok": True, "port": port}


@router.get("/status")
def flash_status(request: Request):
    s = _s(request)
    if not hasattr(s, 'flasher') or s.flasher is None:
        return {"running": False, "progress": 0, "status": "idle", "error": ""}
    f = s.flasher
    return {
        "running": f.running,
        "progress": f.progress,
        "status": f.status,
        "error": f.error,
    }
