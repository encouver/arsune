"""Northwind Supply: a deterministic demo shop with seeded functional faults.

Pages are generated from the tables below. GROUND_TRUTH lists the seeded faults
and the look-alike decoys for tests; no HTTP route exposes it.
"""

import hashlib
import json
from html import escape

from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

SHOP = "/shop"

# slug, name, billing unit
CATEGORIES = [
    ("hosting", "Hosting", "account"),
    ("analytics", "Analytics", "seat"),
    ("storage", "Storage", "account"),
    ("email", "Email", "user"),
    ("support", "Support", "seat"),
    ("security", "Security", "license"),
    ("backup", "Backup", "account"),
    ("payments", "Payments", "account"),
]
LEVELS = ["Basic", "Plus", "Scale"]
INFO = {
    "about": "About Northwind",
    "shipping": "Delivery and activation",
    "contact": "Contact us",
    "faq": "Frequently asked questions",
    "pricing": "Pricing overview",
    "returns": "Cancellations and refunds",
}
CAT = {slug: (name, unit) for slug, name, unit in CATEGORIES}
# Pages with pictures, linked from the home page.
MEDIA = {
    "gallery": ("Product gallery", "Screens from each product, updated with every release."),
    "team": ("Our team", "The people who build and support Northwind Supply."),
}


def _build_plans():
    plans = []
    for ci, (cat, cat_name, unit) in enumerate(CATEGORIES):
        levels = LEVELS + (["Global"] if cat == "payments" else [])
        for li, level in enumerate(levels):
            plans.append(
                {
                    "slug": f"{cat}-{level.lower()}",
                    "cat": cat,
                    "name": f"{cat_name} {level}",
                    "unit": unit,
                    "price": 9 + ci * 2 + li * 10,
                }
            )
    return plans


PLANS = _build_plans()
PLAN = {p["slug"]: p for p in PLANS}
CAT_PLANS = {slug: [p for p in PLANS if p["cat"] == slug] for slug, _, _ in CATEGORIES}


def price_key(name, unit, period="month", currency="USD"):
    """The key the checker derives for a plain visible price card."""
    context = {
        "name": " ".join(name.split()).casefold(),
        "product_context": [],
        "currency": currency,
        "period": period,
        "unit": unit,
        "qualifiers": [],
    }
    digest = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    return "visible-price|" + digest[:24]


def cat_path(slug):
    return f"{SHOP}/c/{slug}"


def plan_path(slug):
    return f"{SHOP}/p/{slug}"


def info_path(slug):
    return f"{SHOP}/{slug}"


# ---------------------------------------------------------------- seeded faults

_PLAN_PATHS = [plan_path(p["slug"]) for p in PLANS]
_CAT_PATHS = [cat_path(c[0]) for c in CATEGORIES]
_INFO_PATHS = {slug: info_path(slug) for slug in INFO}

# (source page, target, HTTP status, link text)
BROKEN_LINKS = []
_targets = [("retired", 404)] * 12 + [("archive", 410)] * 4 + [("reports", 500)] * 4
_sources = (
    [_PLAN_PATHS[i * 2] for i in range(12)]
    + [_CAT_PATHS[i] for i in (1, 3, 5, 7)]
    + [_INFO_PATHS[s] for s in ("about", "shipping", "faq", "contact")]
)
_TEXTS = [
    "Migration guide",
    "Service status history",
    "Compliance report",
    "Setup checklist",
    "Release notes",
    "Capacity planner",
    "Security whitepaper",
    "Partner directory",
    "Customer stories",
    "Integration catalog",
]
for _i, (_src, (_prefix, _status)) in enumerate(zip(_sources, _targets, strict=True)):
    BROKEN_LINKS.append(
        (_src, f"{SHOP}/{_prefix}/item-{_i + 1:02d}", _status, _TEXTS[_i % len(_TEXTS)])
    )

_OVERFLOW_PAGES = (
    [_PLAN_PATHS[i] for i in range(0, 25, 3)]
    + [_CAT_PATHS[i] for i in (0, 2, 4)]
    + [_INFO_PATHS[s] for s in ("contact", "returns", "pricing")]
)

# Plans whose listing price and detail price differ.
_MISMATCH = [i for i in range(1, 23) if i % 3 != 0]
PRICE_MISMATCH_PLANS = {PLANS[i]["slug"]: PLANS[i]["price"] + 10 for i in _MISMATCH}


