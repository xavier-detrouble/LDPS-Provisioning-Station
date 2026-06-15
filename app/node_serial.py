"""USB serial link to a connected Edge-Node — identity provisioning + read-back.

The factory writes a node's identity over USB (NOT over the air); the node's
serial menu provides:
  'i'                          → info (UUID, Genuineness key_id/sig, FW, MAC, ...)
  'P <uuid> <sig> <key_id>'    → write identity (uuid + genuineness, write-once)

Blocking pyserial calls — call these via asyncio.to_thread() from async handlers.
"""
from __future__ import annotations

import re
import time

import serial  # pyserial (in requirements.txt)

from app.utils import log

BAUD = 115200


def _txn(port: str, send: str | None, wait: float = 1.6) -> str:
    """Open `port`, optionally write a line, read for `wait` seconds, return text.
    Reads past the node's interleaved periodic logs by accumulating the window."""
    with serial.Serial(port, BAUD, timeout=0.3) as s:
        time.sleep(0.4)
        s.reset_input_buffer()
        if send is not None:
            s.write((send + "\n").encode())
            s.flush()
        end = time.time() + wait
        buf = b""
        while time.time() < end:
            buf += s.read(8192)
    return buf.decode(errors="replace")


def read_identity(port: str) -> dict:
    """Send 'i' and parse the node's identity fields."""
    t = _txn(port, "i")

    def grab(pat: str, default: str = "") -> str:
        m = re.search(pat, t)
        return m.group(1) if m else default

    return {
        "uuid": grab(r"UUID:\s*([0-9a-fA-F-]{36})"),
        "key_id": grab(r"key_id=(\S+?)\s"),
        "sig": grab(r"sig=([0-9a-f]{128})"),
        "fw": grab(r"FW:\s*(\S+)"),
        "mac": grab(r"MAC=([0-9A-Fa-f:]{17})"),
        "raw": t,
    }


def write_identity(port: str, uuid: str, sig: str, key_id: str) -> dict:
    """Send 'P <uuid> <sig> <key_id>', then read back 'i' to verify the node
    actually stored what we wrote. Returns {ok, uuid, sig, key_id, detail}."""
    resp = _txn(port, f"P {uuid} {sig} {key_id}", wait=2.0)
    prov_lines = [l.strip() for l in resp.splitlines() if "[PROV]" in l]
    if not any("identity written" in l for l in prov_lines):
        return {"ok": False, "error": "write refused/no-ack",
                "detail": prov_lines or resp[-200:].strip()}
    # Read-back verification — the node must report exactly what we wrote.
    ident = read_identity(port)
    ok = (ident.get("uuid") == uuid and ident.get("sig") == sig
          and ident.get("key_id") == key_id)
    if not ok:
        log(f"[NodeSerial] read-back mismatch on {port}: "
            f"wrote uuid={uuid} kid={key_id}, read uuid={ident.get('uuid')} "
            f"kid={ident.get('key_id')}", "ERROR")
    return {"ok": ok, "uuid": ident.get("uuid"), "sig": ident.get("sig"),
            "key_id": ident.get("key_id"),
            "detail": "read-back verified" if ok else "read-back mismatch"}
