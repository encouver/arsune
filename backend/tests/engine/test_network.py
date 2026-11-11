"""Real Chromium requests against controlled servers with public-address routing."""

import asyncio
import copy
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from app.bounty.worker import browser_check
from app.browser import session
from app.browser.checker import check_candidate
from app.browser.observations import observe_page
from app.browser.session import inspection_browser
from app.workers.workflow import run_scan


@pytest.fixture
def controlled_server():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {**os.environ, "ARSUNE_DEMO_PORT": str(port)}
    with subprocess.Popen(
        [sys.executable, "-m", "app.demo"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        for _ in range(80):
            try:
                if (
                    httpx.get(f"http://127.0.0.1:{port}/network/healthy", timeout=0.2).status_code
                    == 200
                ):
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            process.terminate()
            raise RuntimeError("Controlled server did not start.")
        yield port
        process.terminate()
        process.wait(timeout=5)


@pytest.fixture
async def public_network(controlled_server, monkeypatch):
    """Only the named fixture hosts map to public IP, then to this test server."""
    port = controlled_server
    loop = asyncio.get_running_loop()
    resolve = loop.getaddrinfo
    calls = []
    original_client = httpx.AsyncClient

    async def fixture_dns(host, requested_port, *args, **kwargs):
        if host in {"site.test", "cdn.test", "www.site.test"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", requested_port))]
        return await resolve(host, requested_port, *args, **kwargs)

    class ControlledTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.transport = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request):
            # The production resolver must have pinned this request first.
            calls.append((request.headers.get("host"), str(request.url)))
            if request.url.host == "93.184.216.34":
                assert request.url.port == port
                request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=port)
            else:
                # A policy failure must reach the real sentinel and fail the test.
                assert request.url.host == "127.0.0.1" and request.url.port == 8542
            return await self.transport.handle_async_request(request)

        async def aclose(self):
            await self.transport.aclose()

    def fixture_client(*args, **kwargs):
        kwargs["transport"] = ControlledTransport()
        return original_client(*args, **kwargs)

    monkeypatch.setattr(loop, "getaddrinfo", fixture_dns)
    monkeypatch.setattr(session.httpx, "AsyncClient", fixture_client)
    return f"http://site.test:{port}/network", calls


@pytest.fixture
def private_sentinel():
    hits = []

    class Sentinel(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"private sentinel")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 8542), Sentinel)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield hits
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


async def test_redirect_preserves_final_url_and_relative_resources(public_network, tmp_path):
    base, calls = public_network
    report = await run_scan(f"{base}/start", tmp_path)
    observations = [o for o in report["observations"] if o["url"] == f"{base}/start"]
    assert observations
    assert all(o.get("final_url") == f"{base}/final/index.html" for o in observations)
    assert any("/network/final/site.css" in url for _, url in calls)
    assert f"{base}/start" in report["inspected_pages"]
    assert f"{base}/final/index.html" in report["inspected_pages"]
    assert report["coverage"]["redirects"]
    assert not report["coverage"]["incomplete_layout_pages"]


@pytest.mark.parametrize("path", ["upgrade", "www"])
async def test_approved_origin_redirects(public_network, tmp_path, path):
    base, _ = public_network
    report = await run_scan(f"{base}/{path}", tmp_path)
    first = report["observations"][0]
    assert first["status"] == 200
    assert first["final_url"].endswith("/network/final/index.html")
    assert first["final_url"].startswith("https://" if path == "upgrade" else "http://www.")


@pytest.mark.parametrize("path", ["loop/a", "chain/0"])
async def test_redirect_loops_and_limit_are_bounded(public_network, tmp_path, path):
    base, calls = public_network
    report = await run_scan(f"{base}/{path}", tmp_path)
    assert report["confirmed"] == []
    assert report["coverage"]["incomplete_pages"]
    assert len(report["inspected_pages"]) <= 5
    assert report["coverage"]["blocked_requests"]
    # Three successful redirects permit at most four requests per observation.
    assert len(calls) <= 5 * 2 * 4


