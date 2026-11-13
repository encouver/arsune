"""Checker, candidate, and claim rules for broken images and page errors, without a browser."""

import pytest
from app.agents.candidates import scripted, validate_candidates
from app.bounty.claims import ClaimError, final_fingerprint, fingerprint, scoped_claim
from app.browser.checker import check_candidate
from app.browser.observations import mark_third_party_data, stops_layout
from app.browser.policy import origin
from app.core.scan import VIEWPORTS
from ports import free_port

PORT = free_port()
SITE = f"http://127.0.0.1:{PORT}"
PAGE = SITE + "/shop/gallery"
IMAGE = SITE + "/shop/img/gallery/dashboard.png"
SITE_404 = {
    "url": IMAGE,
    "page_url": PAGE,
    "resource_type": "Image",
    "reason": "Resource returned HTTP 404.",
    "http_status": 404,
}
BLOCKED = {
    "url": "http://127.0.0.1:8542/x.js",
    "page_url": PAGE,
    "resource_type": "Script",
    "reason": "Resolved destination is private.",
}
POLICY = {
    "url": "",
    "page_url": PAGE,
    "resource_type": "Policy",
    "reason": "Browser policy blocked connect-src.",
}
# An analytics tag on another site sends a data request to its own site.
THIRD_PARTY_DATA = {
    "url": "https://www.google.com/ccm/collect?v=1",
    "page_url": PAGE,
    "resource_type": "Policy",
    "reason": "Browser policy blocked connect-src.",
    "directive": "connect-src",
    "source": "https://www.googletagmanager.com/gtag/js",
    "third_party_data": True,
}
UNREADY = {
    "url": PAGE,
    "page_url": PAGE,
    "resource_type": "Readiness",
    "reason": "Resource readiness deadline exceeded.",
}


def image(src=IMAGE, **fields):
    record = {
        "src": src,
        "final_url": src,
        "status": 404,
        "failure": "Resource returned HTTP 404.",
        "decoded": False,
        "rendered": True,
    }
    return record | fields


def error(message="TypeError: Cannot read properties of undefined (reading 'items')", **fields):
    return {
        "message": message,
        "kind": "error",
        "source": origin(SITE),
        "first_party": True,
    } | fields


def page(images=(), errors=(), issues=(), **fields):
    return {
        "url": PAGE,
        "final_url": PAGE,
        "viewport": VIEWPORTS["desktop"],
        "status": 200,
        "links": [],
        "facts": [],
        "images": list(images),
        "images_truncated": False,
        "page_errors": list(errors),
        "page_errors_truncated": False,
        "resource_issues": list(issues),
        "layout_complete": not issues,
    } | fields


async def check(claim_type, details, observation):
    seen = []

    async def observe(url, viewport):
        seen.append(viewport)
        return {**observation, "url": url}

    result = await check_candidate(
        {"claim_type": claim_type, "urls": [details["page"]], "details": details}, observe
    )
    assert seen == [VIEWPORTS["desktop"]]
    return result


@pytest.mark.parametrize(
    ("images", "claimed", "expected"),
    [
        ([image()], IMAGE, "confirmed"),
        ([image(status=500)], IMAGE, "confirmed"),
        ([image(status=200, failure=None)], IMAGE, "confirmed"),
        ([image(src=IMAGE + "?v=3")], IMAGE, "confirmed"),
        ([image(src=SITE + "/shop/img/old.png")], IMAGE, "unverified"),
        (
            [image(src=SITE + "/shop/img/old.png", final_url=IMAGE)],
            IMAGE,
            "confirmed",
        ),
        ([image(status=200, failure=None, decoded=True)], IMAGE, "rejected"),
        ([image(status=None, failure=None, decoded=True)], IMAGE, "rejected"),
        ([image(rendered=False)], IMAGE, "unverified"),
        ([image(status=200, failure=None, decoded=None)], IMAGE, "unverified"),
        ([image(status=None, failure="Request budget exceeded.")], IMAGE, "unverified"),
        ([image(status=200, failure="Resource budget exceeded.")], IMAGE, "unverified"),
        ([image(status=301, failure="Redirect limit exceeded.")], IMAGE, "unverified"),
        ([], IMAGE, "unverified"),
    ],
)
async def test_broken_image_states(images, claimed, expected):
    result = await check("broken_image", {"page": PAGE, "image": claimed}, page(images))
    assert result["status"] == expected, result["checker_reason"]


async def test_broken_image_needs_a_successful_page_read():
    for observation in (page([image()], status=404), page([image()], error="Browser failed.")):
        result = await check("broken_image", {"page": PAGE, "image": IMAGE}, observation)
        assert result["status"] == "unverified"


