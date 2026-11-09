"""Controlled faulty and corrected websites: python -m app.demo."""

import asyncio
import os
import re

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

app = FastAPI()
comparison_state = "faulty"
bounty_token = os.getenv("ARSUNE_DEMO_BOUNTY_TOKEN", "")


@app.post("/bounty-proof-control")
async def bounty_proof_control(request: Request):
    """Publish a bounty proof token for this controlled site only."""
    global bounty_token
    token = (await request.json()).get("token")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
        return Response("Invalid token", status_code=422)
    bounty_token = token
    return {"token": token}


@app.get("/.well-known/arsune-bounty.txt", response_class=PlainTextResponse)
def bounty_proof():
    if not bounty_token:
        return PlainTextResponse("No bounty token", status_code=404)
    return bounty_token + "\n"


@app.post("/comparison-control")
async def comparison_control(request: Request):
    """Change only the controlled test site, while keeping its page URLs stable."""
    global comparison_state
    state = (await request.json()).get("state")
    if state not in {"faulty", "repaired", "new_fault", "failed_page", "blocked_css"}:
        return Response("Unknown comparison state", status_code=422)
    comparison_state = state
    return {"state": state}


@app.get("/comparison/{page:path}", response_class=HTMLResponse)
async def comparison_fixture(page: str):
    if comparison_state == "failed_page" and not page:
        return HTMLResponse("Page unavailable", status_code=503)
    if comparison_state == "faulty" and page == "missing":
        return HTMLResponse("Page missing", status_code=404)
    links = "".join(
        f'<a href="/comparison/{target}">{target}</a> '
        for target in ["pricing", "missing", "healthy", "extra-1", "extra-2", "extra-3"]
    )
    price = "$29" if comparison_state == "faulty" and page == "pricing" else "$19"
    overflow = (comparison_state == "faulty" and not page) or (
        comparison_state == "new_fault" and page == "healthy"
    )
    style = "width:900px" if overflow else "max-width:100%"
    blocked = (
        '<link rel="stylesheet" href="http://127.0.0.1:8542/private.css">'
        if comparison_state == "blocked_css" and not page
        else ""
    )
    return f"""<!doctype html><html><head><meta name="viewport"
    content="width=device-width, initial-scale=1">{blocked}
    <style>body{{font:18px sans-serif;margin:16px}}a{{display:inline-block;margin:6px}}</style>
    </head><body><h1>Controlled comparison fixture</h1><nav>{links}</nav>
    <p>Basic plan, USD, per month, one seat:</p>
    <p data-fact-key="basic-price" data-context="USD|month|one-seat">{price}</p>
    <div style="{style}">Product details</div></body></html>"""


@app.get("/network/{page:path}")
async def network_fixture(page: str, request: Request):
    """Deterministic browser fixtures, without access to any customer website."""
    port = request.url.port
    external = f"http://cdn.test:{port}/network"
    private = "http://127.0.0.1:8542/internal"
    if page == "start":
        return RedirectResponse("/network/final/index.html", status_code=302)
    if page == "upgrade":
        return RedirectResponse(f"https://site.test:{port}/network/final/index.html")
    if page == "www":
        return RedirectResponse(f"http://www.site.test:{port}/network/final/index.html")
    if page.startswith("loop/"):
        return RedirectResponse("/network/loop/b" if page.endswith("a") else "/network/loop/a")
    if page.startswith("chain/"):
        step = int(page.rsplit("/", 1)[1])
        return RedirectResponse(f"/network/chain/{step + 1}")
    if page == "private":
        return RedirectResponse(private)
    if page == "asset-private":
        return RedirectResponse(private)
    if page in {"site.css", "slow.css", "final/site.css"}:
        if page == "slow.css":
            await asyncio.sleep(1.0)
        return Response(".wide{width:auto!important;max-width:100%}", media_type="text/css")
    if page == "missing.css":
        return Response("Missing", status_code=404, media_type="text/css")
    if page == "invalid.woff2":
        return Response(
            b"This is not a font.",
            media_type="font/woff2",
            headers={"access-control-allow-origin": "*"},
        )
    if page == "script.js":
        return Response(
            "document.body.dataset.externalScript='loaded';", media_type="text/javascript"
        )
    if page == "image.svg":
        return Response(
            '<svg xmlns="http://www.w3.org/2000/svg" width="7" height="9"><rect width="7" height="9"/></svg>',
            media_type="image/svg+xml",
        )
    if page == "final/index.html":
        content = '<link rel="stylesheet" href="site.css"><a href="child">Child</a>'
    elif page == "external":
        content = f'<link rel="stylesheet" href="{external}/site.css"><script defer src="{external}/script.js"></script><img id="external-image" src="{external}/image.svg">'
    elif page == "failed-font":
        content = (
            '<script>const font = new FontFace("Broken", '
            f"`url({external}/invalid.woff2)`);"
            "document.fonts.add(font); font.load().catch(() => {});</script>"
        )
    elif page == "dns-resource":
        content = '<link rel="stylesheet" href="http://rebind.test:8542/network/site.css">'
    elif page == "private-resource":
        content = f'<link rel="stylesheet" href="{external}/asset-private"><img src="{private}">'
    elif page in {"delayed-style", "missing-style"}:
        css = "slow.css" if page == "delayed-style" else "missing.css"
        content = f'<link rel="stylesheet" href="{external}/{css}">'
    elif page == "many-pages":
        content = "".join(f'<a href="/network/page/{n}">{n}</a>' for n in range(8))
    elif page.startswith("page/"):
        content = "Healthy child page"
    else:
        content = "Healthy page"
    width = "900px" if page in {"delayed-style", "missing-style", "failed-font"} else "auto"
    return HTMLResponse(
        '<!doctype html><html><head><meta name="viewport" '
        'content="width=device-width, initial-scale=1"></head>'
        f'<body>{content}<div class="wide" style="width:{width};min-height:20px">'
        "Fixture</div></body></html>"
    )


