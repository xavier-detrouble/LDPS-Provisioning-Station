"""WebSocket connection manager — thread-safe broadcast hub.
Copied from Control-Hub with no modifications."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Set

from fastapi import WebSocket

from app.utils import log


class WSManager:
    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        with self._lock:
            self._connections.add(ws)
        log(f"[WS] client connected ({len(self._connections)} total)")

    def disconnect(self, ws: WebSocket) -> None:
        with self._lock:
            self._connections.discard(ws)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._connections)

    async def _async_broadcast(self, message: str) -> None:
        with self._lock:
            targets = list(self._connections)
        stale: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                stale.append(ws)
        if stale:
            with self._lock:
                for ws in stale:
                    self._connections.discard(ws)

    def broadcast(self, topic: str, data: Any) -> None:
        if not self._loop or not self._connections:
            return
        msg = json.dumps({"topic": topic, "data": data, "ts": time.time()})
        try:
            self._loop.call_soon_threadsafe(
                asyncio.ensure_future, self._async_broadcast(msg)
            )
        except RuntimeError:
            pass

    async def send_to(self, ws: WebSocket, topic: str, data: Any) -> None:
        msg = json.dumps({"topic": topic, "data": data, "ts": time.time()})
        await ws.send_text(msg)
