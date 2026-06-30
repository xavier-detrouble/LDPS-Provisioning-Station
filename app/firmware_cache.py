"""Firmware / program cache — the Station is the factory's cloud-facing gateway.

For node firmware, the hub program, and the dongle firmware, the Station fetches the
latest build per product from the cloud registry (`GET /firmware/latest?product=`),
**version-checks** against a small local cache, and **downloads only when a newer
version exists** — so a production line is never interrupted by a download on every
unit (only when the published version actually changed). The device itself stays
offline; the Station pushes/flashes the cached binary.

Returns dicts with: ok, version, path, changed, plus error/offline/note on edge cases.
"""
from __future__ import annotations

import hashlib
import json
import os

import httpx

from app.config import STATIC_DIR

CACHE_DIR = os.path.join(STATIC_DIR, "firmware_cache")


def _slug(s: str) -> str:
    return "".join(c if (c.isalnum() or c in "._-") else "-" for c in (s or "_"))


def _read_manifest(man_path: str) -> dict:
    if os.path.exists(man_path):
        try:
            with open(man_path) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def cached_version(product: str, channel: str = "stable") -> str | None:
    """The version currently in the local cache for this product/channel, or None."""
    man = _read_manifest(os.path.join(CACHE_DIR, _slug(product), _slug(channel), "manifest.json"))
    p = man.get("path")
    return man.get("version") if (p and os.path.exists(p)) else None


async def get_latest(cloud_url: str, product: str, channel: str = "stable",
                     timeout: float = 15.0) -> dict:
    """Ensure the latest published build for `product`/`channel` is in the local cache.

    Cheap version check first; downloads + sha256-verifies only on a version change.
    On a network failure, falls back to the cached copy if present.
    """
    pdir = os.path.join(CACHE_DIR, _slug(product), _slug(channel))
    os.makedirs(pdir, exist_ok=True)
    man_path = os.path.join(pdir, "manifest.json")
    cached = _read_manifest(man_path)
    cached_ok = bool(cached.get("path") and os.path.exists(cached.get("path", "")))

    # 1. cheap version check
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{cloud_url}/firmware/latest",
                                  params={"product": product, "channel": channel})
        d = r.json() if r.status_code == 200 else {}
    except Exception as e:
        if cached_ok:
            return {"ok": True, "version": cached.get("version"), "path": cached["path"],
                    "changed": False, "offline": True}
        return {"ok": False, "error": f"version check failed: {e}"}

    latest = d.get("version")
    if not latest:
        if cached_ok:
            return {"ok": True, "version": cached.get("version"), "path": cached["path"],
                    "changed": False, "note": "no published release — using cache"}
        return {"ok": False, "error": f"no published firmware for {product} ({channel})"}

    # 2. already the latest → no download (don't interrupt the line)
    if cached.get("version") == latest and cached_ok:
        return {"ok": True, "version": latest, "path": cached["path"], "changed": False}

    # 3. new version → download once + verify sha256
    dl = d.get("download_url")
    if not dl:
        return {"ok": False, "error": "latest release has no download_url"}
    vdir = os.path.join(pdir, _slug(latest))
    os.makedirs(vdir, exist_ok=True)
    fpath = os.path.join(vdir, "firmware.bin")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(dl)
            resp.raise_for_status()
            content = resp.content
        with open(fpath, "wb") as f:
            f.write(content)
    except Exception as e:
        return {"ok": False, "error": f"download failed: {e}"}

    sha = d.get("sha256")
    if sha:
        got = hashlib.sha256(content).hexdigest()
        if got.lower() != str(sha).lower():
            try:
                os.remove(fpath)
            except OSError:
                pass
            return {"ok": False, "error": f"sha256 mismatch (got {got[:12]}…, want {str(sha)[:12]}…)"}

    with open(man_path, "w") as f:
        json.dump({"product": product, "channel": channel, "version": latest,
                   "sha256": sha, "path": fpath}, f)
    return {"ok": True, "version": latest, "path": fpath, "changed": True}
