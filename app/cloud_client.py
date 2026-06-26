"""Cloud API client for provisioning operations.

Uses manufacturer API key authentication (X-Manufacturer-Key header).
Completely separate from Supabase Auth — manufacturers cannot access Studio.
"""
from __future__ import annotations

import httpx
from app.utils import log


class CloudClient:
    def __init__(self, cloud_url: str):
        self.cloud_url = cloud_url.rstrip("/")
        self.api_key: str = ""
        self.manufacturer_id: str = ""
        self.manufacturer_name: str = ""
        self.quotas: list = []

    def _headers(self) -> dict:
        return {"X-Manufacturer-Key": self.api_key} if self.api_key else {}

    async def login(self, api_key: str) -> dict:
        """Authenticate with manufacturer API key."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{self.cloud_url}/provision/login",
                                      json={"api_key": api_key})
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    self.api_key = api_key
                    self.manufacturer_id = data.get("manufacturer_id", "")
                    self.manufacturer_name = data.get("name", "")
                    self.quotas = data.get("quotas", [])
                    return {
                        "ok": True,
                        "name": self.manufacturer_name,
                        "manufacturer_id": self.manufacturer_id,
                        "quotas": self.quotas,
                    }
            err = "Invalid API key"
            try:
                err = r.json().get("error", err)
            except Exception:
                pass
            return {"ok": False, "error": err}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def get_quota(self) -> list:
        """Re-fetch quotas from login endpoint."""
        if not self.api_key:
            return []
        result = await self.login(self.api_key)
        return result.get("quotas", self.quotas)

    async def request_uuid(self, hardware_serial: str, product_type: str,
                           test_results: dict = None, firmware_ver: str = "") -> dict | None:
        """Mint a node UUID. product_type is REQUIRED (cloud QC gate). Returns
        {uuid, signature, key_id, recovery_key} — signature is the cloud's Ed25519
        genuineness sig over the UUID, written to the node over USB and verified by
        Hubs; recovery_key is the per-node re-claim key (returned ONCE, plaintext) the
        operator prints on the box (§3.4) — never written to the node, stored encrypted
        in the cloud. Returns None on failure (signature/key_id may be None if cloud
        signing is not configured; the UUID is still minted)."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{self.cloud_url}/provision/request-uuid",
                                      json={
                                          "hardware_serial": hardware_serial,
                                          "product_type": product_type,
                                          "test_results": test_results,
                                          "firmware_ver": firmware_ver,
                                      },
                                      headers=self._headers())
            if r.status_code == 200:
                d = r.json()
                return {"uuid": d.get("uuid"),
                        "signature": d.get("signature"),
                        "key_id": d.get("key_id"),
                        "recovery_key": d.get("recovery_key")}
            log(f"[Cloud] request-uuid failed: {r.status_code} {r.text}", "WARNING")
            return None
        except Exception as e:
            log(f"[Cloud] request-uuid error: {e}", "ERROR")
            return None

    async def confirm(self, uuid: str, success: bool = True) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{self.cloud_url}/provision/confirm",
                                      json={"uuid": uuid, "success": success},
                                      headers=self._headers())
            return r.status_code == 200
        except Exception:
            return False

    async def report_test_fail(self, hardware_serial: str,
                               test_results: dict = None, reason: str = "") -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{self.cloud_url}/provision/test-fail",
                                      json={
                                          "hardware_serial": hardware_serial,
                                          "test_results": test_results,
                                          "reason": reason,
                                      },
                                      headers=self._headers())
            return r.status_code == 200
        except Exception:
            return False

    # ── Hub provisioning (flow B, §5/§6.1) — the Station reads the assembled OPi's
    #    RK3566 cpuid and the cloud signs the (hub_uuid:cpuid) binding to THAT chip.
    #    The binding_signature + signing_keys are written to the Hub's SD via the Hub
    #    provisioning channel (the "发去hub" step, later); the cloud keeps only key_id.

    async def provision_hub(self, cpuid: str, product_type: str,
                            test_results: dict = None, firmware_ver: str = "",
                            provision_batch: str = "") -> dict:
        """Reserve + sign a hub binding. Returns the cloud response dict:
        success → {ok: True, hub_uuid, cpuid, binding_signature, key_id, signing_keys};
        failure → {ok: False, error, code?}  (codes: UNKNOWN_PRODUCT_TYPE,
        QUOTA_EXHAUSTED, QUOTA_NOT_FOUND, CPUID_EXISTS). product_type must be an
        active device_type='hub' catalog entry."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{self.cloud_url}/provision/hub",
                                      json={
                                          "cpuid": cpuid,
                                          "product_type": product_type,
                                          "test_results": test_results,
                                          "firmware_ver": firmware_ver or None,
                                          "provision_batch": provision_batch or None,
                                      },
                                      headers=self._headers())
            try:
                d = r.json()
            except Exception:
                d = {}
            if r.status_code == 200 and d.get("ok"):
                return d
            log(f"[Cloud] provision-hub failed: {r.status_code} {d}", "WARNING")
            return {"ok": False, "error": d.get("error", f"HTTP {r.status_code}"),
                    "code": d.get("code")}
        except Exception as e:
            log(f"[Cloud] provision-hub error: {e}", "ERROR")
            return {"ok": False, "error": str(e)}

    async def confirm_hub(self, hub_uuid: str, success: bool = True) -> bool:
        """COMMIT (SD binding written) or RELEASE (write failed → frees the quota)."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{self.cloud_url}/provision/hub/confirm",
                                      json={"hub_uuid": hub_uuid, "success": success},
                                      headers=self._headers())
            return r.status_code == 200
        except Exception:
            return False

    async def rebind_hub(self, hub_uuid: str, cpuid: str) -> dict:
        """RMA board swap: re-sign (hub_uuid:new_cpuid), same hub_uuid + owner.
        Returns {ok, hub_uuid, cpuid, binding_signature, key_id, signing_keys} or
        {ok: False, error}."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{self.cloud_url}/provision/hub/rebind",
                                      json={"hub_uuid": hub_uuid, "cpuid": cpuid},
                                      headers=self._headers())
            try:
                d = r.json()
            except Exception:
                d = {}
            if r.status_code == 200 and d.get("ok"):
                return d
            log(f"[Cloud] rebind-hub failed: {r.status_code} {d}", "WARNING")
            return {"ok": False, "error": d.get("error", f"HTTP {r.status_code}")}
        except Exception as e:
            return {"ok": False, "error": str(e)}