@pytest.mark.parametrize("path", ["private", "private-resource"])
async def test_forbidden_targets_never_reach_server(
    public_network, private_sentinel, tmp_path, path
):
    base, _ = public_network
    report = await run_scan(f"{base}/{path}", tmp_path)
    assert private_sentinel == []
    assert "http://127.0.0.1:8542/internal" in report["coverage"]["blocked_requests"]
    assert report["confirmed"] == []


async def test_external_images_and_scripts_execute(public_network, tmp_path):
    base, calls = public_network
    url = f"{base}/external"
    state = {"selected": [url]}

    async def save():
        pass

    async with inspection_browser(url, "http://127.0.0.1:8541", state, save) as (context, blocked):
        page = await context.new_page()
        await context._arsune_prepare_page(page)
        await page.goto(url, wait_until="load")
        assert await page.evaluate("document.body.dataset.externalScript") == "loaded"
        assert await page.locator("#external-image").evaluate("image => image.naturalWidth") == 7
        assert not blocked
    assert any(host.startswith("cdn.test:") and "/script.js" in url for host, url in calls)


async def test_delayed_css_is_ready_before_layout_measurement(public_network, tmp_path):
    base, _ = public_network
    report = await run_scan(f"{base}/delayed-style", tmp_path)
    assert report["confirmed"] == []
    assert all(o.get("layout_complete") for o in report["observations"])
    assert all(o["scroll_width"] <= o["client_width"] for o in report["observations"])


async def test_missing_css_marks_layout_incomplete(public_network, tmp_path):
    base, _ = public_network
    report = await run_scan(f"{base}/missing-style", tmp_path)
    assert not any(f["claim_type"] == "horizontal_overflow" for f in report["confirmed"])
    assert report["coverage"]["incomplete_layout_pages"]
    assert report["coverage"]["resource_issues"]
    assert all(o.get("layout_complete") is False for o in report["observations"])


async def test_page_limit_counts_redirect_hops_and_discovery(public_network, tmp_path):
    base, _ = public_network
    report = await run_scan(f"{base}/many-pages", tmp_path)
    assert len(report["inspected_pages"]) == 5
    assert all(o["url"] in report["inspected_pages"] for o in report["observations"])


async def test_checkpoint_keeps_network_issues_and_budgets(public_network, tmp_path):
    base, _ = public_network
    snapshots = []
    url = f"{base}/private-resource"
    report = await run_scan(
        url, tmp_path, save_checkpoint=lambda state: snapshots.append(copy.deepcopy(state))
    )
    # A JSON round trip is the worker restart boundary, not shared Python state.
    checkpoint = json.loads(json.dumps(snapshots[-1]))
    assert checkpoint["blocked_requests"]
    assert checkpoint["resource_issues"]
    resumed = await run_scan(url, tmp_path, checkpoint=checkpoint)
    for field in ["requests", "transfer_bytes", "blocked_requests", "resource_issues"]:
        assert resumed["coverage"][field] == report["coverage"][field]


async def test_dns_rebinding_is_blocked_before_connection(
    public_network, private_sentinel, tmp_path, monkeypatch
):
    base, _ = public_network
    loop = asyncio.get_running_loop()
    resolve = loop.getaddrinfo
    lookups = 0

    async def changing_dns(host, port, *args, **kwargs):
        nonlocal lookups
        if host == "rebind.test":
            lookups += 1
            address = "93.184.216.34" if lookups % 2 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
        return await resolve(host, port, *args, **kwargs)

    monkeypatch.setattr(loop, "getaddrinfo", changing_dns)
    report = await run_scan(f"{base}/dns-resource", tmp_path)
    assert lookups >= 2
    assert private_sentinel == []
    assert "http://rebind.test:8542/network/site.css" in report["coverage"]["blocked_requests"]


