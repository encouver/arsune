"""PostgreSQL history checks: stable pages and metadata-only reads."""

from datetime import timedelta
from uuid import uuid4

import pytest
from app.core.db import Session, engine
from app.core.models import Job, utcnow
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import delete, event


@pytest.fixture
def saved_jobs():
    ids = sorted(str(uuid4()) for _ in range(4))
    timestamp = utcnow() + timedelta(days=2)
    with Session.begin() as session:
        for index, job_id in enumerate(ids):
            session.add(
                Job(
                    id=job_id,
                    url=f"https://history.test/{index}",
                    mode="scripted",
                    status=["completed", "failed", "completed", "failed"][index],
                    created_at=timestamp,
                    updated_at=timestamp,
                    deadline=timestamp,
                    report={"private_report": "must not be loaded"},
                    checkpoint={"private_checkpoint": "must not be loaded"},
                )
            )
        session.flush()
        session.get(Job, ids[-1]).baseline_job_id = ids[0]
    yield ids
    with Session.begin() as session:
        session.get(Job, ids[-1]).baseline_job_id = None
        session.flush()
        session.execute(delete(Job).where(Job.id.in_(ids)))


def test_history_keyset_pages_and_metadata_only(saved_jobs):
    assert engine.dialect.name == "postgresql"
    client = TestClient(app)
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get("/api/jobs?limit=2")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    first = response.json()
    assert [row["id"] for row in first["items"]] == saved_jobs[::-1][:2]
    assert first["items"][0]["baseline_job_id"] == saved_jobs[0]
    assert set(first["items"][0]) == {
        "id",
        "url",
        "mode",
        "status",
        "baseline_job_id",
        "created_at",
        "updated_at",
    }
    assert all("report" not in query and "checkpoint" not in query for query in statements)
    # A newer row must not shift or repeat the next page.
    new_id = str(uuid4())
    with Session.begin() as session:
        session.add(
            Job(
                id=new_id,
                url="https://history.test/new",
                mode="scripted",
                status="completed",
                created_at=utcnow() + timedelta(days=3),
                deadline=utcnow(),
            )
        )
    try:
        second = client.get("/api/jobs", params={"limit": 2, "cursor": first["next_cursor"]})
        assert second.status_code == 200, second.text
        assert [row["id"] for row in second.json()["items"]] == saved_jobs[::-1][2:]
    finally:
        with Session.begin() as session:
            session.execute(delete(Job).where(Job.id == new_id))


def test_history_bounds_and_invalid_cursor():
    client = TestClient(app)
    for query in ("limit=0", "limit=51", "limit=no", "cursor=bad", "cursor="):
        assert client.get(f"/api/jobs?{query}").status_code == 422
    from base64 import urlsafe_b64encode

    for value in ("2026-11-05|bad", "2026-11-05T00:00:00|" + str(uuid4())):
        cursor = urlsafe_b64encode(value.encode()).decode()
        assert client.get("/api/jobs", params={"cursor": cursor}).status_code == 422
    cursor = urlsafe_b64encode(f"1970-01-01T00:00:00+00:00|{uuid4()}".encode()).decode()
    assert client.get("/api/jobs", params={"cursor": cursor}).json() == {
        "items": [],
        "next_cursor": None,
    }
