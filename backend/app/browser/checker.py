from urllib.parse import urlsplit

from app.browser.policy import origin
from app.core.scan import VIEWPORTS


def image_state(image):
    """Return broken, loads, or unknown for one image from a fresh page read."""
    status = image.get("status")
    if status is not None and status >= 400:
        return "broken"
    if image.get("decoded") is True:
        return "loads"
    if (
        image.get("decoded") is False
        and status
        and 200 <= status < 300
        and not image.get("failure")
    ):
        return "broken"
    return "unknown"


def image_key(url):
    """Name an image file by origin and path; the query string and fragment do not count."""
    try:
        return origin(url) + (urlsplit(url).path or "/")
    except (TypeError, ValueError):
        return None


def matching_images(observation, image_url):
    key = image_key(image_url)
    return [
        image
        for image in observation.get("images", [])
        if key and key in {image_key(image["src"]), image_key(image["final_url"])}
    ]


def failing_images(observation, image_url):
    """Images that match the claimed URL, fail, and are shown to visitors."""
    return [
        image
        for image in matching_images(observation, image_url)
        if image.get("rendered") and image_state(image) == "broken"
    ]


def normalize_message(message):
    return " ".join(message.split()).casefold() if isinstance(message, str) else ""


def matching_errors(observation, message, first_party=True):
    """Uncaught errors whose message contains the claimed text of at least 8 characters."""
    wanted = normalize_message(message)
    if len(wanted) < 8:
        return []
    return [
        error
        for error in observation.get("page_errors", [])
        if bool(error.get("first_party")) == first_party
        and wanted in normalize_message(error.get("message"))
    ]


def load_reproduced(observation):
    """Arsune's own limits blocked nothing that a visitor's browser would load."""
    return not any(
        issue["resource_type"] != "Readiness" and not issue.get("http_status")
        for issue in observation.get("resource_issues", [])
    )


def load_complete(observation):
    """Every resource loaded, except images that the site answered with an HTTP error."""
    return not any(
        issue["resource_type"] != "Image" or not issue.get("http_status")
        for issue in observation.get("resource_issues", [])
    )


def check_broken_image(page, image_url):
    images = matching_images(page, image_url)
    states = [image_state(image) for image in images]
    failing = failing_images(page, image_url)
    if failing:
        image = failing[0]
        if image["status"] >= 400:
            return "confirmed", (
                f"A fresh page read found the image. Its request returned HTTP {image['status']}."
            )
        return "confirmed", (
            f"A fresh page read found the image. The server returned HTTP {image['status']}, "
            "but the browser could not decode the image."
        )
    if images and all(state == "loads" for state in states):
        return "rejected", "A fresh page read found the image, and the browser loaded it."
    if "broken" in states:
        return "unverified", "The image fails, but the page does not show it to visitors."
    if images:
        return "unverified", "The image did not finish loading, or Arsune's limits blocked it."
    return "unverified", "A fresh page read did not find this image on the page."


def check_page_error(page, message):
    if len(normalize_message(message)) < 8:
        return "unverified", "The claimed error message needs at least 8 characters."
    matched = matching_errors(page, message)
    if matched and load_reproduced(page):
        error = matched[0]
        kind = "promise rejection" if error["kind"] == "rejection" else "error"
        return "confirmed", f"A fresh page load raised an uncaught {kind}: {error['message']}"
    if matched:
        return "unverified", (
            "A matching error occurred, but Arsune's limits blocked resources that a "
            "visitor's browser would load."
        )
    if matching_errors(page, message, first_party=False):
        return "unverified", (
            "The matching error comes from a script on another origin, which this check "
            "does not cover."
        )
    if any(error.get("first_party") for error in page.get("page_errors", [])):
        return "unverified", "The fresh page load raised a different uncaught error."
    if load_complete(page) and not page.get("page_errors_truncated"):
        return "rejected", "A complete fresh page load raised no uncaught error from the site."
    return "unverified", "The page did not load completely, so the error cannot be ruled out."


