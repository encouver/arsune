"""Zero-setup security claims: the checker proves the demo shop's three plants without login."""

import os
import subprocess
import sys
import time

import httpx
import pytest
from app.bounty.claims import ClaimError, final_fingerprint, fingerprint, scoped_claim
from app.browser.checker import check_candidate
from app.browser.session import inspection_browser
from app.demo_shop import SERVICE_ROLE_KEY, SUPABASE_ANON_KEY, demo_customers
from ports import free_port

PORT = free_port()
ORIGIN = f"http://127.0.0.1:{PORT}"
SITE = ORIGIN + "/shop/"
CUSTOMERS = ORIGIN + "/shop/rest/v1/customers"


@pytest.fixture(scope="module", autouse=True)
def demo_server():
    from app.core.config import settings

    old_demo = settings.demo_url
    settings.demo_url = ORIGIN
    environment = {**os.environ, "ARSUNE_DEMO_PORT": str(PORT)}
    with subprocess.Popen(
        [sys.executable, "-m", "app.demo"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    ) as process:
        for _ in range(50):
            try:
                if httpx.get(SITE, timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            process.terminate()
            raise RuntimeError("Demo did not start.")
        yield
        process.terminate()
        process.wait(timeout=5)
        settings.demo_url = old_demo


async def check(claim_type, urls, details):
    urls, details = await scoped_claim(ORIGIN, claim_type, urls, details)
    item = {"claim_type": claim_type, "urls": urls, "details": details}

    async def keep():
        pass

    async with inspection_browser(ORIGIN, ORIGIN, {"selected": urls}, keep) as (context, _):

        async def observe(target, viewport):
            raise AssertionError("A security claim reads no page.")

        return await check_candidate(item, observe, context, site_url=SITE)


def exposure(query, key=SUPABASE_ANON_KEY):
    step = {"method": "GET", "url": CUSTOMERS + query, "headers": {"apikey": key}, "body": None}
    return [CUSTOMERS + query], {"steps": [step]}


async def test_open_customers_table_is_proven_with_masked_samples_only():
    result = await check("data_exposure", *exposure("?select=*"))
    assert result["status"] == "confirmed", result["checker_reason"]
    assert "25 people" in result["checker_reason"]
    summary = result["evidence"][-1]
    assert summary["people"] == 25 and len(summary["samples"]) == 3
    public = repr({k: v for k, v in result.items() if k != "details"})
    for row in demo_customers():
        assert row["email"] not in public and row["phone"] not in public
    again = final_fingerprint("data_exposure", result["urls"], result["details"], [])
    assert again == fingerprint("data_exposure", [CUSTOMERS], {})


async def test_the_apps_own_narrow_query_is_rejected():
    result = await check("data_exposure", *exposure("?select=first_name&id=eq.1"))
    assert result["status"] == "rejected", result["checker_reason"]
    names_only = await check("data_exposure", *exposure("?select=first_name"))
    assert names_only["status"] == "rejected"
    no_key = await check("data_exposure", *exposure("?select=*", key="wrong"))
    assert no_key["status"] == "rejected"


async def test_service_role_key_in_admin_chunk_is_proven_masked():
    url = ORIGIN + "/shop/static/chunk-admin.js"
    details = {"url": url, "kind": "supabase_service_role"}
    result = await check("secret_key", [url], details)
    assert result["status"] == "confirmed", result["checker_reason"]
    assert SERVICE_ROLE_KEY not in repr(result)
    assert result["evidence"][0]["masked"] == SERVICE_ROLE_KEY[:6] + "****" + SERVICE_ROLE_KEY[-4:]
    first = final_fingerprint("secret_key", result["urls"], result["details"], result["evidence"])
    # The same key found in another file is the same fault.
    moved = [{**result["evidence"][0], "url": ORIGIN + "/shop/static/other.js"}]
    assert first == final_fingerprint("secret_key", [], {}, moved)
    # app.js holds only the anon key, so a service_role claim on it is false.
    url = ORIGIN + "/shop/static/app.js"
    anon = await check("secret_key", [url], {"url": url, "kind": "supabase_service_role"})
    assert anon["status"] == "rejected"


async def test_env_backup_is_proven_by_names_only():
    url = ORIGIN + "/shop/.env.backup"
    result = await check("private_file", [url], {"url": url, "kind": "env"})
    assert result["status"] == "confirmed", result["checker_reason"]
    assert "DATABASE_URL" in result["evidence"][0]["names"]
    assert "demo-not-a-real-password" not in repr(result)
    # A normal page is not an env file.
    page = ORIGIN + "/shop/"
    harmless = await check("private_file", [page], {"url": page, "kind": "env"})
    assert harmless["status"] == "rejected"


async def test_claim_rules(monkeypatch):
    bad = [
        ("data_exposure", [CUSTOMERS], {"steps": []}),
        (
            "data_exposure",
            ["https://evil.example/x"],
            {"steps": [{"method": "GET", "url": "https://evil.example/x"}]},
        ),
        (
            "data_exposure",
            [CUSTOMERS],
            {"steps": [{"method": "GET", "url": CUSTOMERS, "headers": {"Cookie": "a=b"}}]},
        ),
        ("data_exposure", [CUSTOMERS], {"steps": [{"method": "DELETE", "url": CUSTOMERS}]}),
        ("secret_key", [CUSTOMERS], {"url": CUSTOMERS, "kind": "password"}),
        ("private_file", [CUSTOMERS], {"url": CUSTOMERS, "kind": "env", "extra": 1}),
        ("private_file", [ORIGIN + "/other"], {"url": CUSTOMERS, "kind": "env"}),
    ]
    for claim_type, urls, details in bad:
        with pytest.raises(ClaimError):
            await scoped_claim(ORIGIN, claim_type, urls, details)

    # A known BaaS host is in scope for data exposure (no DNS lookup in this test).
    async def public(url, demo_origin=None):
        return url

    monkeypatch.setattr("app.bounty.claims.validate_target", public)
    urls, details = await scoped_claim(
        ORIGIN,
        "data_exposure",
        ["https://abcd.supabase.co/rest/v1/users"],
        {"steps": [{"method": "GET", "url": "https://abcd.supabase.co/rest/v1/users?select=*"}]},
    )
    assert urls == ["https://abcd.supabase.co/rest/v1/users"]
    assert details["steps"][0]["headers"] is None