async def test_confirmed_broken_image_explains_impact_and_repair():
    result = await check("broken_image", {"page": PAGE, "image": IMAGE}, page([image()]))
    assert "HTTP 404" in result["checker_reason"]
    assert "picture" in result["visitor_impact"]
    assert result["suggested_correction"] == "Restore the image file or correct the image address."
    decoded = await check(
        "broken_image",
        {"page": PAGE, "image": IMAGE},
        page([image(status=200, failure=None)]),
    )
    assert "could not decode" in decoded["checker_reason"]


@pytest.mark.parametrize(
    ("errors", "issues", "message", "expected"),
    [
        ([error()], [], "cannot read properties of UNDEFINED", "confirmed"),
        ([error()], [], "  Cannot read\n properties  ", "confirmed"),
        ([error(kind="rejection")], [], "reading 'items'", "confirmed"),
        ([error()], [SITE_404], "reading 'items'", "confirmed"),
        ([error()], [UNREADY], "reading 'items'", "confirmed"),
        ([error()], [BLOCKED], "reading 'items'", "unverified"),
        ([error()], [POLICY], "reading 'items'", "unverified"),
        ([error()], [THIRD_PARTY_DATA], "reading 'items'", "confirmed"),
        ([error()], [THIRD_PARTY_DATA, BLOCKED], "reading 'items'", "unverified"),
        ([error(first_party=False, source="")], [], "reading 'items'", "unverified"),
        ([error()], [], "Recommendation feed returned no plans", "unverified"),
        ([], [], "Recommendation feed returned no plans", "rejected"),
        ([error(first_party=False)], [], "Recommendation feed returned no plans", "rejected"),
        ([], [SITE_404], "Recommendation feed returned no plans", "rejected"),
        ([], [UNREADY], "Recommendation feed returned no plans", "unverified"),
        ([], [BLOCKED], "Recommendation feed returned no plans", "unverified"),
        ([], [POLICY], "Recommendation feed returned no plans", "unverified"),
        ([], [THIRD_PARTY_DATA], "Recommendation feed returned no plans", "rejected"),
        ([error()], [], "items'", "unverified"),
        ([error()], [], 42, "unverified"),
    ],
)
async def test_page_error_states(errors, issues, message, expected):
    result = await check(
        "page_error", {"page": PAGE, "message": message}, page(errors=errors, issues=issues)
    )
    assert result["status"] == expected, result["checker_reason"]


TAG = "https://www.googletagmanager.com/gtag/js"
BEACON = "https://www.google.com/ccm/collect?v=1"


@pytest.mark.parametrize(
    ("directive", "url", "source", "expected"),
    [
        ("connect-src", BEACON, TAG, True),
        # The site's own script, or a request to the site, can feed the page.
        ("connect-src", BEACON, SITE + "/shop/app.js", False),
        ("connect-src", BEACON, PAGE, False),
        ("connect-src", SITE + "/api/plans", TAG, False),
        ("connect-src", BEACON, "", False),
        ("connect-src", "", TAG, False),
        ("connect-src", BEACON, None, False),
        ("frame-src", BEACON, TAG, False),
        (None, BEACON, TAG, False),
    ],
)
def test_only_another_sites_data_request_is_third_party_data(directive, url, source, expected):
    issue = {"url": url, "resource_type": "Policy", "directive": directive, "source": source}
    assert mark_third_party_data(issue, PAGE)["third_party_data"] is expected
    assert stops_layout(issue) is not expected


@pytest.mark.parametrize(
    ("issue", "expected"),
    [
        (SITE_404, False),
        (THIRD_PARTY_DATA, False),
        (POLICY, True),
        (BLOCKED, True),
        (UNREADY, True),
        ({**SITE_404, "resource_type": "Stylesheet"}, True),
        ({k: v for k, v in SITE_404.items() if k != "http_status"}, True),
        ({**BLOCKED, "resource_type": "Fetch"}, False),
    ],
)
def test_layout_stops_only_for_arsune_limits_on_rendering_resources(issue, expected):
    assert stops_layout(issue) is expected


async def test_page_error_cannot_be_rejected_from_a_truncated_list():
    observation = page(page_errors_truncated=True)
    result = await check("page_error", {"page": PAGE, "message": "Quiet page error"}, observation)
    assert result["status"] == "unverified"


async def test_confirmed_page_error_explains_impact_and_repair():
    result = await check(
        "page_error",
        {"page": PAGE, "message": "reading 'items'"},
        page(errors=[error(kind="rejection")]),
    )
    assert "uncaught promise rejection" in result["checker_reason"]
    assert "script" in result["visitor_impact"]
    assert "uncaught error" in result["suggested_correction"]


