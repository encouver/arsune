from fastapi import APIRouter

from app.v1.endpoints import bounties, evidence, funding, incidents, jobs, system

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(jobs.router)
api_router.include_router(evidence.router)

api_router.include_router(incidents.router)
api_router.include_router(bounties.router)
api_router.include_router(funding.router)
