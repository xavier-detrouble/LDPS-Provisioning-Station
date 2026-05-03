"""Cloud API client for provisioning operations."""
from __future__ import annotations

import httpx
from app.utils import log


class CloudClient:
    def __init__(self, cloud_url: str):
        self.cloud_url = cloud_url.rstrip("/")
        self.token: str = ""
        self.email: str = ""

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def login(self, email: str, password: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(f"{self.cloud_url}/api/cloud/login",
                                      json={"email": email, "password": password})
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    self.token = data.get("token", "")
                    self.email = email
                    return {"ok": True, "email": email, "name": data.get("name", "")}
            return {"ok": False, "error": r.json().get("error", "Login failed")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def get_quota(self) -> list:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{self.cloud_url}/admin/provision/stats",
                                     headers=self._headers())
            if r.status_code == 200:
                return r.json().get("quotas", [])
            return []
        except Exception:
            return []

    async def request_uuid(self, hardware_serial: str, product_type: str = "default",
                           test_results: dict = None, firmware_ver: str = "") -> str | None:
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
                return r.json().get("uuid")
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
