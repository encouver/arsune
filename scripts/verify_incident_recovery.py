"""Kill a real Celery worker after a committed specialist stage, then recover.

Run with the API, coordinator, PostgreSQL and Redis active and other workers
stopped. The first worker pauses after a real checkpoint commit to make the
kill point deterministic. No parser, specialist, checker or database is mocked.
"""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import httpx
from app.core.db import Session
from app.core.models import Attempt, Event, IncidentSource, Job, Record
from app.incidents import run_incident, validate_bundle
from app.workers.queue import app
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "backend/tests/fixtures/incidents/known"
BOOTSTRAP = """
import time
from app.workers import store
from app.workers.queue import app
original = store.save_checkpoint
def save(job_id, generation, checkpoint):
    original(job_id, generation, checkpoint)
    if 'error' in checkpoint.get('stages', {}) and not checkpoint.get('checker_completed'):
        time.sleep(60)
store.save_checkpoint = save
app.worker_main(['worker', '--concurrency=1', '--loglevel=INFO', '--hostname=incident-kill@%h'])
"""


def start_worker(log, pause=False):
    command = (
        [str(ROOT / ".venv/bin/python"), "-c", BOOTSTRAP]
        if pause
        else [
            str(ROOT / ".venv/bin/celery"),
            "-A",
            "app.workers.queue:app",
            "worker",
            "--concurrency=1",
            "--loglevel=INFO",
            "--hostname=incident-restart@%h",
        ]
    )
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env={**os.environ, "ARSUNE_LEASE_SECONDS": "4"},
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def stop_worker(worker):
    if worker.poll() is None:
        os.killpg(worker.pid, signal.SIGKILL)
        worker.wait(timeout=10)


def wait_for(callback, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = callback()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("Incident recovery exceeded its test deadline.")


def main():
    import asyncio

    payload = json.loads((FIXTURE / "request.json").read_text()) | {
        "events_text": (FIXTURE / "events.ndjson").read_text(),
        "deployments_text": (FIXTURE / "deployments.json").read_text(),
    }
    expected = asyncio.run(run_incident(validate_bundle(**payload)))
    log_path = Path("/tmp/arsune-incident-recovery-worker.log")
    with (
        log_path.open("w") as log,
        httpx.Client(base_url="http://127.0.0.1:8540", timeout=10) as client,
    ):
        worker = start_worker(log, pause=True)
        try:
            response = client.post("/api/incidents", json=payload)
            response.raise_for_status()
            job_id = response.json()["id"]

            def saved_stage():
                with Session() as session:
                    job = session.get(Job, job_id)
                    assert job.status != "failed", job.error
                    return job.checkpoint if "error" in job.checkpoint.get("stages", {}) else None

            saved = wait_for(saved_stage)
            stop_worker(worker)
            with Session() as session:
                job = session.get(Job, job_id)
                generation = job.generation
                hashes = {
                    s.filename: s.sha256
                    for s in session.scalars(
                        select(IncidentSource).where(IncidentSource.job_id == job_id)
                    )
                }
            worker = start_worker(log)

            def complete():
                result = client.get(f"/api/jobs/{job_id}").json()
                assert result["status"] != "failed", result["error"]
                return result if result["status"] == "completed" else None

            result = wait_for(complete)
            assert result["generation"] == generation + 1
            assert result["report"] == expected
            assert result["report"]["coverage"]["source_hashes"] == hashes
            with Session() as session:
                job = session.get(Job, job_id)
                for role, candidates in saved["stages"].items():
                    assert job.checkpoint["stages"][role] == candidates
                sources = session.scalars(
                    select(IncidentSource).where(IncidentSource.job_id == job_id)
                ).all()
                assert {s.filename: s.sha256 for s in sources} == hashes
                assert {s.filename: s.content for s in sources} == {
                    "events.ndjson": payload["events_text"],
                    "deployments.json": payload["deployments_text"],
                }
                attempts = session.scalars(select(Attempt).where(Attempt.job_id == job_id)).all()
                assert sorted(a.status for a in attempts) == ["completed", "expired"]
                events = session.scalars(select(Event).where(Event.job_id == job_id)).all()
                assert len(events) == 4 and len({e.id for e in events}) == 4
                records = session.scalars(select(Record).where(Record.job_id == job_id)).all()
                assert len(records) == len({r.id for r in records})
                assert len([r for r in records if r.kind == "finding"]) == len(expected["findings"])
            for finding in expected["findings"]:
                for reference in finding["evidence"]:
                    evidence = client.get(
                        f"/api/incidents/{job_id}/evidence/{reference['filename']}",
                        params={"record_id": reference["record_id"]},
                    )
                    assert (
                        evidence.status_code == 200
                        and evidence.json()["sha256"] == reference["sha256"]
                    )
            repeated = app.send_task("arsune.scan", args=[job_id])
            wait_for(lambda: f"Task arsune.scan[{repeated.id}] succeeded" in log_path.read_text())
            assert client.get(f"/api/jobs/{job_id}").json() == result
            print(
                f"PASS incident kill/restart: {job_id}; generations {generation}->{result['generation']}; two original hashes retained; stable report; four unique events; repeat delivery unchanged"
            )
        finally:
            stop_worker(worker)


if __name__ == "__main__":
    main()
