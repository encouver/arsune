from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.v1.services.evidence import evidence_path

router = APIRouter()


@router.get("/jobs/{job_id}/evidence/{name}", response_class=FileResponse)
def evidence(job_id: str, name: str):
    return FileResponse(
        evidence_path(job_id, name),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )
