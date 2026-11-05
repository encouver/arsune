"""Run with API, demo, coordinator, PostgreSQL and Redis already running.

This starts a dedicated worker, kills it after a saved browser observation,
then restarts it and checks the report and duplicate delivery.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from app.core.db import Session
from app.core.models import Attempt, Job
from app.workers.queue import app
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]


def start_worker(log):
    environment = {**os.environ, "ARSUNE_LEASE_SECONDS": "4"}
    return subprocess.Popen(
        [
            str(ROOT / ".venv/bin/celery"),
            "-A",
            "app.workers.queue:app",
            "worker",
            "--concurrency=1",
            "--loglevel=INFO",
            "--hostname=arsune-recovery@%h",
        ],
        cwd=ROOT,
        env=environment,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def stop_worker(worker):
    if worker.poll() is None:
        os.killpg(worker.pid, signal.SIGKILL)
        worker.wait(timeout=10)


def wait_for(predicate, seconds=90):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("The operation did not complete within its test deadline")


def main():
    with Path("/tmp/arsune-recovery-worker.log").open("w") as log:
        worker = start_worker(log)
        client = httpx.Client(base_url="http://127.0.0.1:8540", timeout=10)
        try:
            response = client.post(
                "/api/jobs",
                json={
                    "url": "http://127.0.0.1:8541/faulty/",
                    "mode": "scripted",
                    "demo_fault_injection": True,
                },
            )
            response.raise_for_status()
            job_id = response.json()["id"]
            baseline = None
            if "--comparison" in sys.argv:

                def baseline_complete():
                    value = client.get(f"/api/jobs/{job_id}").json()
                    if value["status"] == "failed":
                        raise AssertionError(value["error"])
                    return value if value["status"] == "completed" else None

                baseline = wait_for(baseline_complete)
                response = client.post(f"/api/jobs/{job_id}/rerun", json={})
                response.raise_for_status()
                job_id = response.json()["id"]

            def saved_observations():
                with Session() as session:
                    job = session.get(Job, job_id)
                    return (
                        bool(job.checkpoint.get("baseline_checks"))
                        if baseline
                        else len(job.checkpoint.get("observations", [])) >= 2
                    ) and job.status == "running"

            wait_for(saved_observations)
            stop_worker(worker)
            with Session() as session:
                before = session.get(Job, job_id)
                assert before.checkpoint["observations"]
                saved_baseline_checks = before.checkpoint.get("baseline_checks", {})
                old_generation = before.generation
                saved_captures = {
                    o["screenshot"]
                    for o in before.checkpoint["observations"]
                    if o.get("screenshot")
                }
            worker = start_worker(log)

            def complete():
                value = client.get(f"/api/jobs/{job_id}").json()
                if value["status"] == "failed":
                    raise AssertionError(value["error"])
                return value if value["status"] == "completed" else None

            result = wait_for(complete)
            assert result["generation"] > old_generation
            if baseline:
                comparison = result["report"]["comparison"]
                assert comparison["summary"]["remaining"] == len(baseline["report"]["confirmed"])
                assert comparison["summary"]["resolved"] == 0
                assert client.get(f"/api/jobs/{baseline['id']}").json() == baseline
                with Session() as session:
                    saved = session.get(Job, job_id).checkpoint["baseline_checks"]
                    assert all(saved[key] == value for key, value in saved_baseline_checks.items())
            report = result["report"]
            assert saved_captures <= {o.get("screenshot") for o in report["observations"]}
            assert len(report["inspected_pages"]) == 5
            assert report["confirmed"]
            assert any(f["injected"] for f in report["rejected"])
            ids = [finding["id"] for finding in report["findings"]]
            assert len(ids) == len(set(ids))
            assert {"navigation", "mobile", "consistency"} <= {e["role"] for e in result["events"]}
            for finding in report["findings"]:
                assert finding["evidence"] or finding["reproduction_steps"]
                for evidence in finding["evidence"]:
                    if evidence.get("screenshot"):
                        assert (
                            client.get(
                                f"/api/jobs/{job_id}/evidence/{evidence['screenshot']}"
                            ).status_code
                            == 200
                        )
            repeat = app.send_task("arsune.scan", args=[job_id])
            wait_for(
                lambda: (
                    f"Task arsune.scan[{repeat.id}] succeeded"
                    in Path("/tmp/arsune-recovery-worker.log").read_text()
                )
            )
            repeated = client.get(f"/api/jobs/{job_id}").json()
            assert repeated["report"] == report
            assert repeated["generation"] == result["generation"]
            with Session() as session:
                attempts = session.scalars(select(Attempt).where(Attempt.job_id == job_id)).all()
                assert len(attempts) == 2
            print(f"PASS worker kill/restart and repeat delivery: {job_id}")
            print(
                f"{len(report['confirmed'])} confirmed; {len(report['rejected'])} rejected; 5 pages"
            )
        finally:
            stop_worker(worker)
            client.close()


if __name__ == "__main__":
    main()
