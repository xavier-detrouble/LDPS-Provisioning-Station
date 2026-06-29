"""Hub provisioning workflow routes (flow B — HUB_IDENTITY_DESIGN §5/§6.1).

Build order (Xavier): cloud comms → GUI → HW. This file is the cloud-orchestration
layer + the GUI's backend. The physical transport to the assembled OPi (read its
RK3566 cpuid, write hub_boot_identity.json + signing_keys.json to its SD, run Hub QC)
is the §6.1 provisioning channel = the step-3 hardware piece; until then the operator
supplies the cpuid and the signed binding is returned for that later write. The cloud
calls here are real (verified against the running cloud)."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Body
from fastapi.responses import JSONResponse

from app.config import HUB_HOST
from app.utils import log

router = APIRouter()


def _s(r: Request):
    return r.app.state.app_state


def _need_cloud(s):
    return getattr(s, "cloud_client", None) and s.cloud_client.api_key


@router.post("/provision")
async def hub_provision(request: Request, data: dict = Body(...)):
    """Mint + sign a hub binding for an assembled OPi. body: {cpuid, product_type,
    test_results?, firmware_ver?, provision_batch?}. Returns the binding the Station
    writes onto the hub SD (hub_boot_identity.json + signing_keys.json — step-3
    transport). Reserves quota (status=provisioned, pending COMMIT via /confirm)."""
    s = _s(request)
    if not _need_cloud(s):
        return JSONResponse({"error": "Not logged in to Cloud"}, 401)
    cpuid = (data.get("cpuid") or "").strip()
    product = data.get("product")   # the catalog product key (ADR-0008)
    if not cpuid:
        return JSONResponse({"error": "cpuid required (read from the assembled OPi)"}, 400)
    if not product:
        return JSONResponse({"error": "product required (QC gate)"}, 400)

    res = await s.cloud_client.provision_hub(
        cpuid, product,
        test_results=data.get("test_results"),
        firmware_ver=data.get("firmware_ver", ""),
        provision_batch=data.get("provision_batch", ""))
    if not res.get("ok"):
        code = res.get("code")
        status = (409 if code == "CPUID_EXISTS"
                  else 403 if code in ("QUOTA_EXHAUSTED", "QUOTA_NOT_FOUND")
                  else 400 if code == "UNKNOWN_PRODUCT_TYPE" else 502)
        return JSONResponse({"error": res.get("error"), "code": code}, status)

    # Hold the signed binding on the wizard state so the GUI can show it and the
    # step-3 SD write can consume it without re-minting.
    s.hub_pending = {"hub_uuid": res["hub_uuid"], "cpuid": cpuid, "product": product}
    if s.ws:
        s.ws.broadcast("hub_provision", {"step": "signed", "hub_uuid": res["hub_uuid"], "cpuid": cpuid})
    log(f"[HubProvision] signed binding: hub_uuid={res['hub_uuid']} cpuid={cpuid[:12]}… key_id={res.get('key_id')}")
    return {"ok": True, **{k: res.get(k) for k in
                           ("hub_uuid", "cpuid", "binding_signature", "key_id", "signing_keys")}}


@router.post("/confirm")
async def hub_confirm(request: Request, data: dict = Body(...)):
    """COMMIT (SD binding written + QC passed) or RELEASE (write/QC failed → free quota)."""
    s = _s(request)
    if not _need_cloud(s):
        return JSONResponse({"error": "Not logged in to Cloud"}, 401)
    hub_uuid = data.get("hub_uuid")
    success = bool(data.get("success", True))
    if not hub_uuid:
        return JSONResponse({"error": "hub_uuid required"}, 400)

    ok = await s.cloud_client.confirm_hub(hub_uuid, success=success)
    if ok and success:
        s.stats_provisioned += 1
        s.hub_pending = None
        if s.ws:
            s.ws.broadcast("stats", {"provisioned": s.stats_provisioned, "failed": s.stats_failed})
            s.ws.broadcast("hub_provision", {"step": "done", "hub_uuid": hub_uuid})
        log(f"[HubProvision] COMMIT hub_uuid={hub_uuid}")
    elif ok:
        s.hub_pending = None
        log(f"[HubProvision] RELEASE hub_uuid={hub_uuid} (quota freed)")
    return {"ok": ok}


@router.post("/rebind")
async def hub_rebind(request: Request, data: dict = Body(...)):
    """RMA board swap: re-sign the binding to a NEW cpuid (same hub_uuid + owner)."""
    s = _s(request)
    if not _need_cloud(s):
        return JSONResponse({"error": "Not logged in to Cloud"}, 401)
    hub_uuid = data.get("hub_uuid")
    cpuid = (data.get("cpuid") or "").strip()
    if not hub_uuid or not cpuid:
        return JSONResponse({"error": "hub_uuid and cpuid required"}, 400)

    res = await s.cloud_client.rebind_hub(hub_uuid, cpuid)
    if not res.get("ok"):
        return JSONResponse({"error": res.get("error")}, 502)
    log(f"[HubProvision] REBIND hub_uuid={hub_uuid} → new cpuid={cpuid[:12]}…")
    return {"ok": True, **{k: res.get(k) for k in
                           ("hub_uuid", "cpuid", "binding_signature", "key_id", "signing_keys")}}


# ── §6.1 provisioning channel (Station → assembled OPi) ─────────────────────────
# Step-3 transport. The Station reaches the OPi's open channel (FRESH/locked only) to
# READ its cpuid and WRITE the cloud-signed binding. LAN HTTP now (set HUB_HOST);
# USB-gadget/eth link-local later — same routes, transport-agnostic. The hub verifies
# the binding against its OWN cpuid before writing, so the open channel can never
# impersonate, only self-lock. Authority: HUB_IDENTITY_DESIGN §6.1.

@router.get("/host")
async def hub_host_info():
    """The §6.1 channel target the Station will read/write (shown in the GUI)."""
    return {"hub_host": HUB_HOST}


@router.get("/read-cpuid")
async def hub_read_cpuid():
    """Read the assembled OPi's cpuid over the §6.1 channel (auto-fills the GUI field)."""
    url = f"{HUB_HOST}/api/provision/cpuid"
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(url)
    except Exception as e:
        return JSONResponse({"error": f"cannot reach hub at {HUB_HOST} — check it is powered "
                                      f"+ on the provisioning link ({e})"}, 502)
    if r.status_code == 404:
        return JSONResponse({"error": "hub channel closed — it is already provisioned "
                                      "(RMA-clear it to re-provision)"}, 409)
    if r.status_code != 200:
        return JSONResponse({"error": f"hub returned HTTP {r.status_code}", "body": r.text[:200]}, 502)
    cpuid = (r.json() or {}).get("cpuid", "")
    log(f"[HubChannel] read cpuid={cpuid[:12]}… from {HUB_HOST}")
    return {"ok": True, "cpuid": cpuid, "hub_host": HUB_HOST}


