"""Provisioning workflow routes."""
from __future__ import annotations

import json
import time
import threading
from threading import Event

from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse

from app.utils import log

router = APIRouter()

# Pending ACK trackers
_pending_hw_test: dict[str, dict] = {}
_pending_set_uuid: dict[str, dict] = {}
_pending_nvs_erase: dict[str, dict] = {}


def _s(r: Request):
    return r.app.state.app_state


# ── ESP-NOW Response Handlers (called from __init__.py) ────

def handle_hw_test_result(mac: str, parts: list[str]) -> None:
    json_str = ",".join(parts[1:]) if len(parts) > 1 else "{}"
    try:
        result = json.loads(json_str)
    except Exception:
        result = {"raw": json_str}
    pending = _pending_hw_test.get(mac)
    if pending:
        pending["result"] = result
        pending["event"].set()


def handle_set_uuid_ack(mac: str, parts: list[str]) -> None:
    status = parts[1] if len(parts) > 1 else "?"
    ack_uuid = parts[2] if len(parts) > 2 else ""
    pending = _pending_set_uuid.get(mac)
    if pending:
        pending["result"] = f"{status},{ack_uuid}"
        pending["event"].set()


def handle_nvs_erase_ack(mac: str, parts: list[str]) -> None:
    status = parts[1] if len(parts) > 1 else "?"
    pending = _pending_nvs_erase.get(mac)
    if pending:
        pending["result"] = status
        pending["event"].set()


# ── Discovery ─────────────────────────────────────────

@router.post("/discover")
async def discover(request: Request):
    s = _s(request)
    if not s.espnow:
        return JSONResponse({"error": "Dongle not connected"}, 503)

    s.discovered_nodes.clear()
    s.espnow.discover()

    # Wait 3 seconds for responses
    time.sleep(3)

    nodes = list(s.discovered_nodes.values())
    return {"ok": True, "nodes": nodes}


@router.post("/identify/{mac}")
async def identify(request: Request, mac: str):
    s = _s(request)
    if not s.espnow:
        return JSONResponse({"error": "Dongle not connected"}, 503)
    s.espnow.identify(mac)
    return {"ok": True}


# ── Hardware Test ─────────────────────────────────────

@router.post("/hw-test/{mac}")
async def hw_test(request: Request, mac: str):
    s = _s(request)
    if not s.espnow:
        return JSONResponse({"error": "Dongle not connected"}, 503)

    evt = Event()
    _pending_hw_test[mac] = {"event": evt, "result": None}

    try:
        s.espnow.hw_test(mac)
        if not evt.wait(timeout=15):
            return JSONResponse({"error": "HW_TEST timeout"}, 408)
        result = _pending_hw_test[mac].get("result")
        return {"ok": True, "mac": mac, "test_results": result}
    finally:
        _pending_hw_test.pop(mac, None)


# ── Sync Test Pack ────────────────────────────────────

@router.post("/sync/{mac}")
async def sync_test_pack(request: Request, mac: str, data: dict = Body({})):
    """Write fake UUID → push WiFi config → trigger SYNC_PACKS."""
    s = _s(request)
    if not s.espnow:
        return JSONResponse({"error": "Dongle not connected"}, 503)

    from app.config import FAKE_UUID

    # Step 1: Write fake UUID
    evt = Event()
    _pending_set_uuid[mac] = {"event": evt, "result": ""}
    s.espnow.set_uuid(mac, FAKE_UUID)

    uuid_ok = evt.wait(timeout=5)
    uuid_result = _pending_set_uuid.pop(mac, {}).get("result", "")

    if not uuid_ok:
        return JSONResponse({"error": "SET_UUID timeout"}, 408)

    if "err,already_set" in uuid_result:
        log(f"[Provision] {mac}: UUID already set, proceeding with existing")

    # Step 2: Push WiFi config (Station AP)
    wifi_ssid = data.get("wifi_ssid", "LDPS-Factory")
    wifi_pwd = data.get("wifi_pwd", "ldps1234")
    station_ip = data.get("station_ip", "192.168.4.1:9000")

    s.espnow.push_config(mac, "wifi", f"{wifi_ssid},{wifi_pwd},{station_ip}")
    time.sleep(0.2)

    # Step 3: Switch to test pack (index 0)
    s.espnow.switch_pack(mac, 0)
    time.sleep(0.2)

    # Step 4: Trigger sync
    s.espnow.sync_packs(mac)

    return {"ok": True, "fake_uuid": FAKE_UUID, "wifi": wifi_ssid}


# ── NVS Erase ─────────────────────────────────────────

