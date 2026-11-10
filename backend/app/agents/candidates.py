import hashlib
import json

from app.browser.checker import image_state, load_reproduced
from app.core.scan import ROLES, VIEWPORTS


def candidate(role, claim_type, urls, explanation, details, injected=False):
    return {
        "role": role,
        "claim_type": claim_type,
        "urls": urls,
        "explanation": explanation,
        "expected_behavior": "The page works at the stated size and shows matching facts.",
        "observed_behavior": explanation,
        "details": details,
        "injected": injected,
        "reproduction_steps": [f"Open {url}." for url in urls] + [explanation],
    }


def validate_candidates(items, selected):
    valid = []
    for item in items:
        if not isinstance(item, dict) or item.get("role") not in ROLES:
            raise ValueError("Invalid specialist role.")
        for key in ["claim_type", "explanation", "expected_behavior", "observed_behavior"]:
            if not isinstance(item.get(key), str) or not 1 <= len(item[key]) <= 2000:
                raise ValueError(f"Invalid candidate {key}.")
        urls = item.get("urls")
        if (
            not isinstance(urls, list)
            or not urls
            or len(urls) > 5
            or any(u not in selected for u in urls)
        ):
            raise ValueError("Candidate references a page outside the selected scope.")
        steps = item.get("reproduction_steps")
        if (
            not isinstance(steps, list)
            or not steps
            or len(steps) > 12
            or any(not isinstance(s, str) or len(s) > 2000 for s in steps)
        ):
            raise ValueError("Candidate needs bounded reproduction steps.")
        details = item.get("details", {})
        if not isinstance(details, dict):
            raise TypeError("Invalid candidate details.")
        for key in ["source", "target", "url", "page"]:
            if key in details and details[key] not in selected:
                raise ValueError("Candidate details reference an unselected page.")
        item["id"] = hashlib.sha256(
            json.dumps(
                [item["role"], item["claim_type"], sorted(urls), details],
                sort_keys=True,
            ).encode()
        ).hexdigest()[:20]
        if not any(c["id"] == item["id"] for c in valid):
            valid.append(item)
    return valid


def scripted(observations):
    result = []
    desktop = {o["url"]: o for o in observations if o["viewport"] == VIEWPORTS["desktop"]}
    for source, observation in desktop.items():
        for link in observation.get("links", []):
            target = desktop.get(link)
            if target and (target.get("status") or 0) >= 400:
                result.append(
                    candidate(
                        "navigation",
                        "broken_link",
                        [source, link],
                        f"The linked page returns HTTP {target['status']}.",
                        {"source": source, "target": link},
                    )
                )
    for url, observation in desktop.items():
        if not 200 <= (observation.get("status") or 0) < 300 or observation.get("error"):
            continue
        for image in observation.get("images", []):
            if image.get("rendered") and image_state(image) == "broken":
                failure = (
                    f"returns HTTP {image['status']}"
                    if image["status"] >= 400
                    else "does not decode as an image"
                )
                result.append(
                    candidate(
                        "navigation",
                        "broken_image",
                        [url],
                        f"The image {image['src'][:300]} on this page {failure}.",
                        {"page": url, "image": image["src"]},
                    )
                )
        if not load_reproduced(observation):
            continue
        for error in observation.get("page_errors", []):
            message = " ".join(error["message"].split())
            if error.get("first_party") and len(message) >= 8:
                result.append(
                    candidate(
                        "navigation",
                        "page_error",
                        [url],
                        f"Loading the page raises an uncaught error: {message}",
                        {"page": url, "message": message},
                    )
                )
    for observation in observations:
        if (
            observation["viewport"] == VIEWPORTS["mobile"]
            and observation.get("layout_complete", True)
            and observation.get("scroll_width", 0) > 392
        ):
            result.append(
                candidate(
                    "mobile",
                    "horizontal_overflow",
                    [observation["url"]],
                    "The page content is wider than the 390 pixel mobile viewport.",
                    {"url": observation["url"]},
                )
            )
    facts = {}
    for url, observation in desktop.items():
        for fact in observation.get("facts", []):
            facts.setdefault(fact["key"], []).append((url, fact["value"]))
    for key, values in facts.items():
        for index, (url, value) in enumerate(values):
            for other_url, other_value in values[index + 1 :]:
                if url != other_url and value != other_value:
                    result.append(
                        candidate(
                            "consistency",
                            "price_mismatch",
                            [url, other_url],
                            f"The same product, currency, and billing context show {value} and {other_value}.",
                            {"key": key},
                        )
                    )
    return result
