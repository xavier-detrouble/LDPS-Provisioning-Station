"""Utility functions."""
import datetime
import serial.tools.list_ports


def log(msg: str, level: str = "INFO"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def list_serial_ports() -> list[dict]:
    """List available serial ports."""
    ports = []
    for p in serial.tools.list_ports.comports():
        ports.append({
            "device": p.device,
            "description": p.description,
            "hwid": p.hwid,
        })
    return ports
