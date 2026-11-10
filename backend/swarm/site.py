"""How agents look at the bounty site: plain HTTP for pages and links, Chromium for layout,
prices, images and page errors.

Every agent walks the site itself. Only the transport is shared: one HTTP connection pool and
one Chromium with a small pool of open pages, so a hundred agents stay light.
"""

import asyncio
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from app.browser.prices import PRICE_BLOCKS_JS, extract_prices

MOBILE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}
MAX_LINKS = 80  # the checker reads the first 80 anchors of a page
MAX_HTML = 1_000_000
# Labelled facts, read the same way as Arsune's checker does.
LABELLED_FACTS_JS = """() => Array.from(document.querySelectorAll('[data-fact-key][data-context]'))
  .filter(e => e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})).slice(0, 40)
  .map(e => ({key: e.dataset.factKey + '|' + e.dataset.context, value: e.innerText.trim(),
              source: 'explicit_fact'}))"""
# Uncaught errors and unhandled rejections, as a visitor's browser raises them. A rejection that
# is handled later is marked handled, so it is not a page error (Arsune treats it the same way).
ERRORS_INIT_JS = """(() => {
  const list = window.__swarmErrors = [];
  const firstLine = v => (v && v.stack ? String(v.stack).split('\\n')[0] : String(v));
  const urlIn = v => ((v && v.stack ? String(v.stack) : '').match(/https?:\\/\\/[^\\s)]+/) || [''])[0];
  addEventListener('error', e => {
    if (!(e instanceof ErrorEvent) || list.length >= 20) return;
    const message = e.error !== undefined ? firstLine(e.error) : e.message;
    list.push({message, source: e.filename || urlIn(e.error)});
  });
  addEventListener('unhandledrejection', e => {
    if (list.length < 20) list.push({message: firstLine(e.reason), source: urlIn(e.reason), promise: e.promise});
  });
  addEventListener('rejectionhandled', e => list.forEach(x => { if (x.promise === e.promise) x.handled = true; }));
})()"""
READ_ERRORS_JS = """() => (window.__swarmErrors || []).filter(e => !e.handled)
  .map(e => ({message: String(e.message).slice(0, 500), source: String(e.source || '')}))"""
# Images that finished loading but show no picture, and whether each one is shown.
BROKEN_IMAGES_JS = """async () => {
  const found = [];
  for (const image of Array.from(document.images).slice(0, 60)) {
    const src = (image.currentSrc || image.src || '').split('#')[0];
    if (!src || !image.complete || image.naturalWidth > 0) continue;
    const decoded = await Promise.race([image.decode().then(() => true, () => false),
                                        new Promise(resolve => setTimeout(() => resolve(true), 500))]);
    if (!decoded) found.push({src, shown: image.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})});
  }
  return found;
}"""


def origin(url: str) -> str:
    """scheme://host[:port], with the default port left out, as Arsune writes it."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    if parts.port not in (None, 443 if parts.scheme == "https" else 80):
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}"


def canonical(url: str) -> str | None:
    """The page URL as Arsune stores it, or None when a claim cannot name it (query string)."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.query:
        return None
    return origin(url) + urljoin("/", parts.path or "/")


def failed_images(look: dict, page_url: str) -> list[dict]:
    """Images on the page's own origin that show no picture, once each: {url, shown}.
    A claim can name only an image on the bounty site, without a query string."""
    images = {}
    for image in look.get("images", []):
        url = canonical(image["src"])
        if url and origin(url) == origin(page_url) and url not in images:
            images[url] = {"url": url, "shown": image["shown"]}
    return list(images.values())


def uncaught_errors(look: dict, page_url: str) -> list[dict]:
    """Uncaught errors of at least 8 characters, once each: {message, first_party}."""
    errors = {}
    for error in look.get("errors", []):
        source, message = error["source"], " ".join(error["message"].split())
        first_party = not source.startswith("http") or origin(source) == origin(page_url)
        if len(message) >= 8 and message not in errors:
            errors[message] = {"message": message, "first_party": first_party}
    return list(errors.values())


def base_path(site_url: str) -> str:
    path = urlsplit(site_url).path or "/"
    return path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"


def in_scope(url: str, site_url: str) -> bool:
    """Pages under the bounty URL's folder on the same origin."""
    return origin(url) == origin(site_url) and urlsplit(url).path.startswith(base_path(site_url))


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors: list[list[str]] = []
        self._open: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href")
        if tag == "a" and href is not None:
            self._open = [href, ""]
            self.anchors.append(self._open)

    def handle_data(self, data):
        if self._open is not None:
            self._open[1] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self._open = None


@dataclass
class Page:
    url: str
    status: int | None
    html: str = ""
    links: list[str] = field(default_factory=list)
    link_text: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300 and bool(self.html)


def read_links(html: str, final_url: str) -> tuple[list[str], dict[str, str]]:
    """Same-origin links among the first 80 anchors, resolved like a browser's `a.href`."""
    parser = _Anchors()
    parser.feed(html)
    links, text = [], {}
    for href, label in parser.anchors[:MAX_LINKS]:
        url = canonical(urljoin(final_url, href.strip()).split("#")[0])
        if url and origin(url) == origin(final_url) and url not in text:
            links.append(url)
            text[url] = " ".join(label.split())
    return links, text


