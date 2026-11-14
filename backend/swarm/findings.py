"""Turn what an agent saw into claims in Arsune's five provable shapes.

`broken_link` names {source, target}, `horizontal_overflow` names {url}, `price_mismatch`
names {key} for exactly two pages, `broken_image` names {page, image}, and `page_error` names
{page, message}. The fault key is Arsune's published fingerprint, so an agent can tell when a
fault is already proven on the public bounty page.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from itertools import combinations
from urllib.parse import urlsplit

from swarm.site import Page, looks_wide

OVERFLOW_TOLERANCE = 392  # the checker confirms a mobile page wider than this
LABELS = {
    "broken_link": "broken link",
    "horizontal_overflow": "mobile overflow",
    "price_mismatch": "price mismatch",
    "broken_image": "broken image",
    "page_error": "page error",
    "access_leak": "access leak",
    "data_exposure": "personal data open to anyone",
    "secret_key": "secret key in public code",
    "private_file": "private file open to anyone",
}
SUSPICIOUS_LINK = re.compile(
    r"old|legacy|retired|archive|previous|gift|report|missing|deprecated|beta", re.IGNORECASE
)


def fingerprint(claim_type: str, urls: list[str], details: dict) -> str:
    value = json.dumps([claim_type, sorted(urls), details], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def path(url: str) -> str:
    return urlsplit(url).path or "/"


def claim_label(claim_type: str) -> str:
    """A readable name for a claim type, also for types this swarm does not know yet."""
    return LABELS.get(claim_type) or str(claim_type).replace("_", " ")


def normalize_message(message: str) -> str:
    """Arsune stores a page error message trimmed, with whitespace collapsed, case folded."""
    return " ".join(message.split()).casefold()


@dataclass
class Finding:
    claim_type: str
    urls: list[str]
    details: dict
    explanation: str
    steps: list[str]
    evidence: str
    guessed: bool = False
    key: str = field(init=False)

    def __post_init__(self):
        self.key = fingerprint(self.claim_type, self.urls, self.details)

    @property
    def label(self) -> str:
        return claim_label(self.claim_type)

    @property
    def where(self) -> str:
        return " and ".join(path(u) for u in self.urls)

    def body(self) -> dict:
        return {
            "claim_type": self.claim_type,
            "urls": self.urls,
            "details": self.details,
            "explanation": self.explanation[:2000],
            "reproduction_steps": [s[:2000] for s in self.steps[:12]],
        }


def broken_link(source: str, target: str, status: int) -> Finding:
    return Finding(
        "broken_link",
        [source, target],
        {"source": source, "target": target},
        f"The link from {path(source)} to {path(target)} returns HTTP {status}.",
        [f"Open {source}.", f"Follow the link to {target}.", f"The server answers HTTP {status}."],
        f"GET {path(target)} returned HTTP {status}; linked from {path(source)}",
    )


def broken_image(page: str, image: str, status: int | None) -> Finding:
    if status and status >= 400:
        answer = f"its request answers HTTP {status}"
    else:  # an HTTP 2xx answer that is not a picture the browser can decode
        answer = "the browser cannot show the file it gets"
    return Finding(
        "broken_image",
        [page],
        {"page": page, "image": image},
        f"The image {path(image)} on {path(page)} does not load: {answer}.",
        [f"Open {page} at a 1280 pixel wide viewport.", f"The image {image} shows no picture."],
        f"image {path(image)} on {path(page)}: {answer}",
    )


def page_error(page: str, message: str) -> Finding:
    """`message` is the full first line of the error, so the key is the fault's final key."""
    text = normalize_message(message)
    return Finding(
        "page_error",
        [page],
        {"page": page, "message": text},
        f"Loading {path(page)} raises an uncaught error from the site's own script: {message}",
        [f"Open {page} at a 1280 pixel wide viewport.", f"The page raises: {message}"],
        f'{path(page)} raises "{message[:80]}"',
    )


def broken_links(page: Page, statuses: dict[str, int | None]) -> list[Finding]:
    if not page.ok:
        return []
    return [
        broken_link(page.url, target, statuses[target])
        for target in page.links
        if target != page.url and (statuses.get(target) or 0) >= 400
    ]


def overflow(url: str, width: int | None) -> Finding | None:
    if width is None or width <= OVERFLOW_TOLERANCE:
        return None
    return Finding(
        "horizontal_overflow",
        [url],
        {"url": url},
        f"At a 390 pixel wide phone viewport the content is {width} pixels wide, "
        "so the page scrolls sideways.",
        [
            f"Open {url} in a 390 x 844 viewport.",
            f"document.documentElement.scrollWidth is {width}.",
        ],
        f"content {width} px wide at a 390 px viewport",
    )


def fact_label(fact: dict) -> str:
    context = fact.get("context")
    if context:
        return f"{context['name']} ({context['currency']} per {context['period']})"
    return fact["key"]


def price_mismatches(facts_by_page: dict[str, list[dict]]) -> list[Finding]:
    """Pairs of pages that each show one value for a fact key, and the values differ."""
    single: dict[str, list[tuple[str, dict]]] = {}
    for url, facts in facts_by_page.items():
        for key in {f["key"] for f in facts}:
            matches = [f for f in facts if f["key"] == key]
            if len(matches) == 1:
                single.setdefault(key, []).append((url, matches[0]))
    found = []
    for key, entries in single.items():
        for (url_a, a), (url_b, b) in combinations(entries, 2):
            if a["value"] != b["value"]:
                found.append(
                    Finding(
                        "price_mismatch",
                        [url_a, url_b],
                        {"key": key},
                        f"{fact_label(a)} costs {a['value']} on {path(url_a)} "
                        f"and {b['value']} on {path(url_b)}.",
                        [
                            f"Open {url_a} and read the {fact_label(a)} price: {a['value']}.",
                            f"Open {url_b} and read the same offer: {b['value']}.",
                        ],
                        f"{fact_label(a)}: {a['value']} vs {b['value']}",
                    )
                )
    return found


def guess(pages: list[Page], rng) -> Finding | None:
    """A careless claim from a quick look at the markup, without any measurement."""
    ok = [p for p in pages if p.ok]
    if not ok:
        return None
    if rng.random() < 0.5:
        wide = [p for p in ok if looks_wide(p.html)]
        page = rng.choice(wide or ok)
        found = overflow(page.url, 640)
        found.explanation = "The page looks too wide for a phone."
        found.steps = [f"Open {page.url} on a phone.", "Scroll sideways."]
        found.evidence = "a fixed width in the page markup"
    else:
        links = [(p, t) for p in ok for t in p.links if t != p.url]
        if not links:
            return None
        odd = [(p, t) for p, t in links if SUSPICIOUS_LINK.search(t + " " + p.link_text.get(t, ""))]
        page, target = rng.choice(odd or links)
        found = broken_link(page.url, target, 404)
        found.explanation = f"The link to {path(target)} looks dead."
        found.steps = [f"Open {page.url}.", f"Click the link to {target}."]
        found.evidence = f"the link text or address of {path(target)} looks outdated"
    found.guessed = True
    return found


def copy_of(claim: dict) -> Finding:
    """A re-submission of a claim that another hunter already proved."""
    return Finding(
        claim["claim_type"],
        list(claim["urls"]),
        dict(claim["details"]),
        "Reported fault: " + claim_label(claim["claim_type"]) + ".",
        [f"Open {url}." for url in claim["urls"]],
        "copied from the public bounty page",
    )
