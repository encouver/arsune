"""Destination policy shared by the API and the browser."""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit


class TargetPolicyError(ValueError):
    pass


def origin(url: str) -> str:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{parts.hostname}:{port}"


async def validate_target(url: str, demo_origin: str | None = None) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise TargetPolicyError("Use a complete HTTP or HTTPS URL.")
        if parts.username is not None or parts.password is not None:
            raise TargetPolicyError("URLs with credentials are not permitted.")
        if len(url) > 2048 or any(ord(c) < 33 for c in url) or "\\" in url:
            raise TargetPolicyError("The URL contains unsupported characters.")
        target_origin = origin(url)
        from arsune.config import settings
        demo = demo_origin or settings.demo_url
        if target_origin != origin(demo):
            records = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                ), timeout=5,
            )
            if not records or any(not ipaddress.ip_address(r[4][0]).is_global for r in records):
                raise TargetPolicyError("Private and local network destinations are not permitted.")
        return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
    except (ValueError, OSError, TimeoutError) as exc:
        if isinstance(exc, TargetPolicyError):
            raise
        raise TargetPolicyError("The destination cannot be validated.") from exc
