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
MAX_PAGE_ERRORS = 20
MAX_IMAGE_REQUESTS = 200
MAX_RESOURCE_BYTES = 2_000_000
# One page load (one URL at one viewport) has its own budget, because a real page transfers a
# few MB and a scan makes up to 10 loads plus the checker's loads. The scan-wide cap still
# stops a hostile site from draining bandwidth over many loads.
MAX_TRANSFER_BYTES = 20_000_000
MAX_SCAN_TRANSFER_BYTES = 100_000_000
# Content encodings that httpx decodes here (br needs the brotli package). A body in any other
# encoding would reach the browser still encoded, so the request stops as an Arsune limit.
DECODED_ENCODINGS = {"identity", "gzip", "deflate", "br"}


def exception_record(details):
    """Keep the message and the script location of one uncaught error or rejection."""
    exception = details.get("exception") or {}
    if exception.get("description"):
        message = exception["description"].split("\n    at ")[0]
    elif "value" in exception:
        message = str(exception["value"])
    else:
        # Errors from another origin's script come only as text, such as "Uncaught Error: ...".
        text = details.get("text", "").removeprefix("Uncaught").strip()
        message = text.removeprefix("(in promise)")
    frames = (details.get("stackTrace") or {}).get("callFrames", [])
    script = details.get("url") or next((f["url"] for f in frames if f.get("url")), "")
    return {
        "id": details.get("exceptionId"),
        "message": message.strip()[:500],
        "kind": "rejection" if "(in promise)" in details.get("text", "") else "error",
        "script_url": script[:2048],
    }


@asynccontextmanager
async def inspection_browser(url, demo, state, save):
    blocked = state.setdefault("blocked_requests", [])
    issues = state.setdefault("resource_issues", [])
    redirects = state.setdefault("redirects", [])
    lock = asyncio.Lock()
    # Each request in flight holds a reservation of MAX_RESOURCE_BYTES until it finishes. The
    # scan-wide count is saved in state; a page load's count is not, because an interrupted
    # load starts again on a new page.
    budget = asyncio.Condition(lock)
    in_flight = 0

    def exceeded_budget(load_bytes):
        """The transfer budget without room for one more resource, if any."""
        if load_bytes + MAX_RESOURCE_BYTES > MAX_TRANSFER_BYTES:
            return "Page load transfer budget exceeded."
        if state.get("transfer_reserved_bytes", 0) + MAX_RESOURCE_BYTES > MAX_SCAN_TRANSFER_BYTES:
            return "Scan transfer budget exceeded."
        return None

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
                            reason: 'Browser policy blocked ' + event.effectiveDirective + '.',
                            directive: event.effectiveDirective, source: event.sourceFile});
                    }
                });
            })();""")
            session = await context.new_cdp_session(page)
            chains = {}
            pending = set()
            page_issues = []
            page._arsune_resource_issues = page_issues
            page._arsune_pending = pending
            # Image requests by the URL the element asked for, which survives redirects.
            images, starts = {}, {}
            page._arsune_images = images
            errors, revoked = [], set()
            page._arsune_errors = (errors, revoked)
            # Bytes reserved or transferred by this page's one load.
            load_bytes = 0

            def thrown(event):
                # One record past the bound marks the list as truncated.
                if len(errors) <= MAX_PAGE_ERRORS:
                    errors.append(exception_record(event.get("exceptionDetails", {})))

            # A rejection that gets a handler later is not an unhandled rejection.
            session.on("Runtime.exceptionThrown", thrown)
            session.on("Runtime.exceptionRevoked", lambda event: revoked.add(event["exceptionId"]))
            await session.send("Runtime.enable")

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
                nonlocal in_flight, load_bytes
                request = event["request"]
                target = request["url"]
                kind = event.get("resourceType", "Other")
                request_id = event["requestId"]
                start = starts[request_id] = starts.get(event.get("redirectedRequestId"), target)
                reserved = consumed = 0
                status = failure = None
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
                    async with budget:
                        # A full budget stops a request only when no request in flight can
                        # return part of its reservation; a busy page waits instead.
                        await budget.wait_for(
                            lambda: not exceeded_budget(load_bytes) or not in_flight
                        )
                        if exceeded := exceeded_budget(load_bytes):
                            raise TargetPolicyError(exceeded)
                        reserved = MAX_RESOURCE_BYTES
                        in_flight += 1
                        load_bytes += reserved
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
                        status = response.status_code
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
                        encodings = response.headers.get_list("content-encoding", split_commas=True)
                        if any(e.strip().lower() not in DECODED_ENCODINGS for e in encodings):
                            raise TargetPolicyError(
                                "Resource uses an unsupported content encoding."
                            )
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            consumed += len(chunk)
                            if len(body) > MAX_RESOURCE_BYTES:
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
                    failure = str(exc)
                    issue = {
                        "url": target,
                        "page_url": page.url,
                        "resource_type": kind,
                        "reason": failure,
                    }
                    if status is not None and status >= 400:
                        # The site answered with this error, so a visitor receives it too.
                        issue["http_status"] = status
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
                    if kind == "Image" and (start in images or len(images) < MAX_IMAGE_REQUESTS):
                        images[start] = {"final_url": target, "status": status, "failure": failure}
                    if reserved:
                        async with budget:
                            unused = max(0, reserved - consumed)
                            load_bytes -= unused
                            state["transfer_reserved_bytes"] -= unused
                            in_flight -= 1
                            budget.notify_all()
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
