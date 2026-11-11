"""Can Arsune check this site? The check before a bounty opens (with real browser loads), the
site check that agents ask for before they start, and the pause after claims that Arsune's
own limits leave unverified."""

from datetime import timedelta

import httpx
from app.bounty import site_check
from app.bounty.worker import STOPPED
from app.browser.observations import stops_layout
from app.core.db import Session
from app.core.models import Bounty, Job
from app.workers.tasks import scan

# client and site are pytest fixtures; importing them shares the demo site setup.
# ruff: noqa: F811
from test_bounties import (  # noqa: F401
    BOND,
    CHECKABLE,
    CONFIRMED,
    FUND,
    REWARD,
    claim_payload,
    claim_view,
    client,
    fund,
    ledger,
    open_bounty,
    overflow,
    run_site_check,
    site,
    submit,
    verify_and_check,
)

SCRIPT = "https://cdn.example/app.js"
BLOCKED = {
    "url": SCRIPT,
    "resource_type": "Script",
    "reason": "Private and local network destinations are not permitted.",
}
MISSING_IMAGE = {
    "url": "https://shop.example/img/gone.png",
    "resource_type": "Image",
    "reason": "Resource returned HTTP 404.",
    "http_status": 404,
}
# An analytics beacon that another site's script sends to another site.
BEACON = {
    "url": "https://analytics.example/collect",
    "resource_type": "Policy",
    "reason": "Browser policy blocked connect-src.",
    "directive": "connect-src",
    "source": "https://analytics.example/tag.js",
    "third_party_data": True,
}
UNCHECKABLE = {
    "checkable": False,
    "reason": f"Arsune cannot check this site: a fresh load did not complete. Script {SCRIPT}.",
    "problems": [f"Script {SCRIPT}: Private and local network destinations are not permitted"],
}


def observation(url, issues=(), status=200, width=390):
    return {
        "url": url,
        "final_url": url,
        "viewport": {"width": width, "height": 844},
        "status": status,
        "resource_issues": list(issues),
        "layout_complete": not any(stops_layout(issue) for issue in issues),
    }


def unverified(url, issues, reason="Required page resources were unavailable."):
    return {
        "status": "unverified",
        "checker_reason": reason,
        "evidence": [observation(url, issues)],
    }


def create(client, site, path="/shop/"):
    response = client.post(
        "/api/bounties",
        json={
            "url": site + path,
            "owner_account": "owner.testnet",
            "reward_amount": REWARD,
            "bond_amount": BOND,
            "budget": FUND,
        },
    )
    assert response.status_code == 201, response.text
    bounty = response.json()
    httpx.post(site + "/bounty-proof-control", json={"token": bounty["proof_token"]})
    return bounty


def bounty_view(client, bounty):
    return client.get(f"/api/bounties/{bounty['id']}").json()


def page_claim(site, hunter, page=None):
    """An overflow claim on a page of its own, so that no proven claim makes it a duplicate."""
    url = site + (page or f"/faulty/{hunter}")
    return claim_payload(hunter, "horizontal_overflow", [url], {"url": url})


def decide(client, site, bounty, hunter, result, page=None):
    """A claim of a new hunter, decided with a seeded check result that keeps its evidence."""
    claim = submit(client, bounty, page_claim(site, hunter, page))
    with Session.begin() as session:
        session.get(Job, claim["job_id"]).checkpoint = {"result": result}
    scan.run(claim["job_id"])
    return claim_view(client, bounty, claim)


# ---------------------------------------------------------------- the result of one check


def test_a_load_is_complete_with_images_the_site_answers_with_an_error_and_beacons():
    url = "https://shop.example/"
    pages = [observation(url, [MISSING_IMAGE], width=1280), observation(url, [BEACON])]
    result = site_check.verdict(url, pages)
    assert result["checkable"] and result["problems"] == []
    assert result["reason"] == f"Arsune loaded {url} completely at 1280 and 390 pixels wide."
    readiness = {"url": url, "resource_type": "Readiness", "reason": "Deadline exceeded."}
    pages = [observation(url, [BLOCKED], width=1280), observation(url, [BLOCKED, readiness])]
    result = site_check.verdict(url, pages)
    assert not result["checkable"]
    # One line for each resource, also when both viewports miss it.
    assert result["problems"] == [
        f"Script {SCRIPT}: Private and local network destinations are not permitted",
        f"Readiness {url}: Deadline exceeded",
    ]
    assert result["reason"].startswith("Arsune cannot check this site: a fresh load of")
    assert f"Script {SCRIPT}" in result["reason"]