@router.post("/write-identity")
async def hub_write_identity(data: dict = Body(...)):
    """Write the cloud-signed binding onto the OPi's SD over the §6.1 channel.

    body: {hub_uuid, cpuid, binding_signature, key_id, signing_keys} (the /provision result).
    The hub re-verifies against its own cpuid before persisting; on success it unlocks in
    place and reports provisioned=true (operator then restarts it + /confirm to commit quota).
    """
    payload = {k: data.get(k) for k in
               ("hub_uuid", "cpuid", "binding_signature", "key_id", "signing_keys")}
    if not (payload["hub_uuid"] and payload["cpuid"] and payload["binding_signature"] and payload["key_id"]):
        return JSONResponse({"error": "hub_uuid, cpuid, binding_signature, key_id required "
                                      "(provision/sign the hub first)"}, 400)
    url = f"{HUB_HOST}/api/provision/identity"
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(url, json=payload)
    except Exception as e:
        return JSONResponse({"error": f"cannot reach hub at {HUB_HOST} ({e})"}, 502)
    try:
        body = r.json()
    except Exception:
        body = {"body": r.text[:200]}
    if r.status_code != 200:
        # Surface the hub's own reason (cpuid mismatch / bad sig / closed) verbatim.
        status = r.status_code if r.status_code in (400, 404, 409) else 502
        log(f"[HubChannel] write REJECTED by hub ({r.status_code}): {body.get('error')}")
        return JSONResponse({"error": "hub rejected the write", "hub_status": r.status_code, **body}, status)
    log(f"[HubChannel] wrote identity to {HUB_HOST}: hub_uuid={payload['hub_uuid']} → {body.get('provisioned')}")
    return {"ok": True, "hub_host": HUB_HOST, **body}
