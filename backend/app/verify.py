"""Check an Arsune ruling from the public record. Nothing here trusts Arsune's verdict.

    uv run python -m app.verify <api_url> <claim_id> [--no-browser] [--local-site]
                                [--escrow-contract ACCOUNT] [--rpc-url URL]

1. Download the claim and its evidence bundle. The sha256 of the bundle bytes must be the
   claim's published evidence_hash and, for an escrow bounty, the hash that the escrow stored
   with the ruling (a read-only `get_claim` view).
2. Each screenshot named in the bundle must have the sha256 that the bundle gives.
3. The checker rules, applied again to the published observations, must give the same status.
4. The same check, run again in a fresh local browser, must give the published ruling.

Exit status: 0 when everything matches, 1 on a mismatch, 2 when the record cannot be read.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import httpx

from app.bounty.worker import browser_check
from app.browser.checker import check_candidate
from app.core.config import settings
from app.settlement.base import SettlementError
from app.settlement.escrow import EscrowClient, canonical_json
from app.settlement.near_tx import NearRpc

# The fresh checker status that reproduces each published outcome.
EXPECTED = {
    "proven": "confirmed",
    "unfunded": "confirmed",
    "duplicate": "confirmed",
    "rejected": "rejected",
    "unverified": "unverified",
}


class Unreadable(Exception):
    pass


class Report:
    def __init__(self):
        self.failed = False

    def line(self, label, text, ok=None):
        mark = "" if ok is None else ("  MATCH" if ok else "  MISMATCH")
        print(f"{label:<28}{text}{mark}")
        if ok is False:
            self.failed = True


def fetch(client, path):
    response = client.get(path)
    if response.status_code != 200:
        raise Unreadable(f"GET {path} returned HTTP {response.status_code}: {response.text[:200]}")
    return response


def claim_item(bundle):
    claim = bundle["claim"]
    keys = ("id", "claim_type", "urls", "details", "explanation", "reproduction_steps")
    return {key: claim[key] for key in keys} | {"role": "hunter"}


async def replay(bundle):
    """The checker rules on the published observations, with no browser."""
    observations = bundle["check"]["observations"]

    async def observe(target, viewport):
        found = (o for o in observations if o["url"] == target and o["viewport"] == viewport)
        return next(found, {"url": target, "viewport": viewport, "status": None})

    return (await check_candidate(claim_item(bundle), observe))["status"]


def check_screenshots(client, bundle, report):
    names = [o["screenshot"] for o in bundle["check"]["observations"] if o.get("screenshot")]
    names += [
        f["screenshot"]
        for o in bundle["check"]["observations"]
        for f in o.get("facts", [])
        if f.get("screenshot")
    ]
    if not names:
        report.line("Screenshots:", "none in the bundle")
        return
    matched, missing = 0, 0
    for shot in names:
        response = client.get(f"/api/jobs/{bundle['claim']['job_id']}/evidence/{shot['name']}")
        if response.status_code != 200:
            missing += 1
        elif hashlib.sha256(response.content).hexdigest() == shot["sha256"]:
            matched += 1
    wrong = len(names) - matched - missing
    text = f"{matched} of {len(names)} have the sha256 in the bundle"
    if missing:
        text += f" ({missing} not served)"
    # A file that is not served proves nothing either way; a different file is a mismatch.
    report.line("Screenshots:", text, None if missing and not wrong else not wrong)


def check_escrow(claim, bundle, bundle_hash, contract, rpc_url, report):
    modes = {row["settlement_mode"] for row in claim["ledger"]}
    if "escrow" not in modes:
        mode = ", ".join(sorted(modes)) or "none"
        report.line("Escrow record:", f"none (settlement {mode}); only Arsune publishes the hash")
        return
    if not contract:
        report.line("Escrow record:", "unknown contract; pass --escrow-contract", False)
        return
    try:
        record = EscrowClient(contract, rpc=NearRpc(rpc_url)).get_claim(claim["id"])
    except SettlementError as exc:
        report.line("Escrow record:", f"{contract} cannot be read: {exc}", False)
        return
    if not record or not record.get("outcome"):
        report.line("Escrow record:", f"{contract} has no ruling for this claim", False)
        return
    outcome, stored = record["outcome"], record.get("evidence_hash")
    report.line("Escrow record:", f"{contract}: {outcome}, evidence_hash {stored}")
    report.line("Escrow hash = bundle hash:", stored or "-", stored == bundle_hash)
    ruling = bundle["ruling"]["outcome"]
    report.line("Escrow outcome = ruling:", f"{outcome} = {ruling}", outcome == ruling)


async def fresh_check(bundle):
    with tempfile.TemporaryDirectory(prefix="arsune-verify-") as directory:
        result = await browser_check(
            "verify", claim_item(bundle), bundle["site"]["url"], Path(directory)
        )
    return result["status"], result["checker_reason"]


def check_record(client, claim_id, contract, rpc_url, report):
    """Steps 1 and 2. Return the bundle."""
    claim = fetch(client, f"/api/claims/{claim_id}").json()
    published = claim.get("evidence_hash")
    if not published:
        raise Unreadable(f"claim {claim_id} has no evidence hash yet (status {claim['status']})")
    data = fetch(client, f"/api/claims/{claim_id}/evidence").content
    bundle = json.loads(data)
    if contract is None:
        contract = fetch(client, "/api/settlement").json().get("escrow_contract")
    report.line("Claim:", f"{claim_id} ({claim['claim_type']}, hunter {claim['hunter_account']})")
    report.line("Site:", bundle["site"]["url"])
    ruling = bundle["ruling"]["outcome"]
    report.line("Published ruling:", f"{ruling} (claim status {claim['status']})")
    computed = hashlib.sha256(data).hexdigest()
    report.line("Published evidence_hash:", published)
    report.line("sha256 of the bundle:", computed, computed == published)
    canonical = canonical_json(bundle) == data
    report.line("Bundle is canonical JSON:", "yes" if canonical else "no", canonical)
    names = bundle["claim"]["id"] == claim_id and same_ruling(ruling, claim)
    report.line("Bundle names this ruling:", f"{bundle['claim']['id']}, {ruling}", names)
    check_screenshots(client, bundle, report)
    check_escrow(claim, bundle, computed, contract, rpc_url, report)
    return bundle


def same_ruling(bundle_ruling, claim):
    # The claim API calls an unfunded claim proven, with the status unfunded.
    return bundle_ruling in (claim["outcome"], "unfunded" if claim["outcome"] == "proven" else None)


def check_ruling(bundle, browser, report):
    """Steps 3 and 4."""
    check, ruling = bundle["check"], bundle["ruling"]["outcome"]
    if check["observations"]:
        replayed = asyncio.run(replay(bundle))
        text = f"{replayed} (published {check['status']})"
        report.line("Rules on the observations:", text, replayed == check["status"])
    else:
        report.line("Rules on the observations:", f"no observations: {check['reason']}")
    if not browser:
        report.line("Fresh browser check:", "skipped (--no-browser)")
        return
    status, reason = asyncio.run(fresh_check(bundle))
    expected = EXPECTED.get(ruling)
    text = f"{status} (a {ruling} ruling needs {expected})"
    report.line("Fresh browser check:", text, status == expected)
    report.line("Fresh reason:", reason)


def verify(api_url, claim_id, *, browser=True, local_site=False, contract=None, rpc_url=None):
    report = Report()
    with httpx.Client(base_url=api_url.rstrip("/"), timeout=30) as client:
        bundle = check_record(client, claim_id, contract, rpc_url or settings.near_rpc_url, report)
    if local_site:
        # The URL policy blocks private addresses except the one controlled demo origin.
        settings.demo_url = bundle["site"]["url"]
    check_ruling(bundle, browser, report)
    print()
    if report.failed:
        print("Verdict: MISMATCH. The record or the ruling does not hold (see above).")
        print("Note: a site that changed after the ruling also gives another fresh result.")
        return 1
    print("Verdict: OK. The hash matches" + ("; the ruling is reproduced." if browser else "."))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.verify", description=__doc__.splitlines()[0]
    )
    parser.add_argument("api_url", help="Arsune API base URL, for example http://127.0.0.1:8540")
    parser.add_argument("claim_id")
    parser.add_argument("--no-browser", action="store_true", help="skip the fresh browser check")
    parser.add_argument(
        "--local-site", action="store_true", help="allow a bounty site on a private address"
    )
    parser.add_argument("--escrow-contract", help="default: escrow_contract from the API")
    parser.add_argument("--rpc-url", help="NEAR RPC for read-only views")
    args = parser.parse_args(argv)
    try:
        return verify(
            args.api_url,
            args.claim_id,
            browser=not args.no_browser,
            local_site=args.local_site,
            contract=args.escrow_contract,
            rpc_url=args.rpc_url,
        )
    except (Unreadable, httpx.HTTPError, ValueError, KeyError) as exc:
        print(f"Cannot read the public record: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
