"""Incident API checks against PostgreSQL and the durable worker store."""

import hashlib
import json
from uuid import uuid4

import pytest
from app.core.db import Session, engine
from app.core.models import Attempt, Event, IncidentSource, Job, PendingAction, Record
from app.main import app
from app.v1.services import incidents
from app.workers.tasks import scan
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.exc import DBAPIError


def bundle():
    return {
        "description": "Checkout failures",
        "service": "checkout",
        "start": "2026-11-05T09:50:00Z",
        "end": "2026-11-05T10:20:00Z",
        "events_text": json.dumps(
            {
                "id": "error-1",
                "timestamp": "2026-11-05T10:01:00Z",
                "service": "checkout",
                "level": "error",
                "message": "<script>inert</script> missing column coupon_code",
                "error_code": "missing_column",
                "column": "coupon_code",
                "revision": "v2",
                "request_id": "request-1",
            }
        )
        + "\n",
        "deployments_text": json.dumps(
            [
                {
                    "id": "deploy-1",
                    "timestamp": "2026-11-05T10:00:00Z",
                    "service": "checkout",
                    "revision": "v2",
                    "migration_status": "failed",
                    "migration_column": "coupon_code",
                }
            ],
            indent=2,
        ),
    }


@pytest.fixture
def client():
    assert engine.dialect.name == "postgresql"
    ids = []
    test_client = TestClient(app)
    original = test_client.post

    def post(*args, **kwargs):
        result = original(*args, **kwargs)
        if result.status_code == 202:
            ids.append(result.json()["id"])
        return result

    test_client.post = post
    yield test_client
    with Session.begin() as session:
        for model in (IncidentSource, Record, Event, Attempt, PendingAction):
            session.execute(delete(model).where(model.job_id.in_(ids)))
        session.execute(delete(Job).where(Job.id.in_(ids)))


def test_atomic_sources_queue_history_and_worker(client):
    payload = bundle()
    response = client.post("/api/incidents", json=payload)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert response.json()["kind"] == "incident"
    with Session() as session:
        job = session.get(Job, job_id)
        assert job.status == "queued"
        assert session.get(PendingAction, f"{job_id}:scan")
        sources = session.query(IncidentSource).filter_by(job_id=job_id).all()
        assert len(sources) == 2
        assert {s.filename: s.content for s in sources} == incidents.load_sources(job_id)
        for source in sources:
            assert source.sha256 == hashlib.sha256(source.content.encode()).hexdigest()
        assert "events_text" not in job.incident_input
    assert payload["events_text"] not in response.text
    history = client.get("/api/jobs?limit=50").json()["items"]
    row = next(row for row in history if row["id"] == job_id)
    assert row["kind"] == "incident" and row["title"] == "Incident: checkout"
    assert "sources" not in row
    scan.run(job_id)
    finished = client.get(f"/api/jobs/{job_id}").json()
    assert finished["status"] == "completed", finished
    assert finished["report"]["service"] == "checkout"
    assert finished["generation"] == 1
    scan.run(job_id)
    assert client.get(f"/api/jobs/{job_id}").json() == finished
    assert client.post(f"/api/jobs/{job_id}/rerun", json={}).status_code == 422
    assert client.get(f"/api/jobs/{job_id}/repair-report").status_code == 422
    assert client.get(f"/api/jobs/{job_id}/evidence/screenshot.png").status_code == 422


def test_exact_evidence_selection_and_immutable_hash(client):
    payload = bundle()
    job_id = client.post("/api/incidents", json=payload).json()["id"]
    route = f"/api/incidents/{job_id}/evidence/"
    result = client.get(route + "events.ndjson", params={"record_id": "error-1"})
    assert result.status_code == 200
    value = result.json()
    assert value["raw"] == payload["events_text"].rstrip("\n")
    assert value["record"]["message"].startswith("<script>")
    assert "&lt;script&gt;" in value["excerpt"]
    assert value["sha256"] == hashlib.sha256(payload["events_text"].encode()).hexdigest()
    deployment = client.get(route + "deployments.json", params={"record_id": "deploy-1"}).json()
    assert deployment["line"] is None and deployment["record_index"] == 1
    assert client.get(route + "deployments.json", params={"line": 1}).status_code == 422
    assert client.get(route + "events.ndjson", params={"line": 1}).json() == value
    assert client.get(route + "events.ndjson").status_code == 422
    assert client.get(route + "events.ndjson", params={"record_id": "missing"}).status_code == 404
    assert client.get(route + ".env", params={"line": 1}).status_code == 404
    with pytest.raises(DBAPIError), Session.begin() as session:
        session.execute(
            text("UPDATE incident_sources SET content = 'changed' WHERE job_id = :id"),
            {"id": job_id},
        )
    # A mismatched source hash must fail closed even if privileged corruption bypasses the trigger.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE incident_sources DISABLE TRIGGER immutable_incident_sources"
        )
        connection.execute(
            text("UPDATE incident_sources SET sha256 = :hash WHERE job_id = :id"),
            {"hash": "0" * 64, "id": job_id},
        )
        connection.exec_driver_sql(
            "ALTER TABLE incident_sources ENABLE TRIGGER immutable_incident_sources"
        )
    assert client.get(route + "events.ndjson", params={"line": 1}).status_code == 409
    with pytest.raises(ValueError, match="hash"):
        incidents.load_sources(job_id)


def test_reject_invalid_and_bounded_uploads(client, monkeypatch):
    payload = bundle()
    for key, value in (
        ("start", "2026-11-05T09:50:00"),
        ("description", "\ud800"),
        ("events_text", "\ud800"),
        ("description", "bad\x00description"),
        ("service", ""),
        ("events_text", "{}\n"),
    ):
        assert (
            client.post(
                "/api/incidents", content=json.dumps({**payload, key: value}).encode()
            ).status_code
            == 422
        )
    assert client.post("/api/incidents", content=b"{oops").status_code == 422
    assert (
        client.post("/api/incidents", json={**payload, "url": "file:///etc/passwd"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/incidents",
            json={**payload, "events_text": "x" * (incidents.MAX_SOURCE_BYTES + 1)},
        ).status_code
        == 413
    )
    monkeypatch.setattr(incidents, "MAX_REQUEST_BYTES", 100)
    assert client.post("/api/incidents", content=(b"x" * 101)).status_code == 413
    assert client.get(f"/api/incidents/{uuid4()}/evidence/events.ndjson?line=1").status_code == 404
