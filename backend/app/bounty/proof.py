"""Read the owner's proof file under the shared destination policy."""

import asyncio
import hmac
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.browser.policy import TargetPolicyError, origin, validate_target

PROOF_PATH = "/.well-known/arsune-bounty.txt"
MAX_PROOF_BYTES = 4096
MAX_REDIRECTS = 3


class ProofError(ValueError):
    pass


async def proof_matches(site_url: str, token: str) -> bool:
    try:
        async with asyncio.timeout(10):
            body = await fetch_proof(site_url)
    except TargetPolicyError as exc:
        raise ProofError(f"The proof file could not be read: {exc}") from exc
    except (TimeoutError, httpx.HTTPError, OSError) as exc:
        raise ProofError("The proof file could not be read in time.") from exc
    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        return False
    return hmac.compare_digest(text.encode(), token.encode())


def proof_url(site_url: str) -> str:
    parts = urlsplit(site_url)
    return urlunsplit((parts.scheme, parts.netloc, PROOF_PATH, "", ""))


async def fetch_proof(site_url: str) -> bytes:
    from app.core.config import settings

    target = await validate_target(proof_url(site_url))
    async with httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(target)
            port = parts.port or (443 if parts.scheme == "https" else 80)
            records = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    parts.hostname, port, type=socket.SOCK_STREAM
                ),
                5,
            )
            # Connect to the address that passed the check, as the browser session does.
            if not records or (
                origin(target) != origin(settings.demo_url)
                and any(not ipaddress.ip_address(r[4][0]).is_global for r in records)
            ):
                raise TargetPolicyError("Resolved destination is private.")
            address = records[0][4][0]
            host = f"[{address}]" if ":" in address else address
            endpoint = urlunsplit((parts.scheme, f"{host}:{port}", parts.path, parts.query, ""))
            async with client.stream(
                "GET",
                endpoint,
                headers={"host": parts.netloc, "accept": "text/plain"},
                extensions={"sni_hostname": parts.hostname},
            ) as response:
                location = response.headers.get("location")
                if 300 <= response.status_code < 400 and location:
                    destination = await validate_target(urljoin(target, location))
                    if origin(destination) != origin(site_url):
                        raise TargetPolicyError("The proof file redirects to another origin.")
                    target = destination
                    continue
                if response.status_code != 200:
                    raise TargetPolicyError(f"The proof file returned HTTP {response.status_code}.")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_PROOF_BYTES:
                        raise TargetPolicyError("The proof file exceeds the size limit.")
                return bytes(body)
    raise TargetPolicyError("The proof file exceeds the redirect limit.")
