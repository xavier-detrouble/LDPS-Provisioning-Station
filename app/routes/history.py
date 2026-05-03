"""Provision history routes."""
from fastapi import APIRouter, Request, Query

router = APIRouter()


def _s(r: Request):
    return r.app.state.app_state


def _log(r: Request):
    s = _s(r)
    if not hasattr(s, 'provision_log') or s.provision_log is None:
        from app.provision_log import ProvisionLog
        s.provision_log = ProvisionLog()
    return s.provision_log


@router.get("/")
def list_history(request: Request,
                 limit: int = Query(100, le=500),
                 offset: int = Query(0, ge=0),
                 status: str = Query(""),
                 search: str = Query("")):
    log = _log(request)
    return {"logs": log.list(limit=limit, offset=offset, status=status, search=search)}


@router.get("/stats")
def history_stats(request: Request):
    log = _log(request)
    return log.stats()