async def test_redirect_document_cannot_exceed_five_pages(public_network, tmp_path):
    base, calls = public_network
    url = f"{base}/start"
    state = {
        "selected": [url, *[f"{base}/page/{n}" for n in range(4)]],
        "observations": [],
        "checks": {},
        "activity": [],
    }
    report = await run_scan(url, tmp_path, checkpoint=state)
    assert len(report["inspected_pages"]) == 5
    assert f"{base}/final/index.html" not in report["inspected_pages"]
    assert f"{base}/final/index.html" in report["coverage"]["blocked_requests"]
    assert not any("/network/final/index.html" in request for _, request in calls)


async def test_exhausted_transfer_budget_survives_restart(public_network, tmp_path):
    base, calls = public_network
    url = f"{base}/healthy"
    state = {
        "selected": [url],
        "observations": [],
        "checks": {},
        "activity": [],
        "transfer_reserved_bytes": 20_000_000,
    }
    report = await run_scan(url, tmp_path, checkpoint=json.loads(json.dumps(state)))
    assert report["coverage"]["transfer_bytes"] == 20_000_000
    assert report["coverage"]["incomplete_pages"]
    assert report["confirmed"] == []
    assert calls == []


async def test_failed_font_marks_layout_incomplete_and_persists_reason(public_network, tmp_path):
    base, calls = public_network
    snapshots = []
    url = f"{base}/failed-font"
    report = await run_scan(
        url, tmp_path, save_checkpoint=lambda state: snapshots.append(copy.deepcopy(state))
    )
    assert any("/network/invalid.woff2" in request for _, request in calls)
    assert all(o["status"] == 200 for o in report["observations"])
    assert all(o["layout_complete"] is False for o in report["observations"])
    assert any(o["scroll_width"] > o["client_width"] for o in report["observations"])
    assert not any(f["claim_type"] == "horizontal_overflow" for f in report["confirmed"])
    readiness = [
        issue
        for issue in report["coverage"]["resource_issues"]
        if issue["resource_type"] == "Readiness" and "fonts" in issue["reason"]
    ]
    assert readiness
    checkpoint = json.loads(json.dumps(snapshots[-1]))
    assert all(issue in checkpoint["resource_issues"] for issue in readiness)
    resumed = await run_scan(url, tmp_path, checkpoint=checkpoint)
    assert resumed["coverage"]["resource_issues"] == report["coverage"]["resource_issues"]
    assert resumed["coverage"]["incomplete_layout_pages"]


async def test_errors_from_another_origins_script_do_not_count(public_network, tmp_path):
    base, calls = public_network
    url = f"{base}/script-errors"
    state = {"selected": [url]}

    async def save():
        pass

    results = {}
    async with inspection_browser(url, "http://127.0.0.1:8541", state, save) as (context, _):

        async def observe(target, viewport):
            return await observe_page(context, target, viewport, tmp_path, url)

        for message in ("Partner widget crashed on load", "Site menu failed on load"):
            item = {
                "claim_type": "page_error",
                "urls": [url],
                "details": {"page": url, "message": message},
            }
            results[message] = await check_candidate(item, observe)
    assert any("/network/throw.js" in request for _, request in calls)
    errors = results["Site menu failed on load"]["evidence"][0]["page_errors"]
    assert sorted((e["message"], e["first_party"], e["source"].split(":")[1]) for e in errors) == [
        ("Error: Partner widget crashed on load", False, "//cdn.test"),
        ("Error: Site menu failed on load", True, "//site.test"),
    ]
    assert results["Partner widget crashed on load"]["status"] == "unverified"
    assert results["Site menu failed on load"]["status"] == "confirmed"