def test_an_error_answer_or_no_answer_is_not_checkable_and_long_lists_are_capped():
    url = "https://shop.example/"
    for page, line in [
        (observation(url, status=503), f"{url} at 390 px: HTTP 503"),
        ({**observation(url, status=None), "error": "The browser failed."}, "The browser failed"),
    ]:
        result = site_check.verdict(url, [observation(url, width=1280), page])
        assert not result["checkable"] and line in result["problems"][0]
    issues = [{**BLOCKED, "url": f"{SCRIPT}?{n}"} for n in range(8)]
    result = site_check.verdict(url, [observation(url, issues, width=1280), observation(url)])
    assert len(result["problems"]) == 8 and result["reason"].endswith("; and 3 more.")
    timeout = site_check.verdict(url, [], f"Arsune could not load {url} within 90 seconds")
    assert not timeout["checkable"] and "within 90 seconds" in timeout["reason"]


def test_only_an_incomplete_load_by_arsune_counts_as_a_limit():
    url = "https://shop.example/"
    assert site_check.limited(unverified(url, [BLOCKED]))
    assert site_check.limited(STOPPED)  # the check job stopped: nothing was read
    assert site_check.limited({"status": "unverified", "evidence": []})
    # The site answered: a hidden broken image, an error page, a link that is not there.
    assert not site_check.limited(unverified(url, [MISSING_IMAGE], "The image is not shown."))
    assert not site_check.limited(
        {"status": "unverified", "evidence": [observation(url, status=404)]}
    )


# ---------------------------------------------------------------- the check before opening


def test_the_demo_shop_passes_a_real_site_check_and_the_bounty_opens(client, site):
    bounty = create(client, site, "/shop/")
    response = client.post(f"/api/bounties/{bounty['id']}/verify")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending_proof"
    assert response.json()["site_check"]["state"] == "checking"
    # Funds and claims wait for the check.
    assert fund(client, bounty).status_code == 409
    run_site_check(bounty["id"], result=None)  # Arsune's real browser loads the shop
    view = bounty_view(client, bounty)
    check = view["site_check"]
    assert view["status"] == "open", check
    assert check["state"] == "passed" and check["problems"] == []
    # The home page shows an image that answers HTTP 404: a fault to find, not a limit.
    assert [(p["width"], p["status"], p["loaded"]) for p in check["pages"]] == [
        (1280, 200, True),
        (390, 200, True),
    ]
    assert (
        check["reason"] == f"Arsune loaded {bounty['url']} completely at 1280 and 390 pixels wide."
    )
    assert check["fresh_until"] and check["checked_at"]
    assert fund(client, bounty).status_code == 200


def test_a_site_that_arsune_cannot_load_completely_does_not_open(client, site):
    bounty = create(client, site, "/faulty/resource-policy")
    assert client.post(f"/api/bounties/{bounty['id']}/verify").status_code == 202
    first = run_site_check(bounty["id"], result=None)  # the page loads an image on 127.0.0.1:8542
    view = bounty_view(client, bounty)
    check = view["site_check"]
    assert view["status"] == "pending_proof"
    assert check["state"] == "failed"
    assert check["reason"].startswith("Arsune cannot check this site:")
    assert "Image http://127.0.0.1:8542/internal" in check["reason"]
    assert check["problems"][0].startswith("Image http://127.0.0.1:8542/internal: ")
    assert fund(client, bounty).status_code == 409
    response = client.post(
        f"/api/bounties/{bounty['id']}/claims", json=overflow(site, "hunter-a.testnet")
    )
    assert response.status_code == 409 and response.json()["detail"] == "The bounty is not open."
    # The owner can try again later. A new check runs: an old result is not reused.
    response = client.post(f"/api/bounties/{bounty['id']}/verify")
    assert response.status_code == 202
    assert response.json()["site_check"]["job_id"] != first
    # While it runs, a repeated request shares it.
    again = client.post(f"/api/bounties/{bounty['id']}/verify").json()["site_check"]["job_id"]
    assert again == response.json()["site_check"]["job_id"]
    run_site_check(bounty["id"], CHECKABLE)
    assert bounty_view(client, bounty)["status"] == "open"


def test_a_failed_proof_starts_no_site_check(client, site):
    bounty = create(client, site)
    httpx.post(site + "/bounty-proof-control", json={"token": "another-token-of-16"})
    response = client.post(f"/api/bounties/{bounty['id']}/verify")
    assert response.status_code == 409 and "token" in response.json()["detail"]
    view = bounty_view(client, bounty)
    assert (view["status"], view["site_check"]) == ("pending_proof", None)


# ---------------------------------------------------------------- before agents start


