"""Bounty claims for broken images and page errors on the demo shop, with real browser checks."""

# client and site are pytest fixtures; importing them shares the demo site setup.
from test_bounties import (  # noqa: F401
    claim_payload,
    client,
    open_bounty,
    paid_rewards,
    run_claim,
    site,
    submit,
)


def image_claim(base, hunter, name, page="/shop/gallery"):
    details = {"page": base + page, "image": f"{base}/shop/img/{name}"}
    return claim_payload(hunter, "broken_image", [base + page], details)


def error_claim(base, hunter, message, page="/shop/gallery"):
    details = {"page": base + page, "message": message}
    return claim_payload(hunter, "page_error", [base + page], details)


def test_broken_image_claims_are_paid_once_per_final_file(client, site):  # noqa: F811
    bounty = open_bounty(client, site)
    _, alias = run_claim(
        client, bounty, image_claim(site, "hunter-a.testnet", "gallery/mobile-app.png")
    )
    assert alias["status"] == "paid", alias
    assert "HTTP 404" in alias["checker_reason"]
    final = image_claim(site, "hunter-b.testnet", "gallery/mobile-app-v2.png")
    _, duplicate = run_claim(client, bounty, final)
    assert duplicate["status"] == "duplicate", duplicate
    _, loads = run_claim(
        client, bounty, image_claim(site, "hunter-c.testnet", "old-logo.png", "/shop/")
    )
    assert loads["status"] == "rejected", loads
    _, hidden = run_claim(
        client, bounty, image_claim(site, "hunter-d.testnet", "gallery/old-banner.png")
    )
    assert hidden["status"] == "unverified", hidden
    assert len(paid_rewards(bounty["id"])) == 1


def test_page_error_claims_quote_one_error_once(client, site):  # noqa: F811
    bounty = open_bounty(client, site)
    _, first = run_claim(client, bounty, error_claim(site, "hunter-a.testnet", "Gallery lightbox"))
    assert first["status"] == "paid", first
    assert first["details"]["message"] == "gallery lightbox"
    other_part = error_claim(site, "hunter-b.testnet", "could NOT find its images")
    _, duplicate = run_claim(client, bounty, other_part)
    assert duplicate["status"] == "duplicate", duplicate
    full = error_claim(
        site, "hunter-c.testnet", "Error: Gallery lightbox could not find its images"
    )
    assert submit(client, bounty, full)["status"] == "duplicate"  # known at arrival
    console = error_claim(
        site, "hunter-d.testnet", "Newsletter signup is not configured", "/shop/c/analytics"
    )
    _, rejected = run_claim(client, bounty, console)
    assert rejected["status"] == "rejected", rejected
    assert len(paid_rewards(bounty["id"])) == 1


def test_page_fault_claims_need_exact_details(client, site):  # noqa: F811
    bounty = open_bounty(client, site)
    page = site + "/shop/gallery"
    bad = [
        ("broken_image", [page], {"page": page, "image": site + "/shop/img/a.png?v=2"}),
        ("broken_image", [page, site + "/shop/img/a.png"], {"page": page, "image": site + "/a"}),
        ("broken_image", [page], {"page": page, "url": site + "/shop/img/a.png"}),
        ("page_error", [page], {"page": page, "message": "short"}),
        ("page_error", [page], {"page": page}),
    ]
    for claim_type, urls, details in bad:
        payload = claim_payload("hunter-a.testnet", claim_type, urls, details)
        response = client.post(f"/api/bounties/{bounty['id']}/claims", json=payload)
        assert response.status_code == 422, (claim_type, details, response.text)
