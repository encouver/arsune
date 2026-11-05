import re
from pathlib import Path

from fastapi import HTTPException

from app.core.config import settings
from app.core.db import Session
from app.core.models import Job


def evidence_path(job_id: str, name: str) -> Path:
    if not re.fullmatch(r"[a-f0-9-]{36}", job_id) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+\.png", name
    ):
        raise HTTPException(404, "Evidence not found")
    with Session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job.kind != "website":
            raise HTTPException(422, "Use incident source evidence for this job.")
    root = (settings.evidence_dir / job_id).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "Evidence not found")
    return path
