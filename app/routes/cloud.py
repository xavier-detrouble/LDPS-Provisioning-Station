"""Cloud login and quota routes."""
from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse

from app.config import DEFAULT_CLOUD_URL
from app.utils import log

router = APIRouter()


def _s(r: Request):
    return r.app.state.app_state


@router.post("/login")
async def cloud_login(request: Request, data: dict = Body(...)):
    s = _s(request)
    email = data.get("email", "")
    password = data.get("password", "")
    cloud_url = data.get("cloud_url", DEFAULT_CLOUD_URL)

    if not email or not password:
        return JSONResponse({"error": "email and password required"}, 400)

    from app.cloud_client import CloudClient
    client = CloudClient(cloud_url)
    result = await client.login(email, password)

    if result.get("ok"):
        s.cloud_client = client
        s.cloud_url = cloud_url
        s.cloud_token = client.token
        s.cloud_email = email
        if s.ws:
            s.ws.broadcast("cloud", {"connected": True, "email": email})
        log(f"[Cloud] Logged in as {email}")
        return result
    return JSONResponse(result, 401)


@router.post("/logout")
def cloud_logout(request: Request):
    s = _s(request)
    s.cloud_token = ""
    s.cloud_email = ""
    s.cloud_client = None
    if s.ws:
        s.ws.broadcast("cloud", {"connected": False, "email": ""})
    return {"ok": True}


@router.get("/status")
def cloud_status(request: Request):
    s = _s(request)
    return {
        "connected": bool(s.cloud_token),
        "email": s.cloud_email,
        "cloud_url": s.cloud_url or DEFAULT_CLOUD_URL,
    }


@router.get("/quota")
async def cloud_quota(request: Request):
    s = _s(request)
    if not hasattr(s, 'cloud_client') or not s.cloud_client:
        return JSONResponse({"error": "Not logged in"}, 401)
    quotas = await s.cloud_client.get_quota()
    return {"quotas": quotas}
