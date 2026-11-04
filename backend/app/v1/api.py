from fastapi import APIRouter

from app.v1.endpoints import evidence, jobs, system

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(jobs.router)
api_router.include_router(evidence.router)
