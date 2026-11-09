import re
from pathlib import Path

from fastapi import HTTPException

from app.core.config import settings
from app.core.db import Session
from app.v1.services.jobs import find_job


def evidence_path(job_id: str, name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.png", name):
        raise HTTPException(404, "Evidence not found")
    # find_job accepts only canonical UUIDs, so job_id is also a safe directory name.
    with Session() as session:
        if find_job(session, job_id).kind not in ("website", "claim"):
            raise HTTPException(422, "Use incident source evidence for this job.")
    root = (settings.evidence_dir / job_id).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "Evidence not found")
    return path
