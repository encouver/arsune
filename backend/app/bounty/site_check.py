"""Can Arsune check this site? Agents spend time and model tokens only on a site that the
checker can read.

A site check is a job of kind `site_check`. The claim worker runs it with the checker's own
browser session and observation code: it loads the bounty URL at the desktop viewport and at
the mobile viewport of the overflow check. The site passes when both loads answer HTTP 2xx and
are complete by the checker's own rules: `layout_complete` (app.browser.observations.stops_layout)
and `app.browser.checker.load_complete`. An image that the site answers with an HTTP error does
not fail the check: it is a fault for hunters to find. An analytics beacon from another site's
script to another site does not fail it either.

- The owner's verify request queues a check after the proof. A pass opens the bounty.
- Hunters and the swarm runner ask for a check before agents start. A result stays fresh for
  FRESH_SECONDS, so many agents do not load the site again and again.
- A bounty pauses after PAUSE_AFTER decided claims in a row that Arsune's own load left
  unverified. The owner's verify request runs a new check, and a pass resumes the bounty.
"""

import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.bounty import settlement
from app.browser.checker import load_complete
from app.browser.observations import observe_page, stops_layout
from app.browser.session import inspection_browser
from app.core.config import settings
from app.core.db import Session
from app.core.models import Bounty, Job, PendingAction, utcnow
from app.core.scan import VIEWPORTS
from app.settlement.base import SettlementError
from app.settlement.near_tx import OutcomeUnknown
from app.workers import store

logger = logging.getLogger(__name__)

KIND = "site_check"
FRESH_SECONDS = 300
PAUSE_AFTER = 5
CHECK_SECONDS = 90
CHECKED_VIEWPORTS = ("desktop", "mobile")
MAX_LISTED = 5  # problems named in a reason
MAX_KEPT = 20  # problems kept in a result


def shorten(text, limit=200) -> str:
    text = " ".join(str(text or "").split()).rstrip(".")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def loaded(observation) -> bool:
    """The page answered, and the checker counts its layout and its load as complete."""
    return (
        observation.get("status") is not None
        and not observation.get("error")
        and observation.get("layout_complete", True)
        and load_complete(observation)
    )


def incomplete(issue) -> bool:
    """The checker's own rules: this one resource leaves the layout or the load incomplete."""
    return stops_layout(issue) or not load_complete({"resource_issues": [issue]})


def width(observation):
    return (observation.get("viewport") or {}).get("width")


def problems(observations) -> list[str]:
    """What did not load, one line each, without repeats across viewports."""
    lines = []
    for observation in observations:
        where = f"{shorten(observation.get('url'))} at {width(observation)} px"
        status = observation.get("status")
        if observation.get("error") or status is None:
            lines.append(f"{where}: {shorten(observation.get('error') or 'no response')}")
        elif not 200 <= status < 300:
            lines.append(f"{where}: HTTP {status}")
        for issue in observation.get("resource_issues", []):
            if incomplete(issue):
                lines.append(
                    f"{issue['resource_type']} {shorten(issue.get('url'))}: "
                    f"{shorten(issue.get('reason'), 160)}"
                )
    return list(dict.fromkeys(lines))


def listed(lines) -> str:
    text = "; ".join(lines[:MAX_LISTED])
    if len(lines) > MAX_LISTED:
        text += f"; and {len(lines) - MAX_LISTED} more"
    return text


def verdict(url, observations, failure=None) -> dict:
    """The result of one site check, from its fresh observations."""
    lines = [shorten(failure)] if failure else problems(observations)
    checkable = (
        not failure
        and len(observations) == len(CHECKED_VIEWPORTS)
        and all(loaded(o) and 200 <= o["status"] < 300 for o in observations)
    )
    widths = " and ".join(str(VIEWPORTS[name]["width"]) for name in CHECKED_VIEWPORTS)
    if checkable:
        reason = f"Arsune loaded {url} completely at {widths} pixels wide."
    else:
        reason = (
            f"Arsune cannot check this site: a fresh load of {url} did not complete. "
            f"{listed(lines) or 'The page did not load'}."
        )
    pages = [
        {
            "url": o.get("url"),
            "final_url": o.get("final_url"),
            "width": width(o),
            "status": o.get("status"),
            "loaded": loaded(o),
        }
        for o in observations
    ]
    return {"checkable": checkable, "reason": reason, "problems": lines[:MAX_KEPT], "pages": pages}


async def load(url, output_dir) -> list[dict]:
    state = {"selected": [url]}

    async def keep():
        # Only the finished result is saved; an interrupted check starts again.
        pass

    async with asyncio.timeout(CHECK_SECONDS):
        async with inspection_browser(url, settings.demo_url, state, keep) as (context, _):
            return [
                await observe_page(context, url, VIEWPORTS[name], output_dir, url)
                for name in CHECKED_VIEWPORTS
            ]


async def check_site(url, checkpoint, save_checkpoint, output_dir) -> dict:
    """The worker step. The result is saved before the bounty opens, so a retry reuses it."""
    if "result" in checkpoint:
        return checkpoint["result"]
    try:
        result = verdict(url, await load(url, output_dir))
    except TimeoutError:
        result = verdict(url, [], f"Arsune could not load {url} within {CHECK_SECONDS} seconds")
    except Exception:
        logger.exception("The site check of %s did not complete", url)
        result = verdict(url, [], "Arsune's browser could not complete the site check")
    save_checkpoint({"result": result})
    return result


# ---------------------------------------------------------------- the check of one bounty


