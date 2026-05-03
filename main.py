#!/usr/bin/env python3
"""LDPS Provisioning Station — entry point."""
import socket
import sys

import uvicorn

from app import create_app
from app.config import PORT


def check_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


if __name__ == "__main__":
    if not check_port(PORT):
        print(f"[Station] Port {PORT} already in use")
        sys.exit(1)

    app = create_app()
    print(f"[Station] Starting on http://0.0.0.0:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
