import asyncio
import base64
import ipaddress
import re
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
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


# Logged-in sessions for the security test: log in as test account A once, then replay steps.

LOGIN_SECONDS = 15
SETTLE_SECONDS = 10
STEP_SECONDS = 10
MAX_STEP_BYTES = 256 * 1024
MAX_STEP_REDIRECTS = 3
# Second-level labels under which a registrable domain has three labels (co.uk, com.br, ...).
SHORT_SUFFIX_PARTS = {"co", "com", "net", "org", "gov", "edu", "ac", "gob", "or", "ne", "go"}
EMAIL_INPUTS = ", ".join(
    [
        "input[type=email]",
        "input[autocomplete~=username]",
        "input[autocomplete~=email]",
        "input[name=identifier]",
    ]
    + [f"input[{attr}*={word} i]" for word in ("email", "user", "login") for attr in ("name", "id")]
)
PASSWORD_INPUTS = "input[type=password]"
SUBMIT_BUTTONS = "button[type=submit], input[type=submit]"
SUBMIT_NAMES = re.compile(
    r"^\s*(sign in|log in|login|continue|next|submit|iniciar sesi[oó]n|entrar)\s*$", re.IGNORECASE
)


class LoginFailed(Exception):
    """Arsune could not log in as test account A; the message is a plain reason."""


@dataclass
class Session:
    storage_state: dict
    auth_headers: dict[str, str]  # host -> Authorization value
    home_url: str


