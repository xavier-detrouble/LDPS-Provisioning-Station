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

# Pending ACK trackers with TTL cleanup
_pending_hw_test: dict[str, dict] = {}
_pending_set_uuid: dict[str, dict] = {}
_pending_nvs_erase: dict[str, dict] = {}
_pending_status: dict[str, dict] = {}
_PENDING_TTL = 30  # seconds before auto-cleanup


def _cleanup_stale(d: dict[str, dict]) -> None:
    """Remove entries older than _PENDING_TTL seconds."""
    now = time.time()
    stale = [k for k, v in d.items() if now - v.get("ts", 0) > _PENDING_TTL]
    for k in stale:
        d.pop(k, None)


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


def handle_status_rsp(mac: str, payload: str) -> None:
    """Handle STATUS_RSP for NVS read-back verification."""
    pending = _pending_status.get(mac)
    if pending:
        # Parse key=value pairs
        kv = {}
        for part in payload.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                kv[k] = v
        pending["result"] = kv
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

    _cleanup_stale(_pending_hw_test)
    evt = Event()
    _pending_hw_test[mac] = {"event": evt, "result": None, "ts": time.time()}

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
    _cleanup_stale(_pending_set_uuid)
    evt = Event()
    _pending_set_uuid[mac] = {"event": evt, "result": "", "ts": time.time()}
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

    _cleanup_stale(_pending_nvs_erase)
    evt = Event()
    _pending_nvs_erase[mac] = {"event": evt, "result": "", "ts": time.time()}
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

    _cleanup_stale(_pending_set_uuid)
    evt = Event()
    _pending_set_uuid[mac] = {"event": evt, "result": "", "ts": time.time()}
    s.espnow.set_uuid(mac, uuid)

    if not evt.wait(timeout=5):
        _pending_set_uuid.pop(mac, None)
        return JSONResponse({"error": "SET_UUID timeout"}, 408)

    result = _pending_set_uuid.pop(mac, {}).get("result", "")
    if "ok" in result:
        return {"ok": True, "uuid": uuid}
    return JSONResponse({"error": f"SET_UUID failed: {result}"}, 500)


# ── Finalize (USB provision: cloud UUID + genuineness → write over USB) ──

@router.post("/finalize/{mac}")
async def finalize(request: Request, mac: str, data: dict = Body(...)):
    """USB provision: cloud request-uuid (uuid + Ed25519 genuineness signature) →
    write identity to the node over USB serial ('P') + read-back verify → cloud
    confirm. The dongle is NOT used for identity (that's USB) — only for RF QC
    (/hw-test). `mac` is the node's hardware id (cloud hardware_serial); the body
    carries the USB `port`, `product_type` (required QC gate), `test_results`,
    `firmware_ver`."""
    import asyncio
    from app.node_serial import write_identity

    s = _s(request)
    if not getattr(s, "cloud_client", None):
        return JSONResponse({"error": "Not logged in to Cloud"}, 401)

    port = data.get("port")
    product_type = data.get("product_type")
    test_results = data.get("test_results")
    firmware_ver = data.get("firmware_ver", "")
    if not port:
        return JSONResponse({"error": "port required (node USB serial port)"}, 400)
    if not product_type:
        return JSONResponse({"error": "product_type required (QC gate)"}, 400)

    # Step 1: mint UUID + genuineness signature from Cloud.
    minted = await s.cloud_client.request_uuid(mac, product_type, test_results, firmware_ver)
    if not minted or not minted.get("uuid"):
        return JSONResponse({"error": "Failed to get UUID from Cloud"}, 502)
    uuid = minted["uuid"]
    sig = minted.get("signature")
    key_id = minted.get("key_id")
    if not sig or not key_id:
        # Genuineness is mandatory — a node with no signature can't be Hub-verified.
        await s.cloud_client.confirm(uuid, success=False)
        return JSONResponse({"error": "Cloud returned no signature (signing not configured)"}, 502)

    # Step 2: write identity to the node over USB + read-back verify (blocking → thread).
    if s.ws:
        s.ws.broadcast("provision", {"step": "writing", "mac": mac, "uuid": uuid})
    try:
        w = await asyncio.to_thread(write_identity, port, uuid, sig, key_id)
    except Exception as e:
        await s.cloud_client.confirm(uuid, success=False)
        return JSONResponse({"error": f"USB write error on {port}: {e}"}, 500)
    if not w.get("ok"):
        await s.cloud_client.confirm(uuid, success=False)
        return JSONResponse({"error": f"Node identity write failed: {w.get('detail')}"}, 500)

    # Step 3: confirm with Cloud (reserved → provisioned).
    confirmed = await s.cloud_client.confirm(uuid, success=True)
    if not confirmed:
        log(f"[Provision] Cloud confirm failed for {uuid}, but identity is written", "WARNING")

    # Log
    from app.provision_log import ProvisionLog
    if not getattr(s, "provision_log", None):
        s.provision_log = ProvisionLog()
    s.provision_log.add(
        mac=mac, uuid=uuid,
        firmware_ver=firmware_ver or "?", test_results=test_results,
        status="success", cloud_confirmed=confirmed,
    )

    s.stats_provisioned += 1
    if s.ws:
        s.ws.broadcast("stats", {"provisioned": s.stats_provisioned, "failed": s.stats_failed})
        s.ws.broadcast("provision", {"step": "done", "mac": mac, "uuid": uuid})

    log(f"[Provision] SUCCESS (USB): {mac} → {uuid} (key_id={key_id})")
    return {"ok": True, "mac": mac, "uuid": uuid, "key_id": key_id, "cloud_confirmed": confirmed}


