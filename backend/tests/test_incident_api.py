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


def test_download_incident_report_is_portable_and_escaped(client):
    from html import escape

    payload = bundle()
    payload["description"] = "<img src=x onerror=alert(1)> Checkout failures"
    job_id = client.post("/api/incidents", json=payload).json()["id"]
    route = f"/api/incidents/{job_id}/report"
    assert client.get(route).status_code == 409
    scan.run(job_id)
    response = client.get(route)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        f'attachment; filename="arsune-incident-{job_id}.html"'
    )
    assert response.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "base-uri 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    html = response.text
    assert "<script>" not in html and "<img " not in html
    assert "&lt;script&gt;inert&lt;/script&gt;" in html
    assert escape(payload["description"]) in html
    assert escape(payload["events_text"].rstrip("\n")) in html
    # Exact JSON record text retains the accepted internal deployment spacing.
    deployment_text = payload["deployments_text"]
    assert (
        escape(deployment_text[deployment_text.index("{") : deployment_text.rindex("}") + 1])
        in html
    )
    assert '"migration_status": "failed"'.replace('"', "&quot;") in html
    for title in (
        "Timeline",
        "Observed facts",
        "Supported explanations",
        "Rejected explanations",
        "Could not verify",
        "Missing evidence",
        "Next checks",
        "Coverage and omissions",
        "Mode: scripted",
    ):
        assert title in html
    for key in ("events_text", "deployments_text"):
        assert hashlib.sha256(payload[key].encode()).hexdigest() in html
    assert 'href="#evidence-' in html
    assert 'href="http' not in html and 'src="http' not in html
    assert "No cause was reproduced" in html


def test_report_guards_sources_and_saved_citations(client):
    job_id = client.post("/api/incidents", json=bundle()).json()["id"]
    scan.run(job_id)
    route = f"/api/incidents/{job_id}/report"
    assert client.get(f"/api/incidents/{uuid4()}/report").status_code == 404
    with Session.begin() as session:
        job = session.get(Job, job_id)
        original = job.report
        job.report = {
            **original,
            "timeline": [
                {
                    **original["timeline"][0],
                    "evidence": {
                        **original["timeline"][0]["evidence"],
                        "record_id": "../../etc/passwd",
                    },
                }
            ],
        }
    assert client.get(route).status_code == 409
    with Session.begin() as session:
        job = session.get(Job, job_id)
        job.report = {**original, "coverage": {**original["coverage"], "source_hashes": {}}}
    assert client.get(route).status_code == 409
    with Session.begin() as session:
        job = session.get(Job, job_id)
        job.report = original
        job.kind = "website"
    assert client.get(route).status_code == 422
    with Session.begin() as session:
        session.get(Job, job_id).kind = "incident"
        session.execute(
            delete(IncidentSource).where(
                IncidentSource.job_id == job_id,
                IncidentSource.filename == "deployments.json",
            )
        )
    response = client.get(route)
    assert response.status_code == 409 and "unavailable" in response.text


def test_report_rejects_tampered_source(client):
    job_id = client.post("/api/incidents", json=bundle()).json()["id"]
    scan.run(job_id)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE incident_sources DISABLE TRIGGER immutable_incident_sources"
        )
        try:
            connection.execute(
                text("UPDATE incident_sources SET content = :value WHERE job_id = :id"),
                {"value": "tampered", "id": job_id},
            )
        finally:
            connection.exec_driver_sql(
                "ALTER TABLE incident_sources ENABLE TRIGGER immutable_incident_sources"
            )
    response = client.get(f"/api/incidents/{job_id}/report")
    assert response.status_code == 409 and "hash" in response.text


def test_report_evidence_limits_and_known_bundle(client, monkeypatch):
    from pathlib import Path

    from app.v1.services import incident_report

    fixture = Path(__file__).parent / "fixtures/incidents/known"
    payload = json.loads((fixture / "request.json").read_text())
    payload.update(
        events_text=(fixture / "events.ndjson").read_text(),
        deployments_text=(fixture / "deployments.json").read_text(),
    )
    job_id = client.post("/api/incidents", json=payload).json()["id"]
    scan.run(job_id)
    route = f"/api/incidents/{job_id}/report"
    response = client.get(route)
    assert response.status_code == 200, response.text
    assert "failed migration" in response.text
    assert "checkout_token" in response.text
    assert "timeline omitted" in response.text
    monkeypatch.setattr(incident_report, "MAX_RECORD_BYTES", 1)
    limited = client.get(route)
    assert limited.status_code == 200
    assert "exact record omitted: export size limit" in limited.text
    assert "<pre>{" not in limited.text.split("<h2>Exact cited source records</h2>")[1]
    monkeypatch.setattr(incident_report, "MAX_RECORD_BYTES", 64_000)
    monkeypatch.setattr(incident_report, "MAX_EVIDENCE_BYTES", 1)
    assert "exact record omitted: export size limit" in client.get(route).text
    monkeypatch.setattr(incident_report, "MAX_OUTPUT_BYTES", 1)
    assert client.get(route).status_code == 409
