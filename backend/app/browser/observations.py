import uuid
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as BrowserError

from app.browser.policy import document_scope
from app.browser.prices import PRICE_BLOCKS_JS, extract_prices
from app.browser.session import RESOURCE_READY_TIMEOUT_MS


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
