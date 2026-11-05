from fastapi import APIRouter, Query

from app.core.schemas.jobs import CreateJob, JobDetail, JobPage, RerunJob
from app.v1.services import jobs

router = APIRouter()


@router.post("/jobs", status_code=202, response_model=JobDetail)
async def create_job(request: CreateJob):
    return await jobs.create_job(request)


@router.get("/jobs", response_model=JobPage)
def list_jobs(
    limit: int = Query(10, ge=1, le=50), cursor: str | None = Query(None, max_length=512)
):
    return jobs.list_jobs(limit, cursor)


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str):
    return jobs.get_job(job_id)


@router.post("/jobs/{job_id}/rerun", status_code=202, response_model=JobDetail)
async def rerun_job(job_id: str, request: RerunJob):
    return await jobs.rerun_job(job_id, request)


@router.get("/jobs/{job_id}/repair-report")
def repair_report(job_id: str):
    from app.v1.services.repair_report import render_repair_report

    return render_repair_report(job_id)
