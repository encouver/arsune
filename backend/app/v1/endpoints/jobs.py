from fastapi import APIRouter

from app.core.schemas.jobs import CreateJob, JobDetail
from app.v1.services import jobs

router = APIRouter()


@router.post("/jobs", status_code=202, response_model=JobDetail)
async def create_job(request: CreateJob):
    return await jobs.create_job(request)


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str):
    return jobs.get_job(job_id)
