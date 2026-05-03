"""System info routes."""
from fastapi import APIRouter
from app.utils import list_serial_ports

router = APIRouter()


@router.get("/ports")
def get_ports():
    return {"ports": list_serial_ports()}
