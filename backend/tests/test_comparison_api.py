"""Database-backed checks for repeat scope and safe report export."""

from datetime import timedelta
from uuid import uuid4

import pytest
from app.core.db import Session
from app.core.models import Attempt, Event, Job, PendingAction, Record, utcnow
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import delete


@pytest.fixture
def baseline():
    job_id = str(uuid4())
    with Session.begin() as session:
        session.add(
            Job(
                id=job_id,
                url="http://127.0.0.1:8541/faulty/#fragment",
                mode="scripted",
                status="completed",
                deadline=utcnow() + timedelta(minutes=2),
                report={
                    "site_url": "http://127.0.0.1:8541/faulty/",
                    "inspected_pages": ["http://127.0.0.1:8541/faulty/"],
                    "confirmed": [],
                    "findings": [],
                    "observations": [],
                },
            )
        )
    yield job_id
    with Session.begin() as session:
        children = session.query(Job).filter_by(baseline_job_id=job_id).with_for_update().all()
        for child in children:
            for model in (PendingAction, Record, Event, Attempt):
                session.execute(delete(model).where(model.job_id == child.id))
            session.delete(child)
        session.flush()
        session.execute(delete(Job).where(Job.id == job_id))


def test_normalized_target_keeps_baseline_immutable(baseline):
    client = TestClient(app)
    before = client.get(f"/api/jobs/{baseline}").json()
    response = client.post(f"/api/jobs/{baseline}/rerun", json={})
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["baseline_job_id"] == baseline
    assert result["url"] == before["url"]
    assert result["comparison_mapping"] == {}
    assert client.get(f"/api/jobs/{baseline}").json() == before
    assert (
        client.post(f"/api/jobs/{baseline}/rerun", json={"demo_corrected": True}).status_code == 422
    )


def test_unfinished_baseline_rejected(baseline):
    with Session.begin() as session:
        session.get(Job, baseline).status = "running"
    response = TestClient(app).post(f"/api/jobs/{baseline}/rerun", json={})
    assert response.status_code == 409


def test_export_escapes_site_text_and_never_reads_external_image(baseline):
    attack = '<script src="https://evil.test/code.js">alert(1)</script>'
    with Session.begin() as session:
        job = session.get(Job, baseline)
        job.report = {
            **job.report,
            "findings": [
                {
                    "status": "confirmed",
                    "explanation": attack,
                    "urls": [job.url],
                    "visitor_impact": attack,
                    "suggested_correction": attack,
                    "checker_reason": attack,
                    "reproduction_steps": [attack],
                    "evidence": [{"url": job.url, "screenshot": "../../etc/passwd.png"}],
                }
            ],
        }
    response = TestClient(app).get(f"/api/jobs/{baseline}/repair-report")
    assert response.status_code == 200
    assert "<script src=" not in response.text
    assert "&lt;script" in response.text
    assert "screenshot file is unavailable" in response.text
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "attachment;" in response.headers["content-disposition"]
