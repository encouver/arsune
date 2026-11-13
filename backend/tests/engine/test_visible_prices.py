"""Pricing checks use visible HTML and fresh Chromium reads, without fact annotations."""

import os
import struct
import subprocess
import sys
import time

import httpx
import pytest
from app.agents.candidates import scripted
from app.browser.checker import check_candidate
from app.browser.observations import observe_page
from app.browser.session import inspection_browser
from app.core.scan import VIEWPORTS
from app.workers.workflow import run_scan
from ports import free_port

PORT = free_port()


@pytest.fixture(scope="module")
def price_site():
    from app.core.config import settings

    previous = settings.demo_url
    settings.demo_url = f"http://127.0.0.1:{PORT}"
    with subprocess.Popen(
        [sys.executable, "-m", "app.demo"],
        env={**os.environ, "ARSUNE_DEMO_PORT": str(PORT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            for _ in range(60):
                try:
                    if httpx.get(settings.demo_url + "/prices/equal/first", timeout=0.2).is_success:
                        break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Pricing fixture did not start.")
            yield settings.demo_url
        finally:
            process.terminate()
            process.wait(timeout=5)
            settings.demo_url = previous


async def read_pair(site, case, directory):
    directory.mkdir()
    urls = [f"{site}/prices/{case}/{page}" for page in ("first", "second")]
    state = {"selected": urls}

    async def save():
        pass

    async with inspection_browser(urls[0], site, state, save) as (context, _):
        observations = [
            await observe_page(context, url, VIEWPORTS["desktop"], directory, urls[0])
            for url in urls
        ]
        page = await context.new_page()
        await context._arsune_prepare_page(page)
        try:
            for observation in observations:
                await page.goto(observation["url"])
                for fact in observation["facts"]:
                    source = page.locator(fact["selector"])
                    assert await source.count() == 1
                    assert await source.is_visible()
                    assert (await source.inner_text()).strip() == fact["quote"]
                    assert isinstance(fact["source_position"], int)
        finally:
            await page.close()
        return observations


async def test_visible_price_conflict_is_confirmed_and_repair_resolves(price_site, tmp_path):
    site = price_site
    async with httpx.AsyncClient() as client:
        (
            await client.post(site + "/comparison-control", json={"state": "faulty"})
        ).raise_for_status()
    url = site + "/prices/different/first"
    async with httpx.AsyncClient() as client:
        assert "data-fact" not in (await client.get(url)).text
    baseline = await run_scan(url, tmp_path / "before")
    prices = [f for f in baseline["confirmed"] if f["claim_type"] == "price_mismatch"]
    assert len(prices) == 1
    finding = prices[0]
    for evidence in finding["evidence"]:
        assert evidence["captured_at"]
        assert (tmp_path / "before" / evidence["screenshot"]).is_file()
        facts = [fact for fact in evidence["facts"] if fact["key"] == finding["details"]["key"]]
        assert len(facts) == 1
        fact = facts[0]
        assert fact["source"] == "visible_price"
        assert "Basic" in fact["quote"] and "USD" in fact["quote"]
        assert fact["selector"] and "data-fact" not in fact["selector"]
        assert fact["context"]["currency"] == "USD"
        initial = next(o for o in baseline["observations"] if o["url"] == evidence["url"])
        assert initial["captured_at"] != evidence["captured_at"]
        assert initial["screenshot"] != evidence["screenshot"]
    async with httpx.AsyncClient() as client:
        (
            await client.post(site + "/comparison-control", json={"state": "repaired"})
        ).raise_for_status()
    fixed = await run_scan(
        url, tmp_path / "after", baseline_report=baseline, baseline_job_id="price-before"
    )
    assert not [f for f in fixed["confirmed"] if f["claim_type"] == "price_mismatch"]
    resolved = fixed["comparison"]["resolved"]
    assert len(resolved) == 1
    assert resolved[0]["baseline_finding_id"] == finding["id"]
    assert resolved[0]["current_finding"]["status"] == "rejected"
    assert resolved[0]["current_finding"]["details"]["key"] == finding["details"]["key"]
    for evidence in resolved[0]["current_finding"]["evidence"]:
        assert (tmp_path / "after" / evidence["screenshot"]).is_file()


@pytest.mark.parametrize(
    "case",
    [
        "equal",
        "annual",
        "annual-equivalent",
        "currency",
        "variant",
        "unit",
        "quantity",
        "product",
        "qualifier",
        "missing-period",
        "missing-currency",
        "ambiguous-dollar",
        "from",
        "trial",
        "discount",
        "strike",
        "hidden",
    ],
)
async def test_distinct_or_unsupported_offers_are_not_price_faults(price_site, tmp_path, case):
    observations = await read_pair(price_site, case, tmp_path / case)
    assert all(o["status"] == 200 and o["layout_complete"] for o in observations)
    assert not [c for c in scripted(observations) if c["claim_type"] == "price_mismatch"]
    facts = [fact for o in observations for fact in o["facts"]]
    if case in {"equal", "hidden"}:
        assert len(facts) == 2
        assert facts[0]["key"] == facts[1]["key"]
        assert facts[0]["value"] == facts[1]["value"]
        assert all("99" not in fact["quote"] for fact in facts)
    if case in {"annual", "currency", "variant", "unit", "product", "qualifier"}:
        assert len(facts) == 2
        assert facts[0]["key"] != facts[1]["key"]
    if case in {
        "missing-period",
        "missing-currency",
        "ambiguous-dollar",
        "from",
        "trial",
        "discount",
    }:
        assert facts == []
        assert any(o.get("price_exclusions") for o in observations)


async def test_missing_context_cannot_confirm_a_supplied_price_claim(price_site, tmp_path):
    observations = await read_pair(price_site, "missing-period", tmp_path / "missing")
    calls = []

    async def observe(url, viewport):
        calls.append(url)
        return next(o for o in observations if o["url"] == url)

    item = {
        "claim_type": "price_mismatch",
        "urls": [o["url"] for o in observations],
        "details": {"key": "price|basic|USD|month|seat"},
    }
    result = await check_candidate(item, observe)
    assert result["status"] == "unverified"
    assert calls == item["urls"]


async def test_below_fold_prices_have_card_screenshots(price_site, tmp_path):
    directory = tmp_path / "below-fold"
    observations = await read_pair(price_site, "below-fold", directory)
    for observation in observations:
        assert observation["status"] == 200 and observation["layout_complete"]
        assert len(observation["facts"]) == 1
        fact = observation["facts"][0]
        assert " ".join(fact["quote"].split()) == "Basic USD 19 per month, per seat"
        assert fact["screenshot"] != observation["screenshot"]
        screenshot = directory / fact["screenshot"]
        assert screenshot.is_file()
        image = screenshot.read_bytes()
        assert image[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", image[16:24])
        assert 0 < width <= 320
        assert 0 < height < VIEWPORTS["desktop"]["height"]
