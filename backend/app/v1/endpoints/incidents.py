import json

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.requests import ClientDisconnect

from app.core.schemas.jobs import JobDetail
from app.v1.services import incidents

router = APIRouter()


@router.post("/incidents", status_code=202, response_model=JobDetail)
async def create_incident(request: Request):
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > incidents.MAX_REQUEST_BYTES:
                raise HTTPException(413, "Incident request exceeds the upload limit.")
            body.extend(chunk)
    except ClientDisconnect:
        raise HTTPException(400, "Incident upload was interrupted.") from None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "Supply a valid JSON incident request.") from None
    return incidents.create_incident(payload)


@router.get("/incidents/{job_id}/evidence/{filename}")
def source_evidence(
    job_id: str,
    filename: str,
    record_id: str | None = Query(None, max_length=200),
    line: int | None = Query(None, ge=1, le=5000),
):
    return incidents.source_evidence(job_id, filename, record_id, line)
