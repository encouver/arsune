import re
from datetime import timedelta
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from arsune.config import settings
from arsune.db import Event, Job, PendingAction, Session, utcnow

app = FastAPI(title="Arsune", docs_url="/api/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.ui_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class CreateJob(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    mode: Literal["scripted", "model"] = "scripted"
    demo_fault_injection: bool = False


def detail(session, job):
    events = session.scalars(
        select(Event).where(Event.job_id == job.id).order_by(Event.created_at, Event.id)
    ).all()
    return {
        "id": job.id,
        "url": job.url,
        "mode": job.mode,
        "status": job.status,
        "progress": job.progress,
        "generation": job.generation,
        "events": [
            {"id": e.id, "role": e.role, "message": e.message, "created_at": e.created_at}
            for e in events
        ],
        "report": job.report,
        "findings": (job.report or {}).get("findings", []),
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


@app.get("/api/health")
def health():
    with Session() as session:
        session.execute(select(1))
    return {"status": "ok"}


@app.get("/api/config")
def config():
    return {
        "modes": ["scripted", "model"] if settings.model_enabled else ["scripted"],
        "demo_urls": {
            "faulty": settings.demo_url + "/faulty/",
            "corrected": settings.demo_url + "/corrected/",
        },
        "max_pages": 5,
        "access": "local operator only",
    }


@app.post("/api/jobs", status_code=202)
async def create_job(request: CreateJob):
    from arsune.engine.policy import TargetPolicyError, origin, validate_target

    try:
        await validate_target(request.url, demo_origin=settings.demo_url)
    except (TargetPolicyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if request.mode == "model" and not settings.model_enabled:
        raise HTTPException(409, "Model mode needs a configured endpoint, model, and credential.")
    if request.demo_fault_injection and origin(request.url) != origin(settings.demo_url):
        raise HTTPException(422, "False candidates are limited to the controlled demo.")
    job_id = str(uuid4())
    with Session.begin() as session:
        job = Job(
            id=job_id,
            url=request.url,
            mode=request.mode,
            inject_demo=request.demo_fault_injection,
            deadline=utcnow() + timedelta(seconds=settings.job_deadline_seconds),
        )
        session.add(job)
        session.flush()
        session.add(PendingAction(id=f"{job_id}:scan", job_id=job_id))
        return detail(session, job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with Session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return detail(session, job)


@app.get("/api/jobs/{job_id}/evidence/{name}")
def evidence(job_id: str, name: str):
    if not re.fullmatch(r"[a-f0-9-]{36}", job_id) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+\.png", name
    ):
        raise HTTPException(404, "Evidence not found")
    with Session() as session:
        if not session.get(Job, job_id):
            raise HTTPException(404, "Job not found")
    root = (settings.evidence_dir / job_id).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "Evidence not found")
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )
