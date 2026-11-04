"""Browser inspection and independently checked findings."""
import asyncio
import hashlib
import inspect
import json
import socket
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from playwright.async_api import Error as BrowserError
from playwright.async_api import async_playwright

from .model import generate_candidates
from .policy import TargetPolicyError, origin, validate_target

VIEWPORTS = {"desktop": {"width": 1280, "height": 900}, "mobile": {"width": 390, "height": 844}}
ROLES = ["navigation", "mobile", "consistency"]


def candidate(role, claim_type, urls, explanation, details, injected=False):
    return {
        "role": role, "claim_type": claim_type, "urls": urls, "explanation": explanation,
        "expected_behavior": "The page works at the stated size and shows matching facts.",
        "observed_behavior": explanation, "details": details, "injected": injected,
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
        if not isinstance(urls, list) or not urls or len(urls) > 5 or any(u not in selected for u in urls):
            raise ValueError("Candidate references a page outside the selected scope.")
        steps = item.get("reproduction_steps")
        if not isinstance(steps, list) or not steps or len(steps) > 12 or any(not isinstance(s, str) or len(s) > 2000 for s in steps):
            raise ValueError("Candidate needs bounded reproduction steps.")
        details = item.get("details", {})
        if not isinstance(details, dict):
            raise TypeError("Invalid candidate details.")
        for key in ["source", "target", "url"]:
            if key in details and details[key] not in selected:
                raise ValueError("Candidate details reference an unselected page.")
        item["id"] = hashlib.sha256(json.dumps(
            [item["role"], item["claim_type"], sorted(urls), details], sort_keys=True,
        ).encode()).hexdigest()[:20]
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
                result.append(candidate("navigation", "broken_link", [source, link],
                    f"The linked page returns HTTP {target['status']}.", {"source": source, "target": link}))
    for observation in observations:
        if observation["viewport"] == VIEWPORTS["mobile"] and observation.get("scroll_width", 0) > 392:
            result.append(candidate("mobile", "horizontal_overflow", [observation["url"]],
                "The page content is wider than the 390 pixel mobile viewport.", {"url": observation["url"]}))
    facts = {}
    for url, observation in desktop.items():
        for fact in observation.get("facts", []):
            facts.setdefault(fact["key"], []).append((url, fact["value"]))
    for key, values in facts.items():
        for index, (url, value) in enumerate(values):
            for other_url, other_value in values[index + 1:]:
                if url != other_url and value != other_value:
                    result.append(candidate("consistency", "price_mismatch", [url, other_url],
                        f"The same product, currency, and billing context show {value} and {other_value}.", {"key": key}))
    return result


async def run_scan(url: str, output_dir: Path, *, mode="scripted", inject_demo_false_positive=False,
                   checkpoint=None, save_checkpoint=lambda state: None, emit_event=lambda role, message: None):
    async with asyncio.timeout(240):
        return await _scan(url, Path(output_dir), mode, inject_demo_false_positive,
                           checkpoint, save_checkpoint, emit_event)


async def _scan(url, output_dir, mode, inject, checkpoint, save_checkpoint, emit_event):
    url = await validate_target(url)
    if mode not in {"scripted", "model"}:
        raise ValueError("Mode must be scripted or model.")
    from arsune.config import settings
    demo = settings.demo_url
    if inject and origin(url) != origin(demo):
        raise ValueError("The false candidate is permitted only on the configured demo origin.")
    state = checkpoint or {"selected": [url], "observations": [], "checks": {}, "activity": []}
    if not isinstance(state.get("selected"), list) or len(state["selected"]) > 5:
        raise ValueError("Invalid checkpoint scope.")
    for selected_url in state["selected"]:
        if origin(selected_url) != origin(url):
            raise ValueError("Checkpoint includes an unrelated origin.")
        await validate_target(selected_url)
    output_dir.mkdir(parents=True, exist_ok=True)

    async def save():
        value = save_checkpoint(state)
        if inspect.isawaitable(value):
            await value

    async def event(role, message):
        state["activity"].append({"role": role, "message": message})
        value = emit_event(role, message)
        if inspect.isawaitable(value):
            await value

    requests = state.get("request_count", 0)
    transferred = state.get("transfer_reserved_bytes", 0)
    blocked = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=["--disable-background-networking", "--disable-quic"])
        context = await browser.new_context(service_workers="block", accept_downloads=False)
        network = httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False)

        async def route_request(route):
            nonlocal requests, transferred
            request = route.request
            reserved = 0
            consumed = 0
            try:
                if requests >= 400:
                    raise TargetPolicyError("Request budget exceeded.")
                requests += 1
                state["request_count"] = requests
                await save()
                target = await validate_target(request.url)
                if origin(target) != origin(url) or request.method not in {"GET", "HEAD"}:
                    raise TargetPolicyError("Request exceeds the inspection scope.")
                if request.is_navigation_request() and target not in state["selected"]:
                    raise TargetPolicyError("Navigation target is outside the selected page set.")
                reserved = 2_000_000
                if transferred + reserved > 20_000_000:
                    reserved = 0
                    raise TargetPolicyError("Resource budget exceeded.")
                transferred += reserved
                state["transfer_reserved_bytes"] = transferred
                await save()
                # Resolve once and connect to that numeric address. Host and SNI retain
                # the requested identity. This prevents a DNS rebind between check and connect.
                parts = urlsplit(target)
                records = await asyncio.get_running_loop().getaddrinfo(
                    parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                    type=socket.SOCK_STREAM)
                address = records[0][4][0]
                import ipaddress
                if origin(target) != origin(demo) and any(
                    not ipaddress.ip_address(record[4][0]).is_global for record in records
                ):
                    raise TargetPolicyError("Resolved destination is private.")
                host = f"[{address}]" if ":" in address else address
                endpoint = urlunsplit((parts.scheme, f"{host}:{parts.port or (443 if parts.scheme == 'https' else 80)}",
                                       parts.path, parts.query, ""))
                headers = {"host": parts.netloc, "accept": request.headers.get("accept", "*/*")}
                async with network.stream("GET", endpoint, headers=headers,
                    extensions={"sni_hostname": parts.hostname}) as response:
                    if 300 <= response.status_code < 400 and response.headers.get("location"):
                        destination = urljoin(target, response.headers["location"])
                        if len(blocked) < 100:
                            blocked.append(destination)
                        # Never hand a redirect to Chromium: redirect follow-up
                        # requests can bypass Playwright's initial route callback.
                        raise TargetPolicyError("Redirects require a separate selected-page inspection.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        consumed += len(chunk)
                        if len(body) > 2_000_000:
                            raise TargetPolicyError("Resource budget exceeded.")
                    response_headers = {k: v for k, v in response.headers.items()
                                        if k not in {"content-length", "content-encoding", "transfer-encoding", "set-cookie"}}
                    await route.fulfill(status=response.status_code, headers=response_headers, body=bytes(body))
            except (TargetPolicyError, BrowserError, ValueError, OSError, httpx.HTTPError):
                if len(blocked) < 100:
                    blocked.append(request.url)
                await route.abort()
            finally:
                if reserved:
                    # Retain the full reservation if a worker is killed mid-request.
                    transferred -= max(0, reserved - consumed)
                    state["transfer_reserved_bytes"] = transferred
                    await save()

        await context.route("**/*", route_request)
        await context.route_web_socket("**/*", lambda socket: socket.close())

        async def observe(target, viewport):
            page = await context.new_page()
            await page.set_viewport_size(viewport)
            observation = {"url": target, "viewport": viewport,
                           "captured_at": datetime.now(UTC).isoformat(), "status": None,
                           "links": [], "facts": []}
            try:
                response = await page.goto(target, wait_until="domcontentloaded", timeout=12000)
                await page.wait_for_timeout(200)
                observation.update(await page.evaluate("""() => ({
                    final_url: location.href,
                    text: document.body ? document.body.innerText.slice(0, 16000) : '',
                    scroll_width: document.documentElement.scrollWidth,
                    client_width: document.documentElement.clientWidth,
                    links: Array.from(document.querySelectorAll('a[href]')).slice(0,80).map(a => a.href),
                    facts: Array.from(document.querySelectorAll('[data-fact-key][data-context]')).slice(0,40).map(e => ({
                        key: e.dataset.factKey + '|' + e.dataset.context,
                        value: e.innerText.trim(), selector: '[data-fact-key="' + e.dataset.factKey + '"]'
                    }))
                })"""))
                observation["status"] = response.status if response else None
                observation["links"] = list(dict.fromkeys(
                    urljoin(target, link).split("#")[0] for link in observation["links"]
                    if urlsplit(link).scheme in {"http", "https"} and origin(link) == origin(url)))
                filename = f"capture-{uuid.uuid4().hex}.png"
                await page.screenshot(path=str(output_dir / filename), full_page=False, timeout=5000)
                observation["screenshot"] = filename
            except BrowserError:
                observation["error"] = "The browser could not complete this page request."
            finally:
                await page.close()
            return observation

        try:
            index = 0
            while index < len(state["selected"]):
                target = state["selected"][index]
                for viewport in VIEWPORTS.values():
                    if not any(o["url"] == target and o["viewport"] == viewport for o in state["observations"]):
                        await event("coordinator", f"Inspecting {target} at width {viewport['width']}.")
                        observation = await observe(target, viewport)
                        state["observations"].append(observation)
                        if viewport == VIEWPORTS["desktop"]:
                            for link in observation["links"]:
                                if len(state["selected"]) >= 5:
                                    break
                                if link not in state["selected"]:
                                    state["selected"].append(link)
                        await save()
                index += 1
            if "candidates" not in state:
                candidates = []
                state.setdefault("role_outputs", {})
                state.setdefault("model_reservations", {})
                provider = {"name": "scripted", "live_call": False}
                for role in ROLES:
                    if role not in state["role_outputs"]:
                        await event(role, "Examining the stored browser observations.")
                        if mode == "model":
                            if state["model_reservations"].get(role):
                                raise ValueError("A previous model call has an uncertain charge. Start a new authorized job.")
                            state["model_reservations"][role] = True
                            await save()
                            role_candidates, provider = await generate_candidates(state["observations"], role=role)
                            if any(c.get("role") != role for c in role_candidates):
                                raise ValueError("Model specialist returned another role's candidate.")
                            state.setdefault("model_providers", {})[role] = provider
                        else:
                            role_candidates = [c for c in scripted(state["observations"]) if c["role"] == role]
                        state["role_outputs"][role] = validate_candidates(role_candidates, state["selected"])
                        await event(role, f"Proposed {len(role_candidates)} candidates.")
                        await save()
                    candidates.extend(state["role_outputs"][role])
                if mode == "model":
                    provider = {"name": "compatible-model", "live_call": True, "specialists": state["model_providers"],
                                "reserved_cost_usd": sum(p["reserved_cost_usd"] for p in state["model_providers"].values())}
                if inject:
                    healthy = next((u for u in state["selected"] if "/healthy" in u), None)
                    if healthy:
                        candidates.append(candidate("navigation", "broken_link", [url, healthy],
                            "Injected demo claim: the healthy link returns HTTP 404.", {"source": url, "target": healthy}, True))
                state["candidates"] = validate_candidates(candidates, state["selected"])
                state["provider"] = provider
                await save()
            for item in state["candidates"]:
                if item["id"] in state["checks"]:
                    continue
                await event("checker", f"Repeating the {item['claim_type']} check.")
                fresh = [await observe(u, VIEWPORTS["mobile"] if item["claim_type"] == "horizontal_overflow" else VIEWPORTS["desktop"]) for u in item["urls"]]
                status, reason = "unverified", "The supplied claim could not be checked with the supported measurements."
                details = item.get("details", {})
                if all(o.get("status") is not None for o in fresh):
                    if item["claim_type"] == "broken_link":
                        source = next((o for o in fresh if o["url"] == details.get("source")), None)
                        target = next((o for o in fresh if o["url"] == details.get("target")), None)
                        if source and target and target["url"] in source["links"]:
                            status = "confirmed" if target["status"] >= 400 else "rejected"
                            reason = f"A fresh source-page read found the link. A fresh target request returned HTTP {target['status']}."
                    elif item["claim_type"] == "horizontal_overflow":
                        if fresh[0]["status"] < 400:
                            width = fresh[0].get("scroll_width", 0)
                            status = "confirmed" if width > 392 else "rejected"
                            reason = f"Fresh measurement: content width {width} pixels; viewport width 390 pixels."
                    elif item["claim_type"] == "price_mismatch" and len(fresh) == 2:
                        values = [[f["value"] for f in o["facts"] if f["key"] == details.get("key")] for o in fresh]
                        if all(len(v) == 1 for v in values):
                            status = "confirmed" if values[0] != values[1] else "rejected"
                            reason = f"Fresh page reads for context {details['key']}: {values[0][0]} and {values[1][0]}."
                result = {**item, "status": status, "checker_reason": reason, "evidence": fresh,
                          "visitor_impact": "Visitors can have difficulty using this page or choosing the stated offer.",
                          "suggested_correction": {"broken_link": "Repair the link or restore the destination page.",
                              "horizontal_overflow": "Make the content fit the mobile viewport.",
                              "price_mismatch": "Use the same value for the same product and billing context."}.get(item["claim_type"], "Review the supplied reproduction steps.")}
                if status == "rejected":
                    result["visitor_impact"] = "The fresh check did not reproduce this claimed fault."
                    result["suggested_correction"] = "No repair is needed for this tested claim."
                elif status == "unverified":
                    result["visitor_impact"] = "The effect on visitors is not known."
                    result["suggested_correction"] = "Collect more evidence before deciding on a repair."
                state["checks"][item["id"]] = result
                await save()
        finally:
            await network.aclose()
            await context.close()
            await browser.close()
    findings = list(state["checks"].values())
    report = {"site_url": url, "mode": mode, "provider": state["provider"],
              "inspected_pages": state["selected"], "observations": state["observations"],
              "viewports": VIEWPORTS, "findings": findings, "activity": state["activity"],
              "coverage": {"page_limit": 5, "selected_count": len(state["selected"]), "roles": ROLES,
                           "requests": requests, "scope": "Selected pages only; no forms submitted.",
                           "blocked_requests": blocked, "external_resources": "External origins are blocked; rendering may be incomplete.",
                           "redirect_policy": "Redirects are blocked; submit the final permitted URL as a new scan.",
                           "consistency_scope": "Only explicitly identified data-fact-key and data-context facts can be confirmed; other consistency claims remain unverified in both modes.",
                           "incomplete_pages": [o["url"] for o in state["observations"] if o.get("error")]}}
    for status in ["confirmed", "rejected", "unverified"]:
        report[status] = [f for f in findings if f["status"] == status]
    return report