def _overflow_block(variant):
    if variant == 0:
        return '<div class="banner" style="width:640px">Spring sale ends soon. Order today.</div>'
    if variant == 1:
        return (
            '<table style="width:720px"><tr><th>Feature</th><th>Basic</th><th>Plus</th>'
            "<th>Scale</th></tr><tr><td>Support hours</td><td>Business</td><td>Extended</td>"
            "<td>Round the clock</td></tr></table>"
        )
    if variant == 2:
        return (
            '<pre style="white-space:pre">service  region  status  latency  uptime  incidents'
            "  owner  notes  window</pre>"
        )
    if variant == 3:
        return (
            '<div style="display:flex;min-width:560px"><div style="flex:1">Plan</div>'
            '<div style="flex:1">Limits</div><div style="flex:1">Add-ons</div></div>'
        )
    return '<svg width="800" height="40"><rect width="800" height="40" fill="#cde"/></svg>'


# --------------------------------------------------------------------- decoys

# Extra page content for decoys and seeded faults, keyed by page path.
_EXTRA_LINKS = {}
_EXTRA_HTML = {}
_DETAIL_CARDS = {}  # plan slug -> html that replaces the plain detail card
_LISTING_CARDS = {}  # plan slug -> card text replacing the plain listing text
_DETAIL_EXTRA = {}  # plan slug -> extra html after the detail card


def _link(path, href, text):
    _EXTRA_LINKS.setdefault(path, []).append((href, text))


for _src, _tgt, _st, _txt in BROKEN_LINKS:
    _link(_src, _tgt, _txt)
for _i, _path in enumerate(_OVERFLOW_PAGES):
    _EXTRA_HTML.setdefault(_path, []).append(_overflow_block(_i % 5))

# Decoy page layouts (no page overflow at 390 pixels)
_EXTRA_HTML.setdefault(info_path("shipping"), []).append(
    '<div style="overflow-x:auto"><table style="width:900px"><tr><th>Service</th><th>Standard</th>'
    "<th>Priority</th><th>Dedicated</th><th>Notes</th></tr><tr><td>Activation</td>"
    "<td>Same day</td><td>Within an hour</td><td>Immediate</td><td>By arrangement</td></tr></table></div>"
)
_EXTRA_HTML.setdefault(info_path("about"), []).append(
    "<style>@media (min-width:800px){.wide{width:1100px}}</style>"
    '<div class="wide banner">Visit our offices in person.</div>'
)
_EXTRA_HTML.setdefault(cat_path("email"), []).append(
    '<div class="banner" style="width:380px">Compact notice that fits a phone.</div>'
)
_EXTRA_HTML.setdefault(cat_path("backup"), []).append(
    '<p style="overflow-wrap:anywhere">Reference code '
    "ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ</p>"
)
_EXTRA_HTML.setdefault(cat_path("security"), []).append(
    '<div class="banner" style="width:1200px;max-width:100%;overflow:hidden;white-space:nowrap">'
    "A very long announcement that is clipped inside its own box and never widens the page, "
    "even on narrow screens.</div>"
)

# Decoy links
_link(info_path("about"), f"{SHOP}/old-pricing", "Our previous pricing page")
_link(info_path("faq"), f"{SHOP}/old-shipping", "Previous delivery details")
_link(cat_path("support"), plan_path("legacy-gift-card"), "Gift cards")
_link(info_path("pricing"), f"{SHOP}/files/price-list.txt", "Download the price list")
_link(info_path("faq"), f"{info_path('faq')}#billing", "Jump to billing questions")
_EXTRA_HTML.setdefault(info_path("faq"), []).append('<p id="billing">Billing questions.</p>')

