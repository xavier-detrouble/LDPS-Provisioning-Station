"""LDPS Factory — Provisioning Station application factory."""
import asyncio
import json
import time

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.cors import CORSMiddleware

from app.config import STATIC_DIR, TEMPLATE_DIR, TEST_PACK_DIR
from app.state import AppState
from app.ws_manager import WSManager
from app.utils import log


def _setup_espnow_handler(state: AppState, espnow) -> None:
    """Wire ESP-NOW response handler — routes all upstream messages."""

    def _on_espnow_msg(mac: str, payload: str):
        parts = payload.split(",")
        cmd = parts[0] if parts else ""

        if cmd == "DISCOVER_RSP":
            fw = parts[1] if len(parts) > 1 else "?"
            uuid = parts[2] if len(parts) > 2 else ""
            state.discovered_nodes[mac] = {
                "mac": mac, "fw": fw, "uuid": uuid, "last_seen": time.time()
            }
            if state.ws:
                state.ws.broadcast("discovered", {"nodes": list(state.discovered_nodes.values())})

        elif cmd == "HW_TEST_ACK":
            if state.ws:
                state.ws.broadcast("hw_test", {"mac": mac, "status": "running"})

        elif cmd == "HW_TEST_RESULT":
            json_str = ",".join(parts[1:])
            if state.ws:
                try:
                    results = json.loads(json_str)
                except Exception:
                    results = {"raw": json_str}
                state.ws.broadcast("hw_test", {"mac": mac, "status": "done", "results": results})
            from app.routes.provision import handle_hw_test_result
            handle_hw_test_result(mac, parts)

        elif cmd == "SET_UUID_ACK":
            log(f"[ESPNow] {mac}: SET_UUID_ACK {payload}")
            from app.routes.provision import handle_set_uuid_ack
            handle_set_uuid_ack(mac, parts)

        elif cmd == "NVS_ERASE_ACK":
            log(f"[ESPNow] {mac}: NVS_ERASE_ACK {payload}")
            from app.routes.provision import handle_nvs_erase_ack
            handle_nvs_erase_ack(mac, parts)

        elif cmd == "STATUS_RSP":
            # Parse key=value pairs for sync progress tracking
            kv = {}
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    kv[k] = v
            if state.ws and kv.get("uuid"):
                state.ws.broadcast("node_status", {"mac": mac, **kv})

        elif cmd in ("SYNC_ACK", "SWITCH_PACK_ACK", "CFG_ACK", "PONG"):
            log(f"[ESPNow] {mac}: {payload}")

        else:
            log(f"[ESPNow] {mac}: {payload}", "DEBUG")

    espnow.on_receive(_on_espnow_msg)


def create_app() -> FastAPI:
    app = FastAPI(title="LDPS Factory")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # State
    state = AppState()
    ws_mgr = WSManager()
    state.ws = ws_mgr
    app.state.app_state = state

    # Templates
    templates = Jinja2Templates(directory=TEMPLATE_DIR)

    # Static files
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Serve test pack files (same URLs as Hub for Node compatibility)
    import os
    if os.path.isdir(TEST_PACK_DIR):
        # Pack table
        @app.get("/api/packs/pack_table.json")
        def serve_pack_table():
            import json as _json
            path = os.path.join(TEST_PACK_DIR, "pack_table.json")
            if os.path.exists(path):
                with open(path) as f:
                    return _json.load(f)
            return {"schema": 1, "packs": []}

        # Sequences LUT
        @app.get("/api/packs/{pack_uuid}/sequences_lut.json")
        def serve_sequences_lut(pack_uuid: str):
            import json as _json
            path = os.path.join(TEST_PACK_DIR, pack_uuid, "sequences_lut.json")
            if os.path.exists(path):
                with open(path) as f:
                    return _json.load(f)
            return {"error": "not found"}, 404

        # Assignment
        @app.get("/api/packs/{pack_uuid}/assignments/{node_uuid}.json")
        def serve_assignment(pack_uuid: str, node_uuid: str):
            import json as _json
            path = os.path.join(TEST_PACK_DIR, pack_uuid, "assignments", f"{node_uuid}.json")
            if os.path.exists(path):
                with open(path) as f:
                    return _json.load(f)
            return {"error": "not found"}, 404

        # .lshow files
        from fastapi.responses import FileResponse
        @app.get("/api/lshow/{file_uuid}.lshow")
        def serve_lshow(file_uuid: str):
            path = os.path.join(TEST_PACK_DIR, "lshow", f"{file_uuid}.lshow")
            if os.path.exists(path):
                return FileResponse(path, media_type="application/octet-stream")
            return {"error": "not found"}, 404

    # Routes
    from app.routes import system as system_routes
    from app.routes import dongle as dongle_routes
    from app.routes import cloud as cloud_routes
    from app.routes import flash as flash_routes
    from app.routes import provision as provision_routes
    from app.routes import history as history_routes
    from app.routes import ws as ws_routes

    app.include_router(system_routes.router, prefix="/api/system")
    app.include_router(dongle_routes.router, prefix="/api/dongle")
    app.include_router(cloud_routes.router, prefix="/api/cloud")
    app.include_router(flash_routes.router, prefix="/api/flash")
    app.include_router(provision_routes.router, prefix="/api/provision")
    app.include_router(history_routes.router, prefix="/api/history")
    app.include_router(ws_routes.router)

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    @app.on_event("startup")
    async def startup():
        ws_mgr.set_loop(asyncio.get_event_loop())
        log("[Factory] LDPS Factory started on port 9000")

    @app.on_event("shutdown")
    def shutdown():
        state.stop_event.set()
        if state.dongle:
            state.dongle.close()
        log("[Factory] Shutdown")

    return app