def price_card(text: str, name: str = "Basic") -> str:
    return f'<section class="plan"><h2>{name}</h2><p>{text}</p></section>'


PRICE_CASES = {
    "different": ("USD 19 per month, per seat", "USD 29 per month, per seat"),
    "equal": ("USD 19 per month, per seat", "USD 19.00 per month, per seat"),
    "annual": ("USD 19 per month, per seat", "USD 190 per year, per seat"),
    "annual-equivalent": (
        "USD 19 per month, per seat",
        "USD 15 per month, per seat, billed annually",
    ),
    "currency": ("USD 19 per month, per seat", "EUR 29 per month, per seat"),
    "unit": ("USD 19 per month, per seat", "USD 29 per month, per account"),
    "qualifier": (
        "USD 19 per month, per seat for businesses",
        "USD 29 per month, per seat for students",
    ),
    "quantity": ("USD 19 per month for 1 seat", "USD 29 per month for 5 seats"),
    "missing-period": ("USD 19", "USD 29"),
    "missing-currency": ("19 per month, per seat", "29 per month, per seat"),
    "ambiguous-dollar": ("$19 per month, per seat", "$29 per month, per seat"),
    "from": ("From USD 19 per month, per seat", "From USD 29 per month, per seat"),
    "trial": (
        "USD 19 per month, per seat after a free trial",
        "USD 29 per month, per seat after a free trial",
    ),
    "discount": (
        "USD 19 per month, per seat with a 20% discount",
        "USD 29 per month, per seat with a 20% discount",
    ),
    "strike": (
        "<s>USD 99</s> USD 19 per month, per seat",
        "<s>USD 89</s> USD 19 per month, per seat",
    ),
}


@app.get("/prices/{case}/{page}", response_class=HTMLResponse)
def price_fixture(case: str, page: str):
    """Plain visible cards for checks that must not depend on test annotations."""
    if page not in {"first", "second"}:
        return HTMLResponse("Unknown page", status_code=404)
    index = page == "second"
    name = "Basic"
    if case == "product":
        text = "USD 29 per month, per seat" if index else "USD 19 per month, per seat"
    elif case == "variant":
        text = "USD 29 per month, per seat" if index else "USD 19 per month, per seat"
        name = "Basic Plus" if index else "Basic"
    elif case in {"hidden", "below-fold"}:
        text = "USD 19 per month, per seat"
    elif case in PRICE_CASES:
        text = PRICE_CASES[case][index]
        if case == "different" and comparison_state == "repaired":
            text = PRICE_CASES[case][0]
    else:
        return HTMLResponse("Unknown pricing case", status_code=404)
    hidden = (
        "<section hidden><h2>Basic</h2><p>USD 99 per month, per seat</p></section>"
        '<section style="display:none"><h2>Basic</h2><p>USD 89 per month, per seat</p></section>'
        '<section style="visibility:hidden"><h2>Basic</h2><p>USD 79 per month, per seat</p></section>'
        if case == "hidden" and index
        else ""
    )
    product = ("Analytics" if index else "Hosting") if case == "product" else "Plans"
    spacing = '<div style="height:1500px">Product information</div>' if case == "below-fold" else ""
    other = "first" if index else "second"
    return f"""<!doctype html><html><head><title>Pricing fixture</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>body{{font:18px sans-serif;margin:16px}}.plan{{max-width:300px}}</style>
    </head><body><h1>{product}</h1><a href="/prices/{case}/{other}">Other offer page</a>
    {spacing}{price_card(text, name)}{hidden}</body></html>"""


@app.get("/{variant}/{page:path}", response_class=HTMLResponse)
def demo(variant: str, page: str):
    if variant not in {"faulty", "corrected"}:
        return HTMLResponse("Unknown fixture", status_code=404)
    faulty = variant == "faulty"
    if page == "redirect-private":
        return RedirectResponse("http://127.0.0.1:8542/internal")
    if page == "missing" and faulty:
        return HTMLResponse("<h1>Page missing</h1>", status_code=404)
    price = "$29" if faulty and page == "pricing" else "$19"
    links = "".join(
        f'<a href="/{variant}/{target}">{target}</a> '
        for target in ["pricing", "missing", "healthy", "extra-1", "extra-2", "extra-3"]
    )
    overflow = "width:900px" if faulty and not page else "max-width:100%"
    probe = '<img src="http://127.0.0.1:8542/internal">' if page == "resource-policy" else ""
    return f"""<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Arsune controlled {variant} fixture</title><style>body{{font:18px sans-serif;margin:16px}}a{{display:inline-block;margin:6px}}</style></head>
    <body><h1>Arsune controlled {variant} fixture</h1><nav>{links}</nav>{probe}
    <p>Basic plan, USD, per month, one seat:</p><p data-fact-key="basic-price" data-context="USD|month|one-seat">{price}</p>
    <div style="{overflow};background:#ddd">Product details and current offer</div>
    <p>This site is a controlled test. Page: {page or "home"}.</p></body></html>"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("ARSUNE_DEMO_PORT", "8541")))
