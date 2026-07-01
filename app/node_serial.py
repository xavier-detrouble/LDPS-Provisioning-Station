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


def _open(port: str, retries: int = 12, delay: float = 0.8) -> "serial.Serial":
    """Open the node serial port, retrying through the USB-CDC re-enumeration window.
    After an esptool flash the ESP32-S3 hard-resets (`--after hard_reset`) and its
    native USB-CDC port disappears for several seconds; a stale handle raises
    errno 19 ('Operation not supported by device') / SerialException. We retry for
    ~10s so the post-flash `format_sd` (and `read_identity`) don't spuriously fail."""
    last = None
    for _ in range(max(1, retries)):
        try:
            return serial.Serial(port, BAUD, timeout=0.3)
        except (serial.SerialException, OSError) as e:
            last = e
            time.sleep(delay)
    raise last if last is not None else serial.SerialException(f"cannot open {port}")


def _txn(port: str, send: str | None, wait: float = 1.6) -> str:
    """Open `port`, optionally write a line, read for `wait` seconds, return text.
    Reads past the node's interleaved periodic logs by accumulating the window."""
    with _open(port) as s:
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


def format_sd(port: str) -> dict:
    """Format the node's SD card to FAT32 via the node serial 'f' command (FatFS
    f_mkfs). Done right after a fresh flash so the production SD starts clean (the
    test rig always has the node on USB, so the serial path is fine). Returns
    {ok, detail}. Destructive — wipes the card; only used in the factory flash flow."""
    resp = _txn(port, "f", wait=8.0)  # f_mkfs on a large card + re-scan takes a few s
    if "Format OK" in resp:
        return {"ok": True, "detail": "FAT32 formatted"}
    if "SD not mounted" in resp:
        return {"ok": False, "detail": "SD not mounted — cannot format"}
    m = re.search(r"Format failed:.*", resp)
    return {"ok": False, "detail": m.group(0) if m else "no Format-OK ack"}


def clear_identity(port: str) -> dict:
    """Send 'U' to un-provision the node — clears uuid + genuineness sig + key_id +
    owner from NVS (NOT the SD). Node identity is write-once, so RE-provisioning a node
    that already has a UUID requires clearing it first. Returns {ok, detail}. Used by
    the RMA / re-provision path so the operator doesn't have to reflash just to re-mint."""
    resp = _txn(port, "U", wait=2.0)
    if "identity cleared" in resp:
        return {"ok": True, "detail": "identity cleared -> AWAIT_UUID"}
    return {"ok": False, "detail": "no clear ack: " + resp[-160:].strip()}


def write_identity(port: str, uuid: str, sig: str, key_id: str) -> dict:
    """Send 'P <uuid> <sig> <key_id>', then read back 'i' to verify the node
    actually stored what we wrote. Returns {ok, uuid, sig, key_id, detail}."""
    resp = _txn(port, f"P {uuid} {sig} {key_id}", wait=2.0)
    prov_lines = [l.strip() for l in resp.splitlines() if "[PROV]" in l]
    if not any("identity written" in l for l in prov_lines):
        return {"ok": False, "error": "write refused/no-ack",
                "detail": prov_lines or resp[-200:].strip()}
    # Read-back verification — the node must report exactly what we wrote. RETRY it: the
    # write acked ('[PROV] identity written') but the very first 'i' read-back sometimes comes
    # back EMPTY (the port close/reopen right after 'P' races the node's USB-CDC / it hasn't
    # settled), which falsely failed a write that actually stuck. Re-read a few times.
    ident = {}
    for _ in range(5):
        ident = read_identity(port)
        if (ident.get("uuid") == uuid and ident.get("sig") == sig
                and ident.get("key_id") == key_id):
            return {"ok": True, "uuid": uuid, "sig": sig, "key_id": key_id,
                    "detail": "read-back verified"}
        time.sleep(0.6)
    log(f"[NodeSerial] read-back mismatch on {port} after retries: "
        f"wrote uuid={uuid} kid={key_id}, read uuid={ident.get('uuid')} "
        f"kid={ident.get('key_id')}", "ERROR")
    return {"ok": False, "uuid": ident.get("uuid"), "sig": ident.get("sig"),
            "key_id": ident.get("key_id"), "detail": "read-back mismatch (after retries)"}
