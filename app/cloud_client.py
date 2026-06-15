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
        {uuid, signature, key_id} — signature is the cloud's Ed25519 genuineness
        sig over the UUID, written to the node over USB and verified by Hubs.
        Returns None on failure (signature/key_id may be None if cloud signing
        is not configured; the UUID is still minted)."""
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
                        "key_id": d.get("key_id")}
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
