import os
import re
import subprocess
import sys
import time
from urllib.parse import urljoin, urlsplit

import httpx
import pytest
from app.browser.checker import check_candidate
from app.browser.observations import observe_page
from app.browser.session import inspection_browser
from app.core.scan import VIEWPORTS
from app.demo_shop import build_ground_truth

ORIGIN = "http://127.0.0.1:18541"
TRUTH = build_ground_truth(ORIGIN)
BUGS, DECOYS = TRUTH["bugs"], TRUTH["decoys"]


@pytest.fixture(scope="module", autouse=True)
def demo_server():
    from app.core.config import settings

    old_demo = settings.demo_url
    settings.demo_url = ORIGIN
    environment = {**os.environ, "ARSUNE_DEMO_PORT": "18541"}
    with subprocess.Popen(
        [sys.executable, "-m", "app.demo"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    ) as process:
        for _ in range(50):
            try:
                if httpx.get(ORIGIN + "/shop/", timeout=0.2).status_code == 200:
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


async def check(claim, tmp_path):
    item = {k: claim[k] for k in ("claim_type", "urls", "details")}
    state = {"selected": list(claim["urls"])}

    async def keep():
        pass

    async with inspection_browser(ORIGIN, ORIGIN, state, keep) as (context, _):

        async def observe(target, viewport):
            return await observe_page(context, target, viewport, tmp_path, ORIGIN)

        return await check_candidate(item, observe)


def test_ground_truth_counts():
    kinds = [b["claim_type"] for b in BUGS]
    assert kinds.count("broken_link") == 20
    assert kinds.count("horizontal_overflow") == 15
    assert kinds.count("price_mismatch") == 15
    assert len(DECOYS) >= 15
    assert len(TRUTH["pages"]) >= 40


def test_ground_truth_is_not_served():
    for path in ("/shop/ground-truth", "/shop/bugs", "/shop/debug"):
        assert httpx.get(ORIGIN + path).status_code in {404, 422}


def test_bug_targets_have_expected_status():
    for bug in BUGS:
        if bug["claim_type"] == "broken_link":
            response = httpx.get(bug["details"]["target"])
            assert response.status_code == bug["http_status"]


def crawl():
    seen, queue, statuses, hrefs = set(), [ORIGIN + "/shop/"], {}, set()
    while queue:
        url = queue.pop()
        if url in seen:
            continue
        seen.add(url)
        response = httpx.get(url, follow_redirects=False)
        statuses[url] = response.status_code
        if response.status_code in {301, 302}:
            queue.append(urljoin(url, response.headers["location"]))
        if "text/html" not in response.headers.get("content-type", ""):
            continue
        for href in re.findall(r'href="([^"]+)"', response.text):
            link = urljoin(url, href).split("#")[0]
            hrefs.add(link)
            if urlsplit(link).path.startswith("/shop/"):
                queue.append(link)
    return statuses, hrefs


def test_crawl_from_shop_reaches_every_page():
    statuses, hrefs = crawl()
    reached = {url for url, status in statuses.items() if status == 200}
    assert set(TRUTH["pages"]) <= reached
    orphan = ORIGIN + "/shop/retired/old-newsletter"
    assert orphan not in hrefs
    for bug in BUGS:
        if bug["claim_type"] == "broken_link":
            assert bug["details"]["target"] in hrefs


@pytest.mark.parametrize("bug", BUGS, ids=[b["id"] for b in BUGS])
async def test_seeded_bug_is_confirmed(bug, tmp_path):
    result = await check(bug, tmp_path)
    assert result["status"] == "confirmed", result["checker_reason"]


@pytest.mark.parametrize("decoy", DECOYS, ids=[d["id"] for d in DECOYS])
async def test_decoy_is_not_confirmed(decoy, tmp_path):
    result = await check(decoy, tmp_path)
    assert result["status"] in {"rejected", "unverified"}, result["checker_reason"]
    assert result["status"] == decoy["expected"], result["checker_reason"]


async def test_clean_pages_fit_mobile_viewport(tmp_path):
    flawed = {b["details"]["url"] for b in BUGS if b["claim_type"] == "horizontal_overflow"}
    pages = [p for p in TRUTH["pages"] if p not in flawed]
    state = {"selected": pages}

    async def keep():
        pass

    async with inspection_browser(ORIGIN, ORIGIN, state, keep) as (context, _):
        for page in pages[:30]:
            seen = await observe_page(context, page, VIEWPORTS["mobile"], tmp_path, ORIGIN)
            assert seen["scroll_width"] <= 392, page