# Decoy prices on plans that have no seeded price fault
_p = PLANS[3]  # equal value written with decimals
_DETAIL_CARDS[_p["slug"]] = f"USD {_p['price']}.00 per month, per {_p['unit']}"
_p = PLANS[9]  # extra yearly card with another name and period
_DETAIL_EXTRA[PLANS[9]["slug"]] = (
    f'<section class="plan"><h2>{PLANS[9]["name"]} Yearly</h2>'
    f"<p>USD {PLANS[9]['price'] * 10} per year, per {PLANS[9]['unit']}</p></section>"
)
_p = PLANS[12]  # struck-through earlier price makes the card unreadable by design
_DETAIL_CARDS[_p["slug"]] = (
    f"<s>USD {_p['price'] + 10}</s> USD {_p['price']} per month, per {_p['unit']}"
)
_p = PLANS[15]  # starting-from price on the listing
_LISTING_CARDS[_p["slug"]] = f"From USD {_p['price'] - 4} per month, per {_p['unit']}"
_p = PLANS[18]  # separate named card for another audience
_DETAIL_EXTRA[PLANS[18]["slug"]] = (
    f'<section class="plan"><h2>Education {PLANS[18]["name"]}</h2>'
    f"<p>USD {PLANS[18]['price'] - 5} per month, per {PLANS[18]['unit']}</p></section>"
)
_p = PLANS[21]  # discounted card
_DETAIL_CARDS[_p["slug"]] = f"USD {_p['price'] - 3} per month, per {_p['unit']} with a 20% discount"


# ------------------------------------------------------- images and page errors

IMG = f"{SHOP}/img"
# Image files that load, by name under IMG, with the label drawn on the picture.
_PICTURES = {
    "logo.svg": "Northwind Supply",
    "gallery/billing.svg": "Billing overview",
    "gallery/archive.svg": "Earlier release",
    "team/engineer.svg": "Platform team",
}
_IMAGE_REDIRECTS = {
    "old-logo.png": "logo.svg",
    "gallery/mobile-app.png": "gallery/mobile-app-v2.png",
}
# HTTP 200 answers that a browser cannot decode as an image.
_UNDECODABLE = {
    "storage-hero.jpg": ("text/html", b"<html><body>Image not found</body></html>"),
    "analytics-plus-chart.png": ("image/png", b"\x89PNG\r\n\x1a\nnot an image"),
    "refund-steps.webp": ("image/webp", b""),
}
# Other image names answer 404 unless listed here.
_IMAGE_STATUS = {"gallery/reports.png": 410, "team/ceo.jpg": 403, "team/support-lead.jpg": 500}

# (page, image name, HTTP status of the final answer, alt text). An image that answers an
# HTTP error makes its page's layout incomplete, so those sit on pages without layout faults.
BROKEN_IMAGES = [
    (f"{SHOP}/", "partner-badge.png", 404, "Partner of the year badge"),
    (plan_path("payments-scale"), "payments-scale-flow.png", 404, "Payment flow diagram"),
    (info_path("gallery"), "gallery/dashboard.png", 404, "Hosting dashboard"),
    (info_path("gallery"), "gallery/reports.png", 410, "Analytics reports"),
    (info_path("gallery"), "gallery/mobile-app.png", 404, "Mobile app"),
    (info_path("team"), "team/ceo.jpg", 403, "Dana Whitfield, chief executive"),
    (info_path("team"), "team/support-lead.jpg", 500, "Ravi Menon, support lead"),
    (cat_path("storage"), "storage-hero.jpg", 200, "Storage racks"),
    (plan_path("analytics-plus"), "analytics-plus-chart.png", 200, "Usage chart"),
    (info_path("returns"), "refund-steps.webp", 200, "Refund steps"),
]

FAQ_SEARCH_JS = (
    "document.getElementById('faq-search').addEventListener('input', event => {\n"
    "  document.body.dataset.query = event.target.value;\n});\n"
)
# (page, script, the part of the error message that a claim quotes)
PAGE_ERRORS = [
    (
        cat_path("hosting"),
        "<script>(() => { const cart = window.northwindCart; "
        + "document.title += ' (' + cart.items.length + ')'; })()</script>",
        "Cannot read properties of undefined (reading 'items')",
    ),
    (
        plan_path("security-plus"),
        "<script>initPlanCarousel(document.querySelector('.plan'))</script>",
        "initPlanCarousel is not defined",
    ),
    (
        info_path("faq"),
        f'<script src="{SHOP}/assets/faq-search.js"></script>',
        "Cannot read properties of null (reading 'addEventListener')",
    ),
    (
        plan_path("email-basic"),
        "<script>(async () => { "
        + "throw new Error('Recommendation feed returned no plans') })()</script>",
        "Recommendation feed returned no plans",
    ),
    (
        info_path("gallery"),
        "<script>addEventListener('load', () => { "
        + "throw new Error('Gallery lightbox could not find its images') })</script>",
        "Gallery lightbox could not find its images",
    ),
    (
        info_path("contact"),
        "<script>setTimeout(() => { throw 'Contact form widget failed to start' })</script>",
        "Contact form widget failed to start",
    ),
]


