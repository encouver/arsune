"""One broken image is one fault: every spelling of it is paid once."""

import pytest
from test_bounties import client, open_bounty, paid_rewards, run_claim, site, submit  # noqa: F401
from test_bounty_page_faults import image_claim

PAGE = "/shop/team"
IMAGE = "team/ceo.jpg"


@pytest.mark.parametrize(
    "page,image",
    [
        (PAGE, IMAGE),
        (PAGE + "/", IMAGE),
        ("/shop//team", IMAGE),
        (PAGE, "./" + IMAGE),
        (PAGE, "team//ceo.jpg"),
        (PAGE, "team/../team/ceo.jpg"),
        (PAGE, "team/CEO.jpg"),
        (PAGE, "team/ceo%2Ejpg"),
        (PAGE.upper(), IMAGE),
    ],
)
def test_same_broken_image_is_paid_once(client, site, page, image):  # noqa: F811
    bounty = open_bounty(client, site)
    _, first = run_claim(client, bounty, image_claim(site, "hunter-a.testnet", IMAGE, PAGE))
    assert first["status"] == "paid", first
    _, second = run_claim(client, bounty, image_claim(site, "hunter-b.testnet", image, page))
    assert second["status"] in ("duplicate", "rejected", "unverified"), second
    assert len(paid_rewards(bounty["id"])) == 1


def test_concurrent_claims_of_one_broken_image_pay_once(client, site):  # noqa: F811
    import threading

    from app.bounty.worker import settle_claim
    from app.workers import store

    bounty = open_bounty(client, site)
    page, image = site + PAGE, f"{site}/shop/img/{IMAGE}"
    seen = {
        "url": page,
        "final_url": page,
        "images": [{"src": image, "final_url": image, "status": 403, "rendered": True}],
    }
    result = {"status": "confirmed", "checker_reason": "HTTP 403", "evidence": [seen]}
    claims = [
        submit(client, bounty, image_claim(site, f"hunter-{n}.testnet", IMAGE, PAGE))
        for n in range(6)
    ]
    generations = {c["job_id"]: store.claim(c["job_id"])["generation"] for c in claims}
    barrier, errors = threading.Barrier(len(claims)), []

    def settle(job_id):
        try:
            barrier.wait()
            settle_claim(job_id, generations[job_id], result)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=settle, args=(c["job_id"],)) for c in claims]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors
    views = [client.get(f"/api/bounties/{bounty['id']}/claims/{c['id']}").json() for c in claims]
    assert sorted(v["status"] for v in views) == ["duplicate"] * 5 + ["paid"]
    assert len(paid_rewards(bounty["id"])) == 1