# ── Playback Test (LDPS-Probe) ───────────────────────

# Pending capture result tracker
_pending_capture: dict[str, dict] = {}


def handle_capture_result(payload: str) -> None:
    """Called from __init__.py when tb:CAPTURE_RESULT received."""
    pending = _pending_capture.get("active")
    if pending:
        try:
            idx = payload.index("{")
            pending["result"] = json.loads(payload[idx:])
        except (ValueError, json.JSONDecodeError):
            pending["result"] = {"error": "parse_failed", "raw": payload[:200]}
        pending["event"].set()


@router.post("/playback-test/{mac}")
async def playback_test(request: Request, mac: str):
    """Run WS2812 signal capture via LDPS-Probe and analyze results.
    Sends SX:PLAY + TB:CAPTURE, waits for CAPTURE_RESULT, validates."""
    s = _s(request)
    if not s.dongle or not s.dongle.connected:
        return JSONResponse({"error": "Dongle not connected"}, 503)

    from app.config import TEST_PACK_UUID
    # CRC-8/MAXIM of test pack UUID
    pack_id = _crc8_maxim(TEST_PACK_UUID.encode())

    # Setup pending capture
    _cleanup_stale(_pending_capture)
    evt = Event()
    _pending_capture["active"] = {"event": evt, "result": None, "ts": time.time()}

    # Broadcast progress
    if s.ws:
        s.ws.broadcast("playback_test", {"status": "starting", "mac": mac})

    # Send PLAY_AND_CAPTURE (Probe starts capture then sends SX:PLAY)
    s.dongle.send("TB", f"PLAY_AND_CAPTURE,0,{pack_id},100,8000")

    # Wait for result (8s capture + overhead)
    if not evt.wait(timeout=25):
        _pending_capture.pop("active", None)
        if s.ws:
            s.ws.broadcast("playback_test", {"status": "timeout", "mac": mac})
        return JSONResponse({"error": "Capture timeout"}, 408)

    result = _pending_capture.pop("active", {}).get("result")

    if not result or "error" in result:
        if s.ws:
            s.ws.broadcast("playback_test", {"status": "error", "mac": mac})
        return JSONResponse({"error": "Capture failed", "detail": result}, 500)

    # Analyze results
    channels = result.get("channels", [])
    tests = []
    channels_with_signal = 0

    for ch in channels:
        c = ch.get("ch", "?")
        frames = ch.get("frames", 0)
        timing_ok = ch.get("timing_ok", False)
        fps = ch.get("avg_fps", 0)
        drops = ch.get("dropped", 0)

        has_signal = frames > 0
        ch_pass = has_signal and timing_ok and fps > 5
        if has_signal:
            channels_with_signal += 1

        tests.append({
            "channel": c,
            "frames": frames,
            "fps": round(fps, 1),
            "timing_ok": timing_ok,
            "drops": drops,
            "pass": ch_pass,
        })

    # Pass if all channels WITH signal pass (channels without wires are ignored)
    active_channels = [t for t in tests if t["frames"] > 0]
    all_active_pass = all(t["pass"] for t in active_channels) if active_channels else False

    # Stop playback
    s.dongle.send("SX", "STOP")

    status = "pass" if all_active_pass else "fail"
    if s.ws:
        s.ws.broadcast("playback_test", {"status": status, "mac": mac, "tests": tests})

    return {
        "ok": True,
        "pass": all_active_pass,
        "channels_with_signal": channels_with_signal,
        "tests": tests,
    }


def _crc8_maxim(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) if (crc & 0x80) else (crc << 1)
            crc &= 0xFF
    return crc
