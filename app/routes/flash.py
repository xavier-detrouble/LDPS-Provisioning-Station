"""Firmware flash routes."""
import asyncio

from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse

from app import node_serial
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


@router.post("/format-sd")
async def format_sd(request: Request, data: dict = Body(...)):
    """Format the freshly-flashed node's SD card to FAT32 (post-flash step).
    The node is already on USB at the test rig, so we drive its serial 'f'
    command. Destructive — only run right after a factory flash."""
    port = data.get("port", "")
    if not port:
        return JSONResponse({"error": "port required"}, 400)
    log(f"[Route] SD format requested on {port}")
    result = await asyncio.to_thread(node_serial.format_sd, port)
    if not result.get("ok"):
        return JSONResponse({"error": result.get("detail", "format failed"),
                             **result}, 500)
    log(f"[Route] SD format ok on {port}: {result.get('detail')}")
    return result


@router.post("/generate-manifest")
def generate_manifest(request: Request):
    path = Flasher.generate_manifest()
    return {"ok": True, "path": path}


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
