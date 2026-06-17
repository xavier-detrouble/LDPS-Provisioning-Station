"""Cloud login and quota routes — manufacturer API key auth."""
from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse

from app.config import DEFAULT_CLOUD_URL
from app.utils import log

router = APIRouter()


def _s(r: Request):
    return r.app.state.app_state


@router.post("/login")
async def cloud_login(request: Request, data: dict = Body(...)):
    """Login with manufacturer API key (not email/password)."""
    s = _s(request)
    api_key = data.get("api_key", "")
    cloud_url = data.get("cloud_url", DEFAULT_CLOUD_URL)

    if not api_key:
        return JSONResponse({"error": "api_key required"}, 400)

    from app.cloud_client import CloudClient
    client = CloudClient(cloud_url)
    result = await client.login(api_key)

    if result.get("ok"):
        s.cloud_client = client
        s.cloud_url = cloud_url
        s.cloud_token = api_key  # stored for status display
        s.cloud_email = result.get("name", "Manufacturer")
        if s.ws:
            s.ws.broadcast("cloud", {
                "connected": True,
                "name": result.get("name", ""),
                "manufacturer_id": result.get("manufacturer_id", ""),
            })
        log(f"[Cloud] Manufacturer logged in: {result.get('name')}")
        return result
    return JSONResponse(result, 401)


@router.post("/logout")
def cloud_logout(request: Request):
    s = _s(request)
    s.cloud_token = ""
    s.cloud_email = ""
    s.cloud_client = None
    if s.ws:
        s.ws.broadcast("cloud", {"connected": False, "name": ""})
    return {"ok": True}


@router.get("/status")
def cloud_status(request: Request):
    s = _s(request)
    connected = bool(s.cloud_client and s.cloud_client.api_key)
    return {
        "connected": connected,
        "name": s.cloud_client.manufacturer_name if connected else "",
        "cloud_url": s.cloud_url or DEFAULT_CLOUD_URL,
    }


@router.get("/quota")
async def cloud_quota(request: Request):
    s = _s(request)
    if not s.cloud_client or not s.cloud_client.api_key:
        return JSONResponse({"error": "Not logged in"}, 401)
    quotas = await s.cloud_client.get_quota()
    return {"quotas": quotas}