def _figure(name, alt, attrs=""):
    return (
        f'<figure><img src="{IMG}/{name}" alt="{escape(alt)}" width="240" height="150"{attrs}>'
        f"<figcaption>{escape(alt)}</figcaption></figure>"
    )


for _slug in MEDIA:
    _link(f"{SHOP}/", info_path(_slug), MEDIA[_slug][0])
_EXTRA_HTML.setdefault(info_path("gallery"), []).append(
    _figure("gallery/billing.svg", "Billing overview")
)
_EXTRA_HTML.setdefault(info_path("team"), []).append(_figure("team/engineer.svg", "Platform team"))
for _page, _name, _status, _alt in BROKEN_IMAGES:
    _EXTRA_HTML.setdefault(_page, []).append(_figure(_name, _alt))
for _page, _script, _message in PAGE_ERRORS:
    _EXTRA_HTML.setdefault(_page, []).append(_script)

# Decoy images: never requested, hidden, redirected to a file that loads, lazy and not yet loaded
_EXTRA_HTML[cat_path("hosting")].append(
    f'<noscript><img src="{IMG}/noscript-banner.png" alt="Turn on JavaScript to compare plans"'
    ' width="240" height="60"></noscript>'
)
_EXTRA_HTML[info_path("gallery")].append(
    f'<img src="{IMG}/gallery/old-banner.png" alt="" width="240" height="60" style="display:none">'
)
_EXTRA_HTML[f"{SHOP}/"].append(_figure("old-logo.png", "Northwind Supply logo"))
# Chromium loads a lazy image only within about 3000 pixels of the viewport.
_EXTRA_HTML[info_path("gallery")].append(
    '<div style="min-height:6000px">Photo archive. Earlier release screens follow.</div>'
    + _figure("gallery/archive.svg", "Earlier release", ' loading="lazy"')
)

# Decoy errors: console output, a caught error, another origin's script, a late handler
_EXTRA_HTML.setdefault(cat_path("analytics"), []).append(
    "<script>console.error('Newsletter signup is not configured')</script>"
)
_EXTRA_HTML.setdefault(plan_path("backup-basic"), []).append(
    "<script>try { throw new Error('Saved cart could not be restored') }"
    " catch (error) { console.warn(error.message) }</script>"
)
_EXTRA_HTML.setdefault(plan_path("storage-plus"), []).append(
    "<script>(function startChat() { throw new Error('Chat widget failed to start') })()\n"
    "//# sourceURL=https://widgets.partner-chat.example/loader.js\n</script>"
)
_EXTRA_HTML.setdefault(info_path("about"), []).append(
    "<script>(() => { const points = Promise.reject("
    "new Error('Loyalty points are temporarily unavailable'));"
    " setTimeout(() => points.catch(() => {})); })()</script>"
)


def _svg(label):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="150" viewBox="0 0 240 150">'
        '<rect width="240" height="150" fill="#cde3f5"/><text x="16" y="82"'
        f' font-family="sans-serif" font-size="18" fill="#12355b">{escape(label)}</text></svg>'
    )


def image_response(name):
    if name in _PICTURES:
        return Response(_svg(_PICTURES[name]), media_type="image/svg+xml")
    if name in _IMAGE_REDIRECTS:
        return RedirectResponse(f"{IMG}/{_IMAGE_REDIRECTS[name]}", status_code=301)
    if name in _UNDECODABLE:
        media_type, body = _UNDECODABLE[name]
        return Response(body, media_type=media_type)
    status = _IMAGE_STATUS.get(name, 404)
    return Response(f"Image unavailable ({status})", status_code=status)


def _listing_value(plan):
    return f"USD {plan['price']} per month, per {plan['unit']}"


def _detail_text(plan):
    if plan["slug"] in _DETAIL_CARDS:
        return _DETAIL_CARDS[plan["slug"]]
    price = PRICE_MISMATCH_PLANS.get(plan["slug"], plan["price"])
    return f"USD {price} per month, per {plan['unit']}"


def _key(plan):
    return price_key(plan["name"], plan["unit"])


# ------------------------------------------------------------------ rendering