def current(session, bounty) -> Job | None:
    return session.get(Job, bounty.site_check_job_id) if bounty.site_check_job_id else None


def running(job) -> bool:
    return job.status in ("queued", "running")


def fresh(job) -> bool:
    return job.status == "completed" and job.updated_at >= utcnow() - timedelta(
        seconds=FRESH_SECONDS
    )


def start(session, bounty, *, reuse: bool) -> Job:
    """Queue a check of the bounty URL. A running check is kept; with `reuse`, a fresh one too.

    The caller holds the bounty row lock, so parallel requests share one check.
    """
    job = current(session, bounty)
    if job and (running(job) or (reuse and fresh(job))):
        return job
    job = Job(
        id=str(uuid4()),
        kind=KIND,
        url=bounty.url,
        mode="scripted",
        deadline=utcnow() + timedelta(seconds=settings.job_deadline_seconds),
    )
    session.add(job)
    session.flush()
    session.add(PendingAction(id=f"{job.id}:scan", job_id=job.id))
    bounty.site_check_job_id = job.id
    return job


def view(job) -> dict | None:
    """The public state of a check: checking, passed or failed, with what did not load."""
    if job is None:
        return None
    report = job.report or {}
    if running(job):
        state, reason = "checking", f"Arsune is loading {job.url} in a fresh browser."
    elif job.status == "completed":
        state, reason = ("passed" if report.get("checkable") else "failed"), report.get("reason")
    else:
        state = "failed"
        reason = "Arsune cannot check this site: the site check stopped before a result."
    completed = job.status == "completed"
    return {
        "job_id": job.id,
        "state": state,
        "url": job.url,
        "reason": reason,
        "problems": report.get("problems", []),
        "pages": report.get("pages", []),
        "note": report.get("note"),
        "requested_at": job.created_at,
        "checked_at": None if state == "checking" else job.updated_at,
        "fresh_until": job.updated_at + timedelta(seconds=FRESH_SECONDS) if completed else None,
    }


def resume(bounty) -> None:
    bounty.status, bounty.paused_reason, bounty.unchecked_claims = "open", None, 0
    bounty.updated_at = utcnow()


def open_checked_bounty(job_id, generation, result) -> dict:
    """Open, or resume, the bounty that waits for this check, when the check passed.

    Only the owner's verify request attaches a check to a bounty that is not open: after the
    proof, or to resume a paused bounty. Return the job report.
    """
    with Session.begin() as session:
        store.require_claim(store.locked_job(session, job_id), generation)
        bounty = session.scalar(
            select(Bounty).where(Bounty.site_check_job_id == job_id).with_for_update()
        )
        if not bounty or not result.get("checkable"):
            return result
        if bounty.status == "paused":
            resume(bounty)
            return result
        if bounty.status != "pending_proof":
            return result
        if settlement.settlement_mode() != "escrow":
            bounty.status, bounty.updated_at = "open", utcnow()
            return result
        bounty_id = bounty.id
    return open_on_escrow(job_id, bounty_id, result)


def open_on_escrow(job_id, bounty_id, result) -> dict:
    """Create the bounty on the escrow before it takes funds. Arsune pays the storage NEAR."""
    with Session() as session:
        bounty = session.get(Bounty, bounty_id)
    try:
        tx_id = settlement.open_on_escrow(bounty)
    except OutcomeUnknown:
        note = "The site check passed, but the escrow result is unknown. Verify again later."
        return {**result, "note": note}
    except SettlementError as exc:
        note = f"The site check passed, but the escrow did not open the bounty: {exc}"
        return {**result, "note": note}
    # A sent transaction is a fact to record, so this step needs no lease.
    with Session.begin() as session:
        bounty = session.scalar(select(Bounty).where(Bounty.id == bounty_id).with_for_update())
        if bounty.status == "pending_proof" and bounty.site_check_job_id == job_id:
            bounty.status, bounty.updated_at = "open", utcnow()
            session.add(
                settlement.escrow_row(bounty, "escrow_open", 0, settings.escrow_contract, tx_id)
            )
    return result


# ---------------------------------------------------------------- the circuit breaker


def limited(result) -> bool:
    """Arsune's own load of the claimed pages was incomplete, or the check did not run to the end.

    A page that answers with an HTTP error, a link that is not on its page, or a hidden image
    is the site's answer, not a limit of Arsune.
    """
    evidence = result.get("evidence") or []
    return not evidence or not all(loaded(o) for o in evidence)


def count_claim(bounty, outcome, result, now) -> None:
    """Count the claims in a row that Arsune's limits left unverified; pause at PAUSE_AFTER.

    The caller holds the bounty row lock. A duplicate found without a browser check says
    nothing about the site, so it neither counts nor resets the count.
    """
    if outcome == "unverified" and limited(result):
        bounty.unchecked_claims += 1
        if bounty.unchecked_claims >= PAUSE_AFTER and bounty.status == "open":
            pause(bounty, result, now)
    elif outcome != "duplicate" or result.get("evidence"):
        bounty.unchecked_claims = 0


def pause(bounty, result, now) -> None:
    detail = listed(problems(result.get("evidence") or [])) or shorten(
        result.get("checker_reason"), 300
    )
    bounty.status, bounty.updated_at = "paused", now
    bounty.paused_reason = (
        f"Arsune cannot check this site: the last {bounty.unchecked_claims} claims were "
        "unverified because Arsune could not load their pages completely. "
        f"Last claim: {detail}."
    )
    # The check that opened the bounty is out of date. Only the owner's verify resumes it.
    bounty.site_check_job_id = None
