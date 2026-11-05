import asyncio
import threading

from app.core.config import settings
from app.workers import store
from app.workers.queue import app


@app.task(name="arsune.scan")
def scan(job_id):
    claim = store.claim(job_id)
    if not claim:
        return
    generation = claim["generation"]
    stop = threading.Event()
    lost = threading.Event()

    def renew():
        while not stop.wait(settings.lease_seconds / 3):
            try:
                store.heartbeat(job_id, generation)
            except Exception:  # noqa: BLE001 - stop writes after any failed lease renewal
                lost.set()
                return

    def checkpoint(value):
        if lost.is_set():
            raise store.LeaseLost("Worker claim lost")
        store.save_checkpoint(job_id, generation, value)

    def event(role, message):
        store.emit_event(job_id, generation, role, message)

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        from app.workers.workflow import run_scan

        output = settings.evidence_dir / job_id
        output.mkdir(parents=True, exist_ok=True)

        async def run():
            remaining = (claim["deadline"] - store.utcnow()).total_seconds()
            async with asyncio.timeout(max(0.01, remaining)):
                return await run_scan(
                    claim["url"],
                    output,
                    mode=claim["mode"],
                    inject_demo_false_positive=claim["inject_demo"],
                    checkpoint=claim["checkpoint"],
                    baseline_report=claim["baseline_report"],
                    baseline_job_id=claim["baseline_job_id"],
                    comparison_mapping=claim["comparison_mapping"],
                    save_checkpoint=checkpoint,
                    emit_event=event,
                )

        report = asyncio.run(run())
        store.finish(job_id, generation, report=report)
    except store.LeaseLost:
        return
    except Exception:
        # Raw transport errors can contain credential-bearing URLs or headers.
        try:
            store.finish(
                job_id,
                generation,
                error="The check stopped. Review the target and server configuration.",
            )
        except store.LeaseLost:
            pass
        raise
    finally:
        stop.set()
        thread.join(timeout=2)
