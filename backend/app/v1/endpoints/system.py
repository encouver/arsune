from fastapi import APIRouter

from app.core.schemas.system import ConfigResponse, HealthResponse
from app.v1.services import system

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health():
    return system.health()


@router.get("/config", response_model=ConfigResponse)
def config():
    return system.config()