async def check_candidate(item, observe):
    fresh = [
        await observe(
            u,
            VIEWPORTS["mobile"]
            if item["claim_type"] == "horizontal_overflow"
            else VIEWPORTS["desktop"],
        )
        for u in item["urls"]
    ]
    status, reason = (
        "unverified",
        "The supplied claim could not be checked with the supported measurements.",
    )
    details = item.get("details", {})
    if all(o.get("status") is not None for o in fresh):
        if item["claim_type"] == "broken_link":
            source = next((o for o in fresh if o["url"] == details.get("source")), None)
            target = next((o for o in fresh if o["url"] == details.get("target")), None)
            if (
                source
                and target
                and 200 <= source["status"] < 300
                and source.get("layout_complete", True)
                and target["url"] in source["links"]
            ):
                status = (
                    "confirmed"
                    if target["status"] >= 400
                    else "rejected"
                    if 200 <= target["status"] < 300 and target.get("layout_complete", True)
                    else "unverified"
                )
                reason = f"A fresh source-page read found the link. A fresh target request returned HTTP {target['status']}."
        elif item["claim_type"] == "horizontal_overflow":
            if 200 <= fresh[0]["status"] < 300 and fresh[0].get("layout_complete", True):
                width = fresh[0].get("scroll_width", 0)
                status = "confirmed" if width > 392 else "rejected"
                reason = (
                    f"Fresh measurement: content width {width} pixels; viewport width 390 pixels."
                )
        elif (
            item["claim_type"] == "price_mismatch"
            and len(fresh) == 2
            and all(200 <= o["status"] < 300 and o.get("layout_complete", True) for o in fresh)
        ):
            values = [
                [f["value"] for f in o["facts"] if f["key"] == details.get("key")] for o in fresh
            ]
            facts = [[f for f in o["facts"] if f["key"] == details.get("key")] for o in fresh]
            ordinary_complete = not any(
                f.get("source") == "visible_price"
                and not all(
                    f.get(field) for field in ("context", "quote", "selector", "screenshot")
                )
                for group in facts
                for f in group
            )
            same_context = len(facts[0]) == len(facts[1]) == 1 and facts[0][0].get(
                "context"
            ) == facts[1][0].get("context")
            if all(len(v) == 1 for v in values) and ordinary_complete and same_context:
                status = "confirmed" if values[0] != values[1] else "rejected"
                fact = next(f for f in fresh[0]["facts"] if f["key"] == details["key"])
                context = fact.get("context")
                label = (
                    f"{context['name']}, {context['currency']}, per {context['period']}, unit {context['unit']}"
                    if context
                    else details["key"]
                )
                reason = f"Fresh page reads for {label}: {values[0][0]} and {values[1][0]}."
        elif item["claim_type"] in {"broken_image", "page_error"}:
            page = next((o for o in fresh if o["url"] == details.get("page")), None)
            if page and 200 <= page["status"] < 300 and not page.get("error"):
                if item["claim_type"] == "broken_image":
                    status, reason = check_broken_image(page, details.get("image"))
                else:
                    status, reason = check_page_error(page, details.get("message"))
    if item["claim_type"] == "horizontal_overflow" and any(
        not o.get("layout_complete", True) for o in fresh
    ):
        status, reason = (
            "unverified",
            "Required page resources were unavailable or did not become ready.",
        )
    result = {
        **item,
        "status": status,
        "checker_reason": reason,
        "evidence": fresh,
        "visitor_impact": {
            "broken_image": "Visitors see an empty space or a broken image instead of the picture.",
            "page_error": "A script on this page stops with an error, so parts of the page can "
            "fail for visitors.",
        }.get(
            item["claim_type"],
            "Visitors can have difficulty using this page or choosing the stated offer.",
        ),
        "suggested_correction": {
            "broken_link": "Repair the link or restore the destination page.",
            "horizontal_overflow": "Make the content fit the mobile viewport.",
            "price_mismatch": "Use the same value for the same product and billing context.",
            "broken_image": "Restore the image file or correct the image address.",
            "page_error": "Correct the script so that the page loads without an uncaught error.",
        }.get(item["claim_type"], "Review the supplied reproduction steps."),
    }
    if status == "rejected":
        result["visitor_impact"] = "The fresh check did not reproduce this claimed fault."
        result["suggested_correction"] = "No repair is needed for this tested claim."
    elif status == "unverified":
        result["visitor_impact"] = "The effect on visitors is not known."
        result["suggested_correction"] = "Collect more evidence before deciding on a repair."
    return result