class SiteReader:
    """Shared HTTP transport; each agent keeps its own crawl state."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, concurrency: int = 24):
        self.http = httpx.AsyncClient(
            follow_redirects=True,
            timeout=10,
            transport=transport,
            limits=httpx.Limits(max_connections=concurrency),
            headers={"user-agent": "arsune-swarm-hunter/1"},
        )

    async def fetch(self, url: str) -> Page:
        try:
            response = await self.http.get(url)
        except httpx.HTTPError:
            return Page(url, None)
        html = ""
        if "html" in response.headers.get("content-type", "") and len(response.content) < MAX_HTML:
            html = response.text
        final = str(response.url)
        links, text = read_links(html, final) if html else ([], {})
        # After redirects the page is named by its final URL, so an alias is not a new page.
        return Page(canonical(final) or url, response.status_code, html, links, text)

    async def close(self):
        await self.http.aclose()


class Crawl:
    """One agent's own walk: start at the bounty URL, pick the next page at random."""

    def __init__(self, reader: SiteReader, site_url: str, budget: int, rng):
        self.reader, self.site_url, self.budget, self.rng = reader, site_url, budget, rng
        start = canonical(site_url) or site_url
        self.frontier, self.seen = [start], {start}
        self.pages: dict[str, Page] = {}
        self._status: dict[str, int | None] = {}

    @property
    def done(self) -> bool:
        return not self.frontier or len(self.pages) >= self.budget

    async def step(self) -> Page | None:
        if self.done:
            return None
        url = self.frontier.pop(self.rng.randrange(len(self.frontier)))
        page = await self.reader.fetch(url)
        self._status[url] = page.status
        if page.url in self.pages:  # a redirect to a page this agent already read
            return None
        self.seen.add(page.url)
        self.pages[page.url] = page
        for link in page.links:
            if link not in self.seen and in_scope(link, self.site_url):
                self.seen.add(link)
                self.frontier.append(link)
        return page

    async def status_of(self, url: str) -> int | None:
        """The HTTP status of a link target, read once by this agent."""
        if url not in self._status:
            self._status[url] = (await self.reader.fetch(url)).status
        return self._status[url]


class BrowserPool:
    """One shared Chromium, started on first use, with at most `size` open pages."""

    def __init__(self, size: int = 6):
        self._slots = asyncio.Semaphore(size)
        self._start = asyncio.Lock()
        self._playwright = None
        self._contexts: dict[str, object] = {}

    async def _context(self, kind: str):
        async with self._start:
            if not self._contexts:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                browser = await self._playwright.chromium.launch()
                self._contexts = {
                    "mobile": await browser.new_context(viewport=MOBILE),
                    "desktop": await browser.new_context(viewport=DESKTOP),
                }
                await self._contexts["desktop"].add_init_script(ERRORS_INIT_JS)
        return self._contexts[kind]

    async def _visit(self, url: str, kind: str, work):
        from playwright.async_api import Error as BrowserError

        async with self._slots:
            page = await (await self._context(kind)).new_page()
            try:
                response = await page.goto(url, wait_until="load", timeout=12_000)
                if not response or not 200 <= response.status < 300:
                    return None
                return await work(page)
            except BrowserError:
                return None
            finally:
                await page.close()

    async def page_width(self, url: str) -> int | None:
        """Content width at the 390 pixel mobile viewport."""

        async def width(page):
            return await page.evaluate("() => document.documentElement.scrollWidth")

        return await self._visit(url, "mobile", width)

    async def price_facts(self, url: str) -> list[dict] | None:
        """Labelled facts and plain visible price cards, keyed as Arsune's checker keys them."""

        async def facts(page):
            labelled = await page.evaluate(LABELLED_FACTS_JS)
            blocks = await page.evaluate(PRICE_BLOCKS_JS)
            return labelled + extract_prices(blocks["blocks"])["facts"]

        return await self._visit(url, "desktop", facts)

    async def desktop_look(self, url: str) -> dict | None:
        """One desktop visit: price facts, images that do not load, and uncaught errors."""

        async def look(page):
            labelled = await page.evaluate(LABELLED_FACTS_JS)
            blocks = await page.evaluate(PRICE_BLOCKS_JS)
            await page.wait_for_timeout(250)  # errors thrown from timers and load handlers
            return {
                "facts": labelled + extract_prices(blocks["blocks"])["facts"],
                "images": await page.evaluate(BROKEN_IMAGES_JS),
                "errors": await page.evaluate(READ_ERRORS_JS),
            }

        return await self._visit(url, "desktop", look)

    async def close(self):
        if self._playwright:
            for context in self._contexts.values():
                await context.close()
            await self._playwright.stop()


WIDE_STYLE = re.compile(r"(?:min-)?width\s*[:=]\s*\"?(\d{3,4})(?:px)?", re.IGNORECASE)


def looks_wide(html: str) -> bool:
    """A careless reading of the markup: any fixed width above the phone width."""
    return any(int(value) > 390 for value in WIDE_STYLE.findall(html))
