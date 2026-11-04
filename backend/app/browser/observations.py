import uuid
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as BrowserError

from app.browser.policy import origin


async def observe_page(context, target, viewport, output_dir, site_url):
    page = await context.new_page()
    await page.set_viewport_size(viewport)
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
        await page.wait_for_timeout(200)
        observation.update(
            await page.evaluate("""() => ({
            final_url: location.href,
            text: document.body ? document.body.innerText.slice(0, 16000) : '',
            scroll_width: document.documentElement.scrollWidth,
            client_width: document.documentElement.clientWidth,
            links: Array.from(document.querySelectorAll('a[href]')).slice(0,80).map(a => a.href),
            facts: Array.from(document.querySelectorAll('[data-fact-key][data-context]')).slice(0,40).map(e => ({
                key: e.dataset.factKey + '|' + e.dataset.context,
                value: e.innerText.trim(), selector: '[data-fact-key="' + e.dataset.factKey + '"]'
            }))
        })""")
        )
        observation["status"] = response.status if response else None
        observation["links"] = list(
            dict.fromkeys(
                urljoin(target, link).split("#")[0]
                for link in observation["links"]
                if urlsplit(link).scheme in {"http", "https"} and origin(link) == origin(site_url)
            )
        )
        filename = f"capture-{uuid.uuid4().hex}.png"
        await page.screenshot(path=str(output_dir / filename), full_page=False, timeout=5000)
        observation["screenshot"] = filename
    except BrowserError:
        observation["error"] = "The browser could not complete this page request."
    finally:
        await page.close()
    return observation