def test_scripted_proposes_only_provable_images_and_errors():
    other = SITE + "/shop/team"
    observations = [
        page(
            [
                image(),
                image(src=SITE + "/shop/img/soft.jpg", status=200, failure=None),
                image(src=SITE + "/shop/img/hidden.png", rendered=False),
                image(src=SITE + "/shop/img/ok.svg", status=200, failure=None, decoded=True),
                image(src=SITE + "/shop/img/lazy.svg", status=None, failure=None, decoded=None),
            ],
            [error(), error("Error: Widget", first_party=False), error("short")],
            [SITE_404],
        ),
        page(errors=[error("Error: Blocked fetch failed")], issues=[POLICY], url=other),
        page([image()], viewport=VIEWPORTS["mobile"]),
    ]
    found = scripted(observations)
    assert [(c["claim_type"], c["details"]) for c in found] == [
        ("broken_image", {"page": PAGE, "image": IMAGE}),
        ("broken_image", {"page": PAGE, "image": SITE + "/shop/img/soft.jpg"}),
        ("page_error", {"page": PAGE, "message": error()["message"]}),
    ]
    assert "HTTP 404" in found[0]["explanation"]
    assert "does not decode" in found[1]["explanation"]
    assert len(validate_candidates(found, [PAGE])[0]) == 3
    valid, dropped = validate_candidates([dict(found[2], urls=[other])], [other])
    assert valid == [] and dropped == ["names a page outside the checked pages"]


async def test_bounty_scope_for_page_faults(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "demo_url", SITE)
    site = origin(SITE)
    urls, details = await scoped_claim(
        site, "broken_image", [PAGE], {"page": PAGE + "#top", "image": IMAGE}
    )
    assert (urls, details) == ([PAGE], {"page": PAGE, "image": IMAGE})
    urls, details = await scoped_claim(
        site, "page_error", [PAGE], {"page": PAGE, "message": "  Cannot READ\n properties "}
    )
    assert details == {"page": PAGE, "message": "cannot read properties"}
    bad = [
        ("broken_image", [PAGE, IMAGE], {"page": PAGE, "image": IMAGE}, "exactly the page"),
        ("broken_image", [PAGE], {"page": PAGE, "image": IMAGE + "?v=2"}, "query"),
        ("broken_image", [PAGE], {"page": PAGE, "image": "http://127.0.0.1:8542/a.png"}, "not"),
        ("broken_image", [PAGE], {"page": PAGE}, "exactly: page, image"),
        ("page_error", [PAGE], {"page": PAGE, "message": " short "}, "8 to 500"),
        ("page_error", [PAGE], {"page": PAGE, "message": "x" * 501}, "8 to 500"),
        ("page_error", [PAGE], {"page": PAGE, "message": ["list"]}, "8 to 500"),
        ("page_error", [PAGE], {"page": PAGE, "message": "Long enough", "x": 1}, "exactly"),
    ]
    for claim_type, claim_urls, claim_details, match in bad:
        with pytest.raises((ClaimError, ValueError), match=match):
            await scoped_claim(site, claim_type, claim_urls, claim_details)


def test_final_fingerprint_names_the_failing_file_and_full_message():
    alias = SITE + "/shop/old-gallery"
    moved = SITE + "/shop/img/gallery/mobile-app.png"
    missing = SITE + "/shop/img/gallery/mobile-app-v2.png"
    evidence = [
        page([image(src=moved, final_url=missing)], [error()], url=alias),
    ]
    by_alias = final_fingerprint("broken_image", [alias], {"page": alias, "image": moved}, evidence)
    by_final_file = final_fingerprint(
        "broken_image", [alias], {"page": alias, "image": missing}, evidence
    )
    on_final_page = final_fingerprint(
        "broken_image", [PAGE], {"page": PAGE, "image": missing}, [page([image(src=missing)])]
    )
    assert by_alias == by_final_file == on_final_page
    assert by_alias == fingerprint("broken_image", [PAGE], {"page": PAGE, "image": missing})
    full = error()["message"].casefold()
    parts = [
        final_fingerprint(
            "page_error", [PAGE], {"page": PAGE, "message": text}, [page(errors=[error()])]
        )
        for text in ("cannot read properties", "(reading 'items')", full)
    ]
    assert parts[0] == parts[1] == parts[2]
    assert parts[0] == fingerprint("page_error", [PAGE], {"page": PAGE, "message": full})
    other = final_fingerprint(
        "page_error",
        [PAGE],
        {"page": PAGE, "message": "widget crashed"},
        [page(errors=[error("Error: Widget crashed")])],
    )
    assert other != parts[0]
