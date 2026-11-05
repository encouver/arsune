from fastapi import APIRouter

from app.v1.endpoints import evidence, incidents, jobs, system

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(jobs.router)
api_router.include_router(evidence.router)

api_router.include_router(incidents.router)
