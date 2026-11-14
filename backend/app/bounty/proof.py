"""Read the owner's proof (a file, or a meta tag on the home page) under the shared destination policy."""

import asyncio
import hmac
import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.browser.policy import TargetPolicyError, origin, validate_target

PROOF_PATH = "/.well-known/arsune-bounty.txt"
MAX_PROOF_BYTES = 4096
MAX_PAGE_BYTES = 512 * 1024
MAX_REDIRECTS = 3
META_NAME = "arsune-bounty"
NO_PROOF = (
    f"Arsune found no proof: no file at {PROOF_PATH} and no {META_NAME} meta tag on the home page."
)


class ProofError(ValueError):
    pass


async def proof_matches(site_url: str, token: str) -> bool:
    """True if the proof file or the home page meta tag holds the token. Else ProofError."""
    try:
        body = await read(site_url, PROOF_PATH, MAX_PROOF_BYTES)
        if same(body.decode("utf-8").strip(), token):
            return True
        file_problem = "The proof file does not contain this bounty's token."
    except UnicodeDecodeError:
        file_problem = "The proof file is not UTF-8 text."
    except ProofError as exc:
        file_problem = str(exc)
    try:
        page = await read(site_url, "/", MAX_PAGE_BYTES)
        if any(same(found, token) for found in meta_tokens(page)):
            return True
    except ProofError:
        pass
    raise ProofError(f"{NO_PROOF} {file_problem}")


async def read(site_url: str, path: str, limit: int) -> bytes:
    try:
        async with asyncio.timeout(10):
            return await fetch_proof(site_url, path, limit)
    except TargetPolicyError as exc:
        raise ProofError(f"The proof file could not be read: {exc}") from exc
    except (TimeoutError, httpx.HTTPError, OSError) as exc:
        raise ProofError("The proof file could not be read in time.") from exc


def same(found: str, token: str) -> bool:
    return hmac.compare_digest(found.encode(), token.encode())


class MetaFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tokens = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and (values.get("name") or "").strip().lower() == META_NAME:
            self.tokens.append((values.get("content") or "").strip())


def meta_tokens(page: bytes) -> list[str]:
    finder = MetaFinder()
    finder.feed(page.decode("utf-8", errors="replace"))
    return finder.tokens


def proof_url(site_url: str, path: str = PROOF_PATH) -> str:
    parts = urlsplit(site_url)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


async def fetch_proof(site_url: str, path: str = PROOF_PATH, limit: int = MAX_PROOF_BYTES) -> bytes:
    """GET a path on the site's origin. A body above the limit is an error for the file;
    a home page is cut at the limit, since its head comes first."""
    from app.core.config import settings

    target = await validate_target(proof_url(site_url, path))
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
                headers={
                    "host": parts.netloc,
                    "accept": "text/plain" if path == PROOF_PATH else "text/html",
                },
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
                    if len(body) > limit:
                        if path != PROOF_PATH:
                            return bytes(body[:limit])
                        raise TargetPolicyError("The proof file exceeds the size limit.")
                return bytes(body)
    raise TargetPolicyError("The proof file exceeds the redirect limit.")