CSS = """
*{box-sizing:border-box}
body{font:16px/1.5 system-ui,sans-serif;margin:0;color:#222;background:#fafafa}
.wrap{max-width:960px;margin:0 auto;padding:0 12px}
header{background:#12355b;color:#fff;padding:10px 0}
header a{color:#fff;text-decoration:none;margin-right:12px;display:inline-block}
.brand{font-weight:700;font-size:20px}
nav{display:flex;flex-wrap:wrap;gap:4px 8px}
footer{border-top:1px solid #ccc;margin-top:32px;padding:16px 0;font-size:14px}
footer a{margin-right:12px;display:inline-block}
.h1{font-size:28px;font-weight:700;margin:20px 0 8px}
.plan{border:1px solid #bbb;border-radius:6px;padding:8px 12px;margin:8px 0;max-width:340px;background:#fff}
.plan h2{margin:0 0 4px;font-size:18px}
.plan p{margin:0}
.banner{background:#ffe9a8;padding:10px;margin:12px 0}
table{border-collapse:collapse}
td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}
"""

COPY = {
    "about": "Northwind Supply has provided business software subscriptions since 2009.",
    "shipping": "Every plan activates by email. Dedicated plans include a setup call.",
    "contact": "Write to hello@northwind.example or call our desk on weekdays.",
    "faq": "Answers to common questions about plans, invoices and upgrades.",
    "pricing": "Browse every plan family below. Each plan page shows its monthly rate.",
    "returns": "Cancel at any time. Refunds are issued to the original payment method.",
}


def _layout(title, body, heading_tag="h1"):
    nav = "".join(f'<a href="{cat_path(s)}">{n}</a>' for s, n, _ in CATEGORIES)
    foot = "".join(f'<a href="{info_path(s)}">{t}</a>' for s, t in INFO.items())
    heading = (
        f'<h1 class="h1">{escape(title)}</h1>'
        if heading_tag == "h1"
        else f'<div class="h1">{escape(title)}</div>'
    )
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)} - Northwind Supply</title><style>{CSS}</style></head><body>"
        f'<header><div class="wrap"><a class="brand" href="{SHOP}/">Northwind Supply</a>'
        f"<nav>{nav}</nav></div></header>"
        f'<main class="wrap">{heading}{body}</main>'
        f'<footer><div class="wrap">{foot}</div></footer></body></html>'
    )


def _extras(path):
    html = "".join(_EXTRA_HTML.get(path, []))
    links = _EXTRA_LINKS.get(path, [])
    if links:
        items = "".join(f'<li><a href="{h}">{escape(t)}</a></li>' for h, t in links)
        html += f"<p><strong>Related resources</strong></p><ul>{items}</ul>"
    return html


def _card(name, text, href=None):
    title = f'<a href="{href}">{escape(name)}</a>' if href else escape(name)
    return f'<section class="plan"><h2>{title}</h2><p>{text}</p></section>'


def render_home():
    cats = "".join(f'<li><a href="{cat_path(s)}">{n}</a></li>' for s, n, _ in CATEGORIES)
    featured = "".join(
        f'<li><a href="{plan_path(slug)}">{PLAN[slug]["name"]}</a></li>'
        for slug in ("hosting-basic", "analytics-plus", "security-scale")
    )
    body = (
        "<p>Subscriptions that keep a growing business running.</p>"
        f"<h2>Shop by category</h2><ul>{cats}</ul><h2>Popular plans</h2><ul>{featured}</ul>"
    )
    return _layout("Northwind Supply", body + _extras(f"{SHOP}/"))


def render_category(slug):
    name, _ = CAT[slug]
    cards = ""
    for plan in CAT_PLANS[slug]:
        text = _LISTING_CARDS.get(plan["slug"], _listing_value(plan))
        cards += _card(plan["name"], text, plan_path(plan["slug"]))
    body = f"<p>Compare {name.lower()} plans and pick the one that fits.</p>{cards}"
    return _layout(f"{name} plans", body + _extras(cat_path(slug)), "div")


def render_plan(slug):
    plan = PLAN[slug]
    name = CAT[plan["cat"]][0]
    body = (
        f'<p><a href="{cat_path(plan["cat"])}">All {name.lower()} plans</a></p>'
        + _card(plan["name"], _detail_text(plan))
        + _DETAIL_EXTRA.get(slug, "")
        + f"<p>{plan['name']} includes onboarding help and email support.</p>"
    )
    return _layout(plan["name"], body + _extras(plan_path(slug)), "div")


