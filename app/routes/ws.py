"""WebSocket endpoint."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

router = APIRouter()


def _s(request_or_ws):
    return request_or_ws.app.state.app_state


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    s = ws.app.state.app_state
    await s.ws.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        s.ws.disconnect(ws)
