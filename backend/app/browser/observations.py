import uuid
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as BrowserError

from app.browser.policy import document_scope, origin
from app.browser.prices import PRICE_BLOCKS_JS, extract_prices
from app.browser.session import MAX_PAGE_ERRORS, RESOURCE_READY_TIMEOUT_MS

# An image that already failed rejects decode(); a decoded one with no intrinsic size resolves.
IMAGES_JS = """async () => {
    const images = Array.from(document.images);
    const read = async image => {
        let decoded = null;
        if (image.complete && (image.currentSrc || image.src)) {
            decoded = image.naturalWidth > 0 || await Promise.race([
                image.decode().then(() => true, () => false),
                new Promise(resolve => setTimeout(() => resolve(null), 500)),
            ]);
        }
        return {
            src: (image.currentSrc || image.src).split('#')[0],
            decoded,
            rendered: image.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}),
        };
    };
    const read60 = await Promise.all(images.slice(0, 60).map(read));
    return {images: read60, truncated: images.length > 60};
}"""


def image_results(images, requests):
    """Join each image element with the request the browser made for it."""
    results = []
    for image in images:
        src = image["src"]
        if urlsplit(src).scheme not in {"http", "https"} or len(src) > 2048:
            continue
        request = requests.get(src, {})
        results.append(
            {
                "src": src,
                "final_url": request.get("final_url", src),
                "status": request.get("status"),
                "failure": request.get("failure"),
                "decoded": image["decoded"],
                "rendered": image["rendered"],
            }
        )
    return results


def page_errors(page, site_url):
    """Uncaught errors that were not revoked, marked by whether the site's script raised them."""
    errors, revoked = page._arsune_errors
    results = []
    for error in errors:
        if error["id"] in revoked:
            continue
        script, source, first_party = error["script_url"], "", False
        try:
            if urlsplit(script).scheme in {"http", "https"}:
                source, first_party = origin(script), document_scope(site_url, script)
        except ValueError:
            pass
        results.append(
            {
                "message": error["message"],
                "kind": error["kind"],
                "source": source,
                "first_party": first_party,
            }
        )
    return results[:MAX_PAGE_ERRORS], len(errors) > MAX_PAGE_ERRORS


async def observe_page(context, target, viewport, output_dir, site_url):
    page = await context.new_page()
    await page.set_viewport_size(viewport)
    await context._arsune_prepare_page(page)
    observation = {
        "url": target,
        "viewport": viewport,
        "captured_at": datetime.now(UTC).isoformat(),
        "status": None,
        "links": [],
        "facts": [],
    }
    try:
        response = await page.goto(target, wait_until="domcontentloaded", timeout=12000)
        try:
            await page.wait_for_load_state("networkidle", timeout=RESOURCE_READY_TIMEOUT_MS)
            fonts_ready = await page.evaluate(
                "() => Promise.race([document.fonts.ready.then(() => Array.from(document.fonts).every(font => font.status !== 'error')), new Promise(resolve => setTimeout(() => resolve(false), 1000))])"
            )
            if not fonts_ready:
                page._arsune_resource_issues.append(
                    {
                        "url": page.url,
                        "page_url": page.url,
                        "resource_type": "Readiness",
                        "reason": "Page fonts failed to load or exceeded the readiness deadline.",
                    }
                )
        except BrowserError:
            page._arsune_resource_issues.append(
                {
                    "url": page.url,
                    "page_url": page.url,
                    "resource_type": "Readiness",
                    "reason": "Resource readiness deadline exceeded.",
                }
            )
        observation.update(
            await page.evaluate("""() => ({
            final_url: location.href,
            text: document.body ? document.body.innerText.slice(0, 16000) : '',
            scroll_width: document.documentElement.scrollWidth,
            client_width: document.documentElement.clientWidth,
            links: Array.from(document.querySelectorAll('a[href]')).slice(0,80).map(a => a.href),
            facts: Array.from(document.querySelectorAll('[data-fact-key][data-context]')).filter(e => e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})).slice(0,40).map(e => ({
                key: e.dataset.factKey + '|' + e.dataset.context,
                value: e.innerText.trim(), quote: e.innerText.trim(), source: 'explicit_fact', selector: '[data-fact-key="' + e.dataset.factKey + '"]'
            }))
        })""")
        )
        read = await page.evaluate(IMAGES_JS)
        observation["images"] = image_results(read["images"], page._arsune_images)
        observation["images_truncated"] = read["truncated"]
        pricing_blocks = await page.evaluate(PRICE_BLOCKS_JS)
        pricing = extract_prices(pricing_blocks["blocks"])
        observation["facts"].extend(pricing["facts"])
        observation["price_exclusions"] = pricing["exclusions"]
        observation["price_extraction_truncated"] = pricing_blocks["truncated"]
        observation["status"] = response.status if response else None
        observation["links"] = list(
            dict.fromkeys(
                urljoin(target, link).split("#")[0]
                for link in observation["links"]
                if urlsplit(link).scheme in {"http", "https"} and document_scope(site_url, link)
            )
        )
        page._arsune_resource_issues.extend(
            await page.evaluate("() => window.__arsunePolicyIssues || []")
        )
        filename = f"capture-{uuid.uuid4().hex}.png"
        await page.screenshot(path=str(output_dir / filename), full_page=False, timeout=5000)
        observation["screenshot"] = filename
        for fact in pricing["facts"]:
            filename = f"capture-{uuid.uuid4().hex}.png"
            try:
                await page.locator(fact["selector"]).screenshot(
                    path=str(output_dir / filename), timeout=2500
                )
                fact["screenshot"] = filename
            except BrowserError:
                fact["screenshot_error"] = "The browser could not capture this price card."
    except BrowserError:
        observation["error"] = "The browser could not complete this page request."
    finally:
        observation["page_errors"], observation["page_errors_truncated"] = page_errors(
            page, site_url
        )
        observation["resource_issues"] = list(page._arsune_resource_issues)
        for issue in observation["resource_issues"]:
            issue["viewport"] = viewport
        observation["layout_complete"] = not observation.get("error") and not any(
            issue["resource_type"]
            in {"Document", "Stylesheet", "Script", "Image", "Font", "Readiness", "Policy"}
            for issue in observation["resource_issues"]
        )
        await page.close()
        pending = list(page._arsune_pending)
        if pending:
            import asyncio

            await asyncio.gather(*pending, return_exceptions=True)
    return observation