def render_info(slug):
    body = f"<p>{COPY[slug]}</p>"
    if slug == "pricing":
        body += "".join(f'<p><a href="{cat_path(s)}">{n} plans</a></p>' for s, n, _ in CATEGORIES)
    return _layout(INFO[slug], body + _extras(info_path(slug)))


def render_media(slug):
    title, copy = MEDIA[slug]
    return _layout(title, f"<p>{copy}</p>" + _extras(info_path(slug)))


def shop_response(path):
    """Serve one /shop/ path."""
    if path == "":
        return render_home()
    if path.startswith("c/") and path[2:] in CAT:
        return render_category(path[2:])
    if path.startswith("p/") and path[2:] in PLAN:
        return render_plan(path[2:])
    if path in INFO:
        return render_info(path)
    if path in MEDIA:
        return render_media(path)
    if path.startswith("img/"):
        return image_response(path[4:])
    if path == "assets/faq-search.js":
        return Response(FAQ_SEARCH_JS, media_type="text/javascript")
    if path == "p/legacy-gift-card":
        return _layout("Gift cards", "<p>Gift cards are no longer available.</p>")
    if path == "files/price-list.txt":
        return PlainTextResponse("Northwind Supply price list. See each plan page for rates.\n")
    redirects = {
        "old-pricing": (301, f"{SHOP}/pricing"),
        "old-shipping": (301, f"{SHOP}/legacy-shipping"),
        "legacy-shipping": (302, f"{SHOP}/shipping"),
    }
    if path in redirects:
        status, target = redirects[path]
        return RedirectResponse(target, status_code=status)
    for prefix, status in (("retired/", 404), ("archive/", 410), ("reports/", 500)):
        if path.startswith(prefix):
            return Response(f"Unavailable ({status})", status_code=status)
    return Response("Page not found", status_code=404)


# --------------------------------------------------------------- ground truth

ALL_PAGES = (
    [f"{SHOP}/"]
    + _CAT_PATHS
    + _PLAN_PATHS
    + list(_INFO_PATHS.values())
    + [plan_path("legacy-gift-card"), f"{SHOP}/files/price-list.txt"]
    + [info_path(slug) for slug in MEDIA]
)