def claim(claim_type, *urls, **details):
    keys = {
        "broken_link": ("source", "target"),
        "horizontal_overflow": ("url",),
        "page_error": ("page",),
    }[claim_type]
    return {
        "id": "test-claim",
        "claim_type": claim_type,
        "urls": list(urls),
        "details": dict(zip(keys, urls, strict=False)) | details,
    }


async def test_parallel_resources_wait_for_the_transfer_budget(public_network, tmp_path):
    # Each request in flight reserves 2 MB of the 20 MB budget; the 11th used to fail at once.
    base, calls = public_network
    url = f"{base}/parallel-styles"
    result = await browser_check("job", claim("horizontal_overflow", url), base, tmp_path)
    observation = result["evidence"][0]
    assert sum("/network/parallel/" in request for _, request in calls) == 16
    assert observation["resource_issues"] == []
    assert observation["layout_complete"] is True
    assert result["status"] == "rejected", result["checker_reason"]


async def test_brotli_resource_reaches_the_browser_decoded(public_network, tmp_path):
    base, _ = public_network
    url = f"{base}/brotli-script"
    state = {"selected": [url]}

    async def save():
        pass

    async with inspection_browser(url, "http://127.0.0.1:8541", state, save) as (context, blocked):
        page = await context.new_page()
        await context._arsune_prepare_page(page)
        await page.goto(url, wait_until="load")
        assert await page.evaluate("document.body.dataset.brotliScript") == "loaded"
        assert page._arsune_errors[0] == []
        assert not blocked


async def test_undecoded_resource_is_an_arsune_limit_not_a_page_error(public_network, tmp_path):
    # A body still in an encoding that Arsune cannot decode must never run as the page's script.
    base, _ = public_network
    url = f"{base}/undecoded-script"
    item = claim("page_error", url, message="SyntaxError: Invalid or unexpected token")
    result = await browser_check("job", item, base, tmp_path)
    observation = result["evidence"][0]
    assert observation["page_errors"] == []
    [issue] = observation["resource_issues"]
    assert issue["resource_type"] == "Script" and "http_status" not in issue
    assert "content encoding" in issue["reason"]
    assert result["status"] == "unverified"


async def test_third_party_beacons_do_not_stop_verdicts(public_network, tmp_path):
    base, _ = public_network
    url = f"{base}/third-party-beacon"
    overflow = await browser_check("job", claim("horizontal_overflow", url), base, tmp_path)
    assert overflow["status"] == "confirmed", overflow["checker_reason"]
    assert overflow["evidence"][0]["layout_complete"] is True
    issues = overflow["evidence"][0]["resource_issues"]
    [beacon] = [i for i in issues if i["resource_type"] == "Policy"]
    assert beacon["directive"] == "connect-src" and beacon["third_party_data"] is True
    assert beacon["source"].endswith("/network/beacon.js")
    # The tag's image fallback: another site answered it with an HTTP error.
    assert [i.get("http_status") for i in issues if i["resource_type"] == "Image"] == [405]
    error = await browser_check(
        "job", claim("page_error", url, message="Site menu failed on load"), base, tmp_path
    )
    assert error["status"] == "confirmed", error["checker_reason"]
    link = await browser_check("job", claim("broken_link", url, f"{base}/healthy"), base, tmp_path)
    assert link["status"] == "rejected", link["checker_reason"]


async def test_site_data_requests_still_count_as_blocked(public_network, tmp_path):
    base, _ = public_network
    url = f"{base}/site-data"
    overflow = await browser_check("job", claim("horizontal_overflow", url), base, tmp_path)
    [blocked] = overflow["evidence"][0]["resource_issues"]
    assert blocked["resource_type"] == "Policy" and blocked["third_party_data"] is False
    assert overflow["evidence"][0]["layout_complete"] is False
    assert overflow["status"] == "unverified"
    error = await browser_check(
        "job", claim("page_error", url, message="Site menu failed on load"), base, tmp_path
    )
    assert error["status"] == "unverified"
    assert "Arsune's limits blocked" in error["checker_reason"]