def scope_base(host: str) -> str:
    """The registrable domain of a host: staging.personaizit.com -> personaizit.com."""
    host = (host or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SHORT_SUFFIX_PARTS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def in_scope(url: str, base: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    return (
        parts.scheme in ("http", "https")
        and bool(base)
        and (host == base or host.endswith("." + base))
    )


def _page_key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


async def _allowed(url: str, base: str, extra_origin: str | None = None) -> bool:
    if not (in_scope(url, base) or (extra_origin and origin(url) == origin(extra_origin))):
        return False
    try:
        await validate_target(url)
    except TargetPolicyError:
        return False
    return True


async def _scoped_context(browser, base: str, headers: dict[str, str], **options):
    """A context that blocks private destinations and records Authorization headers in scope."""
    context = await browser.new_context(service_workers="block", accept_downloads=False, **options)
    verdicts: dict[str, bool] = {}

    async def guard(route):
        url = route.request.url
        if urlsplit(url).scheme not in ("http", "https"):
            await route.continue_()
            return
        key = origin(url)
        if key not in verdicts:
            try:
                await validate_target(url)
                verdicts[key] = True
            except TargetPolicyError:
                verdicts[key] = False
        await (route.continue_() if verdicts[key] else route.abort("blockedbyclient"))

    def record(request):
        value = request.headers.get("authorization")
        if value and in_scope(request.url, base):
            headers[urlsplit(request.url).hostname] = value

    await context.route("**/*", guard)
    await context.route_web_socket("**/*", lambda socket: socket.close())
    context.on("request", record)
    return context


async def _settle(page, seconds=SETTLE_SECONDS):
    try:
        await page.wait_for_load_state("networkidle", timeout=seconds * 1000)
    except (BrowserError, TimeoutError):
        pass


async def _first_visible(page, selector):
    items = page.locator(selector)
    for n in range(min(await items.count(), 20)):
        item = items.nth(n)
        if await item.is_visible():
            return item
    return None


async def _submit(page, field):
    button = await _first_visible(page, SUBMIT_BUTTONS)
    if button is None:
        buttons = page.locator("button, [role=button]")
        for n in range(min(await buttons.count(), 30)):
            item = buttons.nth(n)
            if await item.is_visible() and SUBMIT_NAMES.match(await item.inner_text()):
                button = item
                break
    if button:
        await button.click(timeout=5000)
    else:
        await field.press("Enter")


async def login_session(browser, login_url: str, email: str, password: str, base: str) -> Session:
    """Log in through the site's own login page and keep cookies, storage and tokens."""
    if not in_scope(login_url, base) or not await _allowed(login_url, base):
        raise LoginFailed("The login page is not on the bounty site.")
    headers: dict[str, str] = {}
    context = await _scoped_context(browser, base, headers)
    try:
        page = await context.new_page()
        try:
            await page.goto(login_url, timeout=LOGIN_SECONDS * 1000)
        except (BrowserError, TimeoutError) as exc:
            raise LoginFailed("The login page did not load.") from exc
        await _settle(page, LOGIN_SECONDS)
        login_page = _page_key(page.url)
        user_field = await _first_visible(page, f":is({EMAIL_INPUTS}):not([type=password])")
        secret_field = await _first_visible(page, PASSWORD_INPUTS)
        if user_field:
            await user_field.fill(email)
            if secret_field is None:
                # Two-step forms (Clerk and others) show the password after Continue.
                await _submit(page, user_field)
                try:
                    await page.locator(f"{PASSWORD_INPUTS} >> visible=true").first.wait_for(
                        timeout=LOGIN_SECONDS * 1000
                    )
                except (BrowserError, TimeoutError):
                    pass
                secret_field = await _first_visible(page, PASSWORD_INPUTS)
        if secret_field is None:
            raise LoginFailed("No password field was found on the login page.")
        await secret_field.fill(password)
        await _submit(page, secret_field)
        try:
            await page.wait_for_url(
                lambda url: _page_key(url) != login_page, timeout=LOGIN_SECONDS * 1000
            )
        except (BrowserError, TimeoutError) as exc:
            raise LoginFailed(
                "Still on the login page after the form was sent. Check the email and password."
            ) from exc
        await _settle(page)
        home_url = page.url
        if not in_scope(home_url, base):
            raise LoginFailed("The login went to a page that is not on the bounty site.")
        return Session(await context.storage_state(), dict(headers), home_url)
    finally:
        await context.close()


async def _fetch(context, url, method, body, headers, base, site_origin):
    """One request with the context's cookies; follows redirects only inside the scope."""
    for _ in range(MAX_STEP_REDIRECTS + 1):
        if not await _allowed(url, base, site_origin):
            raise ValueError("A step or its redirect left the bounty site.")
        options = {"method": method, "max_redirects": 0, "timeout": STEP_SECONDS * 1000}
        token = headers.get(urlsplit(url).hostname)
        if token:
            options["headers"] = {"Authorization": token}
        if method == "POST" and body is not None:
            options["data"] = body
        response = await context.request.fetch(url, **options)
        try:
            location = response.headers.get("location")
            if response.status in (301, 302, 303, 307, 308) and location:
                url = urljoin(url, location)
                if response.status in (301, 302, 303):
                    method, body = "GET", None
                continue
            length = int(response.headers.get("content-length") or 0)
            raw = b"" if length > MAX_STEP_BYTES else (await response.body())[:MAX_STEP_BYTES]
            return url, response.status, raw.decode("utf-8", "replace")
        finally:
            await response.dispose()
    raise ValueError("A step redirected too many times.")


def step_url(step: dict, site_origin: str) -> str:
    return step.get("url") or urljoin(site_origin.rstrip("/") + "/", step.get("path") or "/")


async def replay_steps(
    browser, session: Session, steps: list[dict], base: str, origin: str
) -> list[dict]:
    """Replay steps logged in as A. Each result: method, url, status, body (and error if any)."""
    site_origin = origin
    headers = dict(session.auth_headers)
    context = await _scoped_context(browser, base, headers, storage_state=session.storage_state)
    results = []
    try:
        if await _allowed(session.home_url, base, site_origin):
            # The app's own scripts refresh short-lived tokens; the newest header wins.
            page = await context.new_page()
            try:
                await page.goto(session.home_url, timeout=LOGIN_SECONDS * 1000)
                await _settle(page)
            except (BrowserError, TimeoutError):
                pass
            await page.close()
        for step in steps:
            url = step_url(step, site_origin)
            result = {"method": step["method"], "url": url, "status": None, "body": ""}
            try:
                async with asyncio.timeout(STEP_SECONDS + 1):
                    _, result["status"], result["body"] = await _fetch(
                        context, url, step["method"], step.get("body"), headers, base, site_origin
                    )
            except ValueError as exc:
                result["error"] = str(exc)
            except (BrowserError, TimeoutError):
                result["error"] = "The step did not finish."
            results.append(result)
        return results
    finally:
        await context.close()