def build_ground_truth(origin):
    """Seeded faults and decoys as claims with absolute URLs."""

    def u(path):
        return origin + path

    bugs = []
    for i, (src, tgt, status, _) in enumerate(BROKEN_LINKS, 1):
        bugs.append(
            {
                "id": f"broken-{i:02d}",
                "claim_type": "broken_link",
                "urls": [u(src), u(tgt)],
                "details": {"source": u(src), "target": u(tgt)},
                "http_status": status,
            }
        )
    for i, path in enumerate(_OVERFLOW_PAGES, 1):
        bugs.append(
            {
                "id": f"overflow-{i:02d}",
                "claim_type": "horizontal_overflow",
                "urls": [u(path)],
                "details": {"url": u(path)},
            }
        )
    for i, (slug, detail) in enumerate(PRICE_MISMATCH_PLANS.items(), 1):
        plan = PLAN[slug]
        bugs.append(
            {
                "id": f"price-{i:02d}",
                "claim_type": "price_mismatch",
                "urls": [u(cat_path(plan["cat"])), u(plan_path(slug))],
                "details": {"key": _key(plan)},
                "listing_price": plan["price"],
                "detail_price": detail,
            }
        )

    for i, (page, name, status, _) in enumerate(BROKEN_IMAGES, 1):
        bugs.append(
            {
                "id": f"image-{i:02d}",
                "claim_type": "broken_image",
                "urls": [u(page)],
                "details": {"page": u(page), "image": u(f"{IMG}/{name}")},
                "http_status": status,
            }
        )
    for i, (page, _, message) in enumerate(PAGE_ERRORS, 1):
        bugs.append(
            {
                "id": f"error-{i:02d}",
                "claim_type": "page_error",
                "urls": [u(page)],
                "details": {"page": u(page), "message": message},
            }
        )

    def decoy(name, claim_type, urls, details, expected, why):
        return {
            "id": f"decoy-{name}",
            "claim_type": claim_type,
            "urls": [u(x) for x in urls],
            "details": details,
            "expected": expected,
            "why": why,
        }

    def link(name, src, tgt, expected, why, urls=None):
        return decoy(
            name,
            "broken_link",
            urls or [src, tgt],
            {"source": u(src), "target": u(tgt)},
            expected,
            why,
        )

    def overflow(name, path, why):
        return decoy(name, "horizontal_overflow", [path], {"url": u(path)}, "rejected", why)

    def image(name, page, file, expected, why):
        details = {"page": u(page), "image": u(f"{IMG}/{file}")}
        return decoy(name, "broken_image", [page], details, expected, why)

    def error(name, page, message, expected, why):
        details = {"page": u(page), "message": message}
        return decoy(name, "page_error", [page], details, expected, why)

    def price(name, plan, expected, why, key=None):
        return decoy(
            name,
            "price_mismatch",
            [cat_path(plan["cat"]), plan_path(plan["slug"])],
            {"key": key or _key(plan)},
            expected,
            why,
        )

    about, faq = info_path("about"), info_path("faq")
    decoys = [
        link("redirect", about, f"{SHOP}/old-pricing", "rejected", "redirects to a live page"),
        link("redirect-chain", faq, f"{SHOP}/old-shipping", "rejected", "two redirects, live page"),
        link(
            "soft-404",
            cat_path("support"),
            plan_path("legacy-gift-card"),
            "rejected",
            "unavailable notice served with HTTP 200",
        ),
        link(
            "orphan-404",
            info_path("returns"),
            f"{SHOP}/retired/old-newsletter",
            "unverified",
            "404 page that nothing links to",
        ),
        link(
            "file-download",
            info_path("pricing"),
            f"{SHOP}/files/price-list.txt",
            "rejected",
            "text file, HTTP 200",
        ),
        link(
            "anchor",
            faq,
            faq,
            "rejected",
            "same-page anchor link",
            urls=[faq],
        ),
        overflow("scroll-table", info_path("shipping"), "wide table inside a scroll container"),
        overflow("desktop-only", about, "wide block only at desktop widths"),
        overflow("fits", cat_path("email"), "banner narrower than the viewport"),
        overflow("long-word", cat_path("backup"), "long text that wraps"),
        overflow("clipped", cat_path("security"), "wide banner clipped by its own box"),
        price("equal-decimals", PLANS[3], "rejected", "19 and 19.00 are the same price"),
        price("consistent", PLANS[6], "rejected", "listing and detail agree"),
        price(
            "yearly-card",
            PLANS[9],
            "unverified",
            "yearly card exists only on the detail page",
            key=price_key(PLANS[9]["name"] + " Yearly", PLANS[9]["unit"], "year"),
        ),
        price("struck-price", PLANS[12], "unverified", "struck earlier price is not a plain price"),
        price("starting-from", PLANS[15], "unverified", "listing shows a starting-from price"),
        price(
            "audience-card",
            PLANS[18],
            "unverified",
            "different named card for another audience",
            key=price_key("Education " + PLANS[18]["name"], PLANS[18]["unit"]),
        ),
        price("discount", PLANS[21], "unverified", "discounted card has another context"),
        image(
            "image-noscript",
            cat_path("hosting"),
            "noscript-banner.png",
            "unverified",
            "missing image inside noscript, which a browser running scripts never requests",
        ),
        image(
            "image-hidden",
            info_path("gallery"),
            "gallery/old-banner.png",
            "unverified",
            "missing image that the page does not show",
        ),
        image("image-redirect", f"{SHOP}/", "old-logo.png", "rejected", "redirects to an image"),
        image(
            "image-lazy",
            info_path("gallery"),
            "gallery/archive.svg",
            "unverified",
            "lazy image far below the first screen, not loaded yet",
        ),
        error(
            "error-console",
            cat_path("analytics"),
            "Newsletter signup is not configured",
            "rejected",
            "console.error output, not an uncaught error",
        ),
        error(
            "error-caught",
            plan_path("backup-basic"),
            "Saved cart could not be restored",
            "rejected",
            "the page catches this error",
        ),
        error(
            "error-third-party",
            plan_path("storage-plus"),
            "Chat widget failed to start",
            "unverified",
            "uncaught error from a script on another origin",
        ),
        error(
            "error-handled-later",
            info_path("about"),
            "Loyalty points are temporarily unavailable",
            "rejected",
            "the rejected promise gets a handler before the page settles",
        ),
    ]
    return {"bugs": bugs, "decoys": decoys, "pages": [u(p) for p in ALL_PAGES]}


GROUND_TRUTH = build_ground_truth("http://127.0.0.1:8541")
