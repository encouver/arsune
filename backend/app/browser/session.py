import asyncio
import base64
import ipaddress
import socket
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from playwright.async_api import Error as BrowserError
from playwright.async_api import async_playwright

from app.browser.policy import TargetPolicyError, document_scope, origin, validate_target

MAX_REDIRECTS = 3
RESOURCE_READY_TIMEOUT_MS = 3000


@asynccontextmanager
async def inspection_browser(url, demo, state, save):
    blocked = state.setdefault("blocked_requests", [])
    issues = state.setdefault("resource_issues", [])
    redirects = state.setdefault("redirects", [])
    lock = asyncio.Lock()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-background-networking",
                "--disable-quic",
                "--disable-site-isolation-trials",
            ],
        )
        context = await browser.new_context(service_workers="block", accept_downloads=False)
        network = httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False)

        async def prepare_page(page):
            await page.add_init_script("""(() => {
                window.__arsunePolicyIssues = [];
                document.addEventListener('securitypolicyviolation', event => {
                    if (window.__arsunePolicyIssues.length < 100) {
                        window.__arsunePolicyIssues.push({url: event.blockedURI,
                            page_url: location.href, resource_type: 'Policy',
                            reason: 'Browser policy blocked ' + event.effectiveDirective + '.'});
                    }
                });
            })();""")
            session = await context.new_cdp_session(page)
            chains = {}
            pending = set()
            page_issues = []
            page._arsune_resource_issues = page_issues
            page._arsune_pending = pending

            def failed(request):
                kind = request.resource_type.capitalize()
                if len(page_issues) < 400 and not any(
                    issue["url"] == request.url for issue in page_issues
                ):
                    page_issues.append(
                        {
                            "url": request.url,
                            "page_url": page.url,
                            "resource_type": kind,
                            "reason": request.failure
                            or "The browser could not load this resource.",
                        }
                    )

            page.on("requestfailed", failed)

            async def intercept(event):
                request = event["request"]
                target = request["url"]
                kind = event.get("resourceType", "Other")
                request_id = event["requestId"]
                reserved = consumed = 0
                try:
                    async with lock:
                        if state.get("request_count", 0) >= 400:
                            raise TargetPolicyError("Request budget exceeded.")
                        state["request_count"] = state.get("request_count", 0) + 1
                        await save()
                    target = await validate_target(target)
                    if request["method"] not in {"GET", "HEAD"}:
                        raise TargetPolicyError("Only GET and HEAD requests are permitted.")
                    if kind not in {"Document", "Stylesheet", "Script", "Image", "Font"}:
                        raise TargetPolicyError(
                            "This resource type is outside the inspection scope."
                        )
                    previous = event.get("redirectedRequestId")
                    hops = chains.get(previous, 0) if previous else 0
                    chains[request_id] = hops
                    if kind == "Document":
                        if not document_scope(url, target):
                            raise TargetPolicyError(
                                "Navigation target is outside the permitted site."
                            )
                        if target not in state["selected"]:
                            raise TargetPolicyError(
                                "Navigation target is outside the selected page set."
                            )
                    async with lock:
                        reserved = 2_000_000
                        if state.get("transfer_reserved_bytes", 0) + reserved > 20_000_000:
                            reserved = 0
                            raise TargetPolicyError("Resource budget exceeded.")
                        state["transfer_reserved_bytes"] = (
                            state.get("transfer_reserved_bytes", 0) + reserved
                        )
                        await save()
                    parts = urlsplit(target)
                    records = await asyncio.wait_for(
                        asyncio.get_running_loop().getaddrinfo(
                            parts.hostname,
                            parts.port or (443 if parts.scheme == "https" else 80),
                            type=socket.SOCK_STREAM,
                        ),
                        5,
                    )
                    if not records or (
                        origin(target) != origin(demo)
                        and any(
                            not ipaddress.ip_address(record[4][0]).is_global for record in records
                        )
                    ):
                        raise TargetPolicyError("Resolved destination is private.")
                    address = records[0][4][0]
                    host = f"[{address}]" if ":" in address else address
                    endpoint = urlunsplit(
                        (
                            parts.scheme,
                            f"{host}:{parts.port or (443 if parts.scheme == 'https' else 80)}",
                            parts.path,
                            parts.query,
                            "",
                        )
                    )
                    headers = {
                        "host": parts.netloc,
                        "accept": request.get("headers", {}).get("Accept", "*/*"),
                    }
                    for name, value in request.get("headers", {}).items():
                        if name.lower() in {"origin", "referer", "accept-language", "user-agent"}:
                            headers[name.lower()] = value
                    async with network.stream(
                        request["method"],
                        endpoint,
                        headers=headers,
                        extensions={"sni_hostname": parts.hostname},
                    ) as response:
                        location = response.headers.get("location")
                        if 300 <= response.status_code < 400 and location:
                            destination = urljoin(target, location)
                            record = {
                                "url": target,
                                "target": destination,
                                "resource_type": kind,
                                "status": response.status_code,
                                "allowed": False,
                            }
                            try:
                                destination = await validate_target(destination)
                                if hops >= MAX_REDIRECTS:
                                    raise TargetPolicyError("Redirect limit exceeded.")
                                if kind == "Document":
                                    if not document_scope(
                                        target, destination
                                    ) or not document_scope(url, destination):
                                        raise TargetPolicyError(
                                            "Redirect target is outside the permitted site."
                                        )
                                    async with lock:
                                        if destination not in state["selected"]:
                                            if len(state["selected"]) >= 5:
                                                raise TargetPolicyError(
                                                    "Document URL budget exceeded."
                                                )
                                            state["selected"].append(destination)
                                chains[request_id] = hops + 1
                                record["allowed"] = True
                            except TargetPolicyError as exc:
                                record["reason"] = str(exc)
                                if len(blocked) < 100 and destination not in blocked:
                                    blocked.append(destination)
                                raise
                            finally:
                                if len(redirects) < 400:
                                    redirects.append(record)
                                await save()
                            await session.send(
                                "Fetch.fulfillRequest",
                                {
                                    "requestId": request_id,
                                    "responseCode": response.status_code,
                                    "responseHeaders": [{"name": "location", "value": destination}],
                                    "body": "",
                                },
                            )
                            return
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            consumed += len(chunk)
                            if len(body) > 2_000_000:
                                raise TargetPolicyError("Resource budget exceeded.")
                        if kind != "Document" and response.status_code >= 400:
                            raise TargetPolicyError(
                                f"Resource returned HTTP {response.status_code}."
                            )
                        response_headers = [
                            {"name": k, "value": v}
                            for k, v in response.headers.items()
                            if k
                            not in {
                                "content-length",
                                "content-encoding",
                                "transfer-encoding",
                                "set-cookie",
                            }
                        ]
                        if kind == "Document":
                            response_headers.append(
                                {
                                    "name": "content-security-policy",
                                    "value": "sandbox allow-scripts allow-same-origin; frame-src 'none'; worker-src 'none'; connect-src 'none'; object-src 'none'; form-action 'none'",
                                }
                            )
                        await session.send(
                            "Fetch.fulfillRequest",
                            {
                                "requestId": request_id,
                                "responseCode": response.status_code,
                                "responseHeaders": response_headers,
                                "body": base64.b64encode(body).decode("ascii"),
                            },
                        )
                except (
                    TargetPolicyError,
                    BrowserError,
                    ValueError,
                    OSError,
                    TimeoutError,
                    httpx.HTTPError,
                ) as exc:
                    issue = {
                        "url": target,
                        "page_url": page.url,
                        "resource_type": kind,
                        "reason": str(exc),
                    }
                    if len(issues) < 400:
                        issues.append(issue)
                    if len(page_issues) < 400:
                        page_issues.append(issue)
                    if len(blocked) < 100 and target not in blocked:
                        blocked.append(target)
                    try:
                        await session.send(
                            "Fetch.failRequest",
                            {"requestId": request_id, "errorReason": "BlockedByClient"},
                        )
                    except BrowserError:
                        pass
                    await save()
                finally:
                    if reserved:
                        async with lock:
                            state["transfer_reserved_bytes"] -= max(0, reserved - consumed)
                            await save()

            def paused(event):
                task = asyncio.create_task(intercept(event))
                pending.add(task)
                task.add_done_callback(pending.discard)

            session.on("Fetch.requestPaused", paused)
            await session.send(
                "Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}
            )

        context._arsune_prepare_page = prepare_page
        await context.route_web_socket("**/*", lambda socket: socket.close())
        try:
            yield context, blocked
        finally:
            await context.close()
            await network.aclose()
            await browser.close()