@router.post("/nvs-erase/{mac}")
async def nvs_erase(request: Request, mac: str):
    s = _s(request)
    if not s.espnow:
        return JSONResponse({"error": "Dongle not connected"}, 503)

    evt = Event()
    _pending_nvs_erase[mac] = {"event": evt, "result": ""}
    s.espnow.nvs_erase(mac)

    if not evt.wait(timeout=5):
        _pending_nvs_erase.pop(mac, None)
        return JSONResponse({"error": "NVS_ERASE timeout"}, 408)

    result = _pending_nvs_erase.pop(mac, {}).get("result", "")
    if "ok" in result:
        return {"ok": True}
    return JSONResponse({"error": f"NVS_ERASE failed: {result}"}, 500)


# ── SET_UUID (real UUID) ──────────────────────────────

@router.post("/set-uuid/{mac}")
async def set_uuid(request: Request, mac: str, data: dict = Body(...)):
    s = _s(request)
    if not s.espnow:
        return JSONResponse({"error": "Dongle not connected"}, 503)

    uuid = data.get("uuid", "")
    if not uuid:
        return JSONResponse({"error": "uuid required"}, 400)

    evt = Event()
    _pending_set_uuid[mac] = {"event": evt, "result": ""}
    s.espnow.set_uuid(mac, uuid)

    if not evt.wait(timeout=5):
        _pending_set_uuid.pop(mac, None)
        return JSONResponse({"error": "SET_UUID timeout"}, 408)

    result = _pending_set_uuid.pop(mac, {}).get("result", "")
    if "ok" in result:
        return {"ok": True, "uuid": uuid}
    return JSONResponse({"error": f"SET_UUID failed: {result}"}, 500)


# ── Finalize (full provision sequence) ────────────────

@router.post("/finalize/{mac}")
async def finalize(request: Request, mac: str, data: dict = Body(...)):
    """NVS_ERASE → request UUID from Cloud → SET_UUID → confirm.
    Pre-fetches UUID before erasing (race condition fix)."""
    s = _s(request)
    if not s.espnow:
        return JSONResponse({"error": "Dongle not connected"}, 503)
    if not hasattr(s, 'cloud_client') or not s.cloud_client:
        return JSONResponse({"error": "Not logged in to Cloud"}, 401)

    product_type = data.get("product_type", "default")
    test_results = data.get("test_results")

    # Step 1: Pre-fetch UUID from Cloud
    real_uuid = await s.cloud_client.request_uuid(mac, product_type, test_results)
    if not real_uuid:
        return JSONResponse({"error": "Failed to get UUID from Cloud"}, 502)

    # Step 2: NVS_ERASE
    evt = Event()
    _pending_nvs_erase[mac] = {"event": evt, "result": ""}
    s.espnow.nvs_erase(mac)

    if not evt.wait(timeout=5):
        _pending_nvs_erase.pop(mac, None)
        await s.cloud_client.confirm(real_uuid, success=False)
        return JSONResponse({"error": "NVS_ERASE timeout"}, 408)

    erase_result = _pending_nvs_erase.pop(mac, {}).get("result", "")
    if "ok" not in erase_result:
        await s.cloud_client.confirm(real_uuid, success=False)
        return JSONResponse({"error": f"NVS_ERASE failed: {erase_result}"}, 500)

    # Step 3: SET_UUID (real)
    evt2 = Event()
    _pending_set_uuid[mac] = {"event": evt2, "result": ""}
    s.espnow.set_uuid(mac, real_uuid)

    if not evt2.wait(timeout=5):
        _pending_set_uuid.pop(mac, None)
        await s.cloud_client.confirm(real_uuid, success=False)
        return JSONResponse({"error": "SET_UUID timeout"}, 408)

    uuid_result = _pending_set_uuid.pop(mac, {}).get("result", "")
    if "ok" not in uuid_result:
        await s.cloud_client.confirm(real_uuid, success=False)
        return JSONResponse({"error": f"SET_UUID failed: {uuid_result}"}, 500)

    # Step 4: Confirm with Cloud
    confirmed = await s.cloud_client.confirm(real_uuid, success=True)
    if not confirmed:
        log(f"[Provision] Cloud confirm failed for {real_uuid}, but UUID is written", "WARNING")

    # Log to SQLite
    from app.provision_log import ProvisionLog
    if not hasattr(s, 'provision_log') or s.provision_log is None:
        s.provision_log = ProvisionLog()
    s.provision_log.add(
        mac=mac, uuid=real_uuid, product_type=product_type,
        firmware_ver="2.0.0", test_results=test_results,
        status="success", cloud_confirmed=confirmed,
    )

    s.stats_provisioned += 1
    if s.ws:
        s.ws.broadcast("stats", {"provisioned": s.stats_provisioned, "failed": s.stats_failed})
        s.ws.broadcast("provision", {"step": "done", "mac": mac, "uuid": real_uuid})

    log(f"[Provision] SUCCESS: {mac} → {real_uuid}")
    return {"ok": True, "mac": mac, "uuid": real_uuid, "cloud_confirmed": confirmed}