def test_agents_share_one_fresh_site_check(client, site):
    bounty = open_bounty(client, site)
    opening = bounty["site_check"]["job_id"]
    path = f"/api/bounties/{bounty['id']}/site-check"
    # The check that opened the bounty is still fresh, so nothing loads the site again.
    response = client.post(path)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert (detail["bounty_status"], detail["site_check"]["job_id"]) == ("open", opening)
    assert detail["site_check"]["state"] == "passed"
    assert client.get(path).json() == detail
    with Session.begin() as session:
        job = session.get(Job, opening)
        job.updated_at -= timedelta(seconds=site_check.FRESH_SECONDS + 1)
    response = client.post(path)
    assert response.status_code == 202
    fresh = response.json()["site_check"]["job_id"]
    assert fresh != opening
    assert client.post(path).json()["site_check"]["job_id"] == fresh
    run_site_check(bounty["id"], UNCHECKABLE)
    # A failed result is fresh too; the bounty stays open, and agents do not start.
    response = client.post(path)
    assert response.status_code == 200
    assert response.json()["site_check"]["state"] == "failed"
    assert response.json()["site_check"]["job_id"] == fresh
    assert bounty_view(client, bounty)["status"] == "open"


def test_a_site_check_needs_an_open_bounty(client, site):
    bounty = create(client, site)
    response = client.post(f"/api/bounties/{bounty['id']}/site-check")
    assert response.status_code == 409 and response.json()["detail"] == "The bounty is not open."
    assert client.post("/api/bounties/not-a-bounty/site-check").status_code == 404


# ---------------------------------------------------------------- the pause


def test_claims_unverified_by_arsunes_limits_pause_the_bounty(client, site):
    bounty = open_bounty(client, site)
    url = site + "/faulty/"
    limited, ordinary = unverified(url, [BLOCKED]), unverified(url, [MISSING_IMAGE])
    n = site_check.PAUSE_AFTER

    def series(start, result, count):
        for i in range(start, start + count):
            assert decide(client, site, bounty, f"hunter-{i}.testnet", result)["outcome"]

    # An ordinary unverified claim and a proven claim each end the series.
    series(0, limited, n - 1)
    series(100, ordinary, 1)
    series(200, limited, n - 1)
    proven = decide(client, site, bounty, "hunter-300.testnet", CONFIRMED, "/faulty/")
    assert proven["outcome"] == "proven"
    # A duplicate found without a browser check neither counts nor ends the series.
    series(400, limited, n - 1)
    duplicate = submit(client, bounty, page_claim(site, "hunter-500.testnet", "/faulty/"))
    assert duplicate["outcome"] == "duplicate"
    queued = submit(client, bounty, page_claim(site, "hunter-600.testnet"))
    assert bounty_view(client, bounty)["status"] == "open"
    series(700, limited, 1)

    view = bounty_view(client, bounty)
    assert view["status"] == "paused" and view["site_check"] is None
    reason = view["paused_reason"]
    assert reason.startswith(f"Arsune cannot check this site: the last {n} claims were unverified")
    assert f"Script {SCRIPT}: Private and local network destinations" in reason
    # No new claim: the 409 gives the reason. A site check request gets it too.
    response = client.post(
        f"/api/bounties/{bounty['id']}/claims", json=overflow(site, "hunter-800.testnet")
    )
    assert response.status_code == 409
    assert response.json()["detail"] == f"The bounty is paused: {reason}"
    response = client.post(f"/api/bounties/{bounty['id']}/site-check")
    assert response.status_code == 409 and response.json()["detail"].startswith(
        "The bounty is paused: Arsune cannot check this site"
    )
    # A claim queued before the pause is still checked and settled as usual.
    with Session.begin() as session:
        session.get(Job, queued["job_id"]).checkpoint = {"result": limited}
    scan.run(queued["job_id"])
    settled = claim_view(client, bounty, queued)
    assert settled["status"] == "unverified" and settled["settled_at"]
    assert {r.kind for r in ledger(bounty["id"], queued["id"])} == {"bond_in", "bond_refund"}


def test_the_owner_resumes_a_paused_bounty_after_a_passing_site_check(client, site):
    bounty = open_bounty(client, site)
    for i in range(site_check.PAUSE_AFTER):
        decide(client, site, bounty, f"hunter-{i}.testnet", STOPPED)
    assert bounty_view(client, bounty)["status"] == "paused"
    # A paused bounty was proven before, so the proof file is not read again.
    httpx.post(site + "/bounty-proof-control", json={"token": "another-token-of-16"})
    view = verify_and_check(client, bounty, UNCHECKABLE)
    assert view["status"] == "paused" and view["site_check"]["state"] == "failed"
    view = verify_and_check(client, bounty)
    assert (view["status"], view["paused_reason"]) == ("open", None)
    with Session() as session:
        assert session.get(Bounty, bounty["id"]).unchecked_claims == 0
    claim = submit(client, bounty, overflow(site, "hunter-new.testnet"))
    assert claim["status"] == "received"
