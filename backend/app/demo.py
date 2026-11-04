"""Controlled faulty and corrected websites: python -m app.demo."""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI()


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
    import os

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("ARSUNE_DEMO_PORT", "8541")))
