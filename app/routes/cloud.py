"""Cloud login and quota routes — manufacturer API key auth."""
import json
import os

import httpx
from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse

from app.config import DEFAULT_CLOUD_URL, STATIC_DIR
from app.utils import log

router = APIRouter()

# Persisted manufacturer session — so the Station (especially on the OPi kiosk, where the
# long mfr key is painful to type) logs in ONCE and auto-restores on boot / when the network
# returns. Trusted local device; stored 0600. Cleared only on explicit logout.
SESSION_PATH = os.path.join(STATIC_DIR, "mfr_session.json")


def _s(r: Request):
    return r.app.state.app_state


def _has_saved_session() -> bool:
    return os.path.exists(SESSION_PATH)


def _save_session(api_key: str, cloud_url: str) -> None:
    try:
        with open(SESSION_PATH, "w") as f:
            json.dump({"api_key": api_key, "cloud_url": cloud_url}, f)
        os.chmod(SESSION_PATH, 0o600)
    except Exception as e:
        log(f"[Cloud] session save failed: {e}", "WARNING")


def _clear_session() -> None:
    try:
        if os.path.exists(SESSION_PATH):
            os.remove(SESSION_PATH)
    except Exception:
        pass


async def _cloud_reachable(url: str) -> bool:
    """Cheap reachability probe — ANY HTTP response (even 404) means the cloud is up."""
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            await c.get(url)
        return True
    except Exception:
        return False


async def _do_login(state, api_key: str, cloud_url: str) -> dict:
    """Shared login path: validate the key, attach the client, persist the session."""
    from app.cloud_client import CloudClient
    client = CloudClient(cloud_url)
    result = await client.login(api_key)
    if result.get("ok"):
        state.cloud_client = client
        state.cloud_url = cloud_url
        state.cloud_token = api_key
        state.cloud_email = result.get("name", "Manufacturer")
        _save_session(api_key, cloud_url)
    return result


async def restore_session(state) -> None:
    """Boot: re-login from the persisted key (login once on the OPi). A network failure
    keeps the saved key (retried later); only an explicit logout forgets it."""
    if not _has_saved_session():
        return
    try:
        with open(SESSION_PATH) as f:
            sess = json.load(f)
    except Exception:
        return
    api_key = sess.get("api_key")
    cloud_url = sess.get("cloud_url") or DEFAULT_CLOUD_URL
    if not api_key:
        return
    result = await _do_login(state, api_key, cloud_url)
    log("[Cloud] session restore: " + ("ok " + str(result.get("name")) if result.get("ok")
        else "deferred (" + str(result.get("error")) + ")"))


@router.post("/login")
async def cloud_login(request: Request, data: dict = Body(...)):
    """Login with manufacturer API key (not email/password)."""
    s = _s(request)
    api_key = data.get("api_key", "")
    cloud_url = data.get("cloud_url", DEFAULT_CLOUD_URL)

    if not api_key:
        return JSONResponse({"error": "api_key required"}, 400)

    result = await _do_login(s, api_key, cloud_url)
    if result.get("ok"):
        if s.ws:
            s.ws.broadcast("cloud", {
                "connected": True,
                "name": result.get("name", ""),
                "manufacturer_id": result.get("manufacturer_id", ""),
            })
        log(f"[Cloud] Manufacturer logged in: {result.get('name')}")
        return result
    return JSONResponse(result, 401)


@router.post("/relogin")
async def cloud_relogin(request: Request):
    """Re-attempt login from the persisted key (no typing) — boot / network-return."""
    s = _s(request)
    if not _has_saved_session():
        return JSONResponse({"error": "no saved session"}, 404)
    try:
        with open(SESSION_PATH) as f:
            sess = json.load(f)
    except Exception:
        return JSONResponse({"error": "saved session unreadable"}, 500)
    result = await _do_login(s, sess.get("api_key", ""),
                             sess.get("cloud_url") or DEFAULT_CLOUD_URL)
    if result.get("ok"):
        if s.ws:
            s.ws.broadcast("cloud", {"connected": True, "name": result.get("name", "")})
        return result
    return JSONResponse(result, 401)


@router.post("/logout")
def cloud_logout(request: Request):
    s = _s(request)
    s.cloud_token = ""
    s.cloud_email = ""
    s.cloud_client = None
    _clear_session()
    if s.ws:
        s.ws.broadcast("cloud", {"connected": False, "name": ""})
    return {"ok": True}


@router.get("/status")
async def cloud_status(request: Request):
    s = _s(request)
    connected = bool(s.cloud_client and s.cloud_client.api_key)
    url = s.cloud_url or DEFAULT_CLOUD_URL
    return {
        "connected": connected,
        "reachable": await _cloud_reachable(url),
        "name": s.cloud_client.manufacturer_name if connected else "",
        "cloud_url": url,
        "has_saved_session": _has_saved_session(),
    }


@router.get("/quota")
async def cloud_quota(request: Request):
    s = _s(request)
    if not s.cloud_client or not s.cloud_client.api_key:
        return JSONResponse({"error": "Not logged in"}, 401)
    quotas = await s.cloud_client.get_quota()
    return {"quotas": quotas}
