import asyncio
import socket
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from playwright.async_api import Error as BrowserError
from playwright.async_api import async_playwright

from app.browser.policy import TargetPolicyError, origin, validate_target


@asynccontextmanager
async def inspection_browser(url, demo, state, save):
    requests = state.get("request_count", 0)
    transferred = state.get("transfer_reserved_bytes", 0)
    blocked = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=["--disable-background-networking", "--disable-quic"]
        )
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
                    parts.hostname,
                    parts.port or (443 if parts.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
                address = records[0][4][0]
                import ipaddress

                if origin(target) != origin(demo) and any(
                    not ipaddress.ip_address(record[4][0]).is_global for record in records
                ):
                    raise TargetPolicyError("Resolved destination is private.")
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
                headers = {"host": parts.netloc, "accept": request.headers.get("accept", "*/*")}
                async with network.stream(
                    "GET", endpoint, headers=headers, extensions={"sni_hostname": parts.hostname}
                ) as response:
                    if 300 <= response.status_code < 400 and response.headers.get("location"):
                        destination = urljoin(target, response.headers["location"])
                        if len(blocked) < 100:
                            blocked.append(destination)
                        # Never hand a redirect to Chromium: redirect follow-up
                        # requests can bypass Playwright's initial route callback.
                        raise TargetPolicyError(
                            "Redirects require a separate selected-page inspection."
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        consumed += len(chunk)
                        if len(body) > 2_000_000:
                            raise TargetPolicyError("Resource budget exceeded.")
                    response_headers = {
                        k: v
                        for k, v in response.headers.items()
                        if k
                        not in {
                            "content-length",
                            "content-encoding",
                            "transfer-encoding",
                            "set-cookie",
                        }
                    }
                    await route.fulfill(
                        status=response.status_code, headers=response_headers, body=bytes(body)
                    )
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
        try:
            yield context, blocked
        finally:
            await network.aclose()
            await context.close()
            await browser.close()
