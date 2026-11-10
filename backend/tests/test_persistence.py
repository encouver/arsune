"""Integration checks against the configured PostgreSQL database."""

from datetime import timedelta
from uuid import uuid4

import pytest
from app.core.config import settings
from app.core.db import Session
from app.core.models import Attempt, Event, Job, PendingAction, Record, utcnow
from app.main import app
from app.workers import coordinator, store
from fastapi.testclient import TestClient
from sqlalchemy import delete, select


@pytest.fixture
def job_id():
    value = str(uuid4())
    with Session.begin() as session:
        session.add(
            Job(
                id=value,
                url="http://127.0.0.1:8541/faulty/",
                mode="scripted",
                deadline=utcnow() + timedelta(minutes=3),
            )
        )
    yield value
    with Session.begin() as session:
        for model in (Record, Event, Attempt, PendingAction):
            session.execute(delete(model).where(model.job_id == value))
        session.execute(delete(Job).where(Job.id == value))


def test_expired_worker_cannot_change_new_generation(job_id):
    first = store.claim(job_id)
    store.save_checkpoint(job_id, first["generation"], {"stage": "observed"})
    with Session.begin() as session:
        session.get(Job, job_id).lease_until = utcnow() - timedelta(seconds=1)
    second = store.claim(job_id)
    assert second["checkpoint"] == {"stage": "observed"}
    with pytest.raises(store.LeaseLost):
        store.finish(job_id, first["generation"], report={"bad": True})
    store.finish(job_id, second["generation"], report={"findings": []})
    assert store.claim(job_id) is None


def test_repeat_checkpoint_and_event_are_unique(job_id):
    generation = store.claim(job_id)["generation"]
    for _ in range(2):
        store.save_checkpoint(
            job_id,
            generation,
            {"candidates": [{"id": "candidate-1"}], "findings": [{"id": "candidate-1"}]},
        )
        store.emit_event(job_id, generation, "checker", "Repeated claim checked")
    with Session() as session:
        records = session.scalars(select(Record).where(Record.job_id == job_id)).all()
        events = session.scalars(select(Event).where(Event.job_id == job_id)).all()
        assert len(records) == 2
        assert len(events) == 1


def test_api_blocks_private_target_and_evidence_escape(job_id):
    client = TestClient(app)
    assert client.post("/api/jobs", json={"url": "http://127.0.0.1:9999/"}).status_code == 422
    assert client.post("/api/jobs", json={"url": "file:///etc/passwd"}).status_code == 422
    assert client.get(f"/api/jobs/{job_id}/evidence/..%2F..%2F.env").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/evidence/.env").status_code == 404
    assert client.get("/api/jobs/missing").status_code == 404


def test_the_deadline_starts_when_a_worker_takes_the_job(job_id, monkeypatch):
    sent = []
    monkeypatch.setattr(coordinator.app, "send_task", lambda name, args: sent.append(args))
    with Session.begin() as session:  # the job waited in a busy queue past its first deadline
        session.get(Job, job_id).deadline = utcnow() - timedelta(seconds=1)
    coordinator.tick()
    with Session() as session:
        assert session.get(Job, job_id).status == "queued"
    assert [job_id] in sent
    started = store.claim(job_id)
    assert started, "a queued job has not used any of its time"
    left = started["deadline"] - utcnow()
    assert left > timedelta(seconds=settings.job_deadline_seconds - 30)


def test_deadline_failure_clears_pending_work(job_id):
    assert store.claim(job_id)
    with Session.begin() as session:
        job = session.get(Job, job_id)
        job.deadline = job.lease_until = utcnow() - timedelta(seconds=1)
        session.add(PendingAction(id=f"{job_id}:scan", job_id=job_id))
    assert store.claim(job_id) is None
    with Session() as session:
        job = session.get(Job, job_id)
        assert job.status == "failed"
        assert job.progress["stage"] == "failed"
        assert job.lease_until is None
        assert session.get(PendingAction, f"{job_id}:scan") is None


def test_example_configuration_accepts_empty_model_prices():
    from app.core.config import Settings

    config = Settings(_env_file=".env.example")
    assert config.model_input_usd_per_million is None
    assert config.model_output_usd_per_million is None


def test_job_response_preserves_report_fields_and_timestamps(job_id):
    report = {
        "findings": [{"id": "finding-1", "measurement": {"width": 901}}],
        "coverage": {"requests": 23},
        "provider": {"live_call": False},
    }
    with Session.begin() as session:
        job = session.get(Job, job_id)
        job.report = report
        event = Event(id=f"{job_id}:event", job_id=job_id, role="checker", message="Checked")
        session.add(event)
        session.flush()
        expected_created = job.created_at.isoformat()
        expected_updated = job.updated_at.isoformat()
        expected_event_created = event.created_at.isoformat()

    response = TestClient(app).get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    value = response.json()
    assert value["report"] == report
    assert value["findings"] == report["findings"]
    assert value["created_at"] == expected_created
    assert value["updated_at"] == expected_updated
    assert value["events"][0]["created_at"] == expected_event_created
