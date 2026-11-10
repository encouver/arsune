"""The public evidence bundle of a decided claim, and its hash.

The bundle holds what anyone needs to check a ruling again: the claim, the pages, the
viewports, the fresh observations, and the checker's result. It names screenshots by their
sha256 and holds no host path and no secret. Arsune stores the canonical JSON text, so
`GET /api/claims/{id}/evidence` serves exactly the bytes whose sha256 is `evidence_hash`.
"""

import hashlib
from pathlib import Path

from app.settlement.escrow import canonical_json, evidence_hash

FORMAT = "arsune-evidence/1"
# Where and when the page was read, and every field that app.browser.checker reads.
OBSERVATION_KEYS = (
    "url",
    "final_url",
    "viewport",
    "captured_at",
    "status",
    "error",
    "layout_complete",
    "scroll_width",
    "client_width",
    "links",
    "facts",
    "images",
    "images_truncated",
    "page_errors",
    "page_errors_truncated",
    "resource_issues",
)


def file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def screenshot(directory: Path, name: str | None) -> dict | None:
    # Only the file name: it is served from the job's evidence route, never as a host path.
    if not name:
        return None
    return {"name": name, "sha256": file_sha256(directory / name)}


def observation(item: dict, directory: Path) -> dict:
    found = {key: item[key] for key in OBSERVATION_KEYS if key in item}
    found["screenshot"] = screenshot(directory, item.get("screenshot"))
    if "facts" in found:
        found["facts"] = [
            {**fact, "screenshot": screenshot(directory, fact.get("screenshot"))}
            for fact in found["facts"]
        ]
    return found


def ruling(claim) -> str:
    """The outcome as the escrow names it: a proven claim that holds no fault is unfunded."""
    if claim.outcome == "proven" and claim.final_fingerprint is None:
        return "unfunded"
    return claim.outcome


def scout_terms(scout: dict | None) -> dict | None:
    # The tip ID is the swarm's own record; the account and share are what the bond names.
    return {"account": scout["account"], "share_bps": scout["share_bps"]} if scout else None


def bundle(bounty, claim, result: dict, directory: Path, decided_at) -> dict:
    return {
        "format": FORMAT,
        "site": {"url": bounty.url, "origin": bounty.origin},
        "claim": {
            "id": claim.id,
            "bounty_id": claim.bounty_id,
            "job_id": claim.job_id,
            "hunter_account": claim.hunter_account,
            "scout": scout_terms(claim.scout),
            "claim_type": claim.claim_type,
            "urls": claim.urls,
            "details": claim.details,
            "explanation": claim.explanation,
            "reproduction_steps": claim.reproduction_steps,
            "fingerprint": claim.fingerprint,
            "submitted_at": claim.created_at.isoformat(),
        },
        "check": {
            "status": result.get("status"),
            "reason": claim.checker_reason,
            "observations": [observation(o, directory) for o in result.get("evidence", [])],
            "coverage": result.get("coverage"),
        },
        "ruling": {
            "outcome": ruling(claim),
            "final_fingerprint": claim.final_fingerprint,
            "decided_at": decided_at.isoformat(),
        },
    }


def publish(bounty, claim, result: dict, directory: Path, decided_at) -> None:
    """Store the bundle once, as the exact bytes that its hash names."""
    data = canonical_json(bundle(bounty, claim, result, directory, decided_at))
    claim.evidence_bundle = data.decode()
    claim.evidence_hash = evidence_hash(data)
