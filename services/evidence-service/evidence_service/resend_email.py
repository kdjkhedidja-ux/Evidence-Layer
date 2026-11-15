"""Minimal Resend invitation delivery.

The API key is send-only in this environment. Content is limited to a one-time
invitation link; failures are swallowed and reported to the caller as a boolean
so invitation creation never depends on provider availability.
"""

from __future__ import annotations

import html
from urllib.parse import quote

import httpx

RESEND_ENDPOINT = "https://api.resend.com/emails"
_MAX_RESPONSE_BYTES = 16 * 1024


def _valid_from(value: object) -> bool:
    return (
        isinstance(value, str)
        and 3 <= len(value) <= 320
        and "@" in value
        and "\r" not in value
        and "\n" not in value
    )


async def send_invitation_email(
    *,
    api_key: str,
    from_email: str,
    base_url: str,
    to_email: str,
    token: str,
) -> bool:
    if (
        not isinstance(api_key, str)
        or not api_key.startswith("re_")
        or not _valid_from(from_email)
        or not isinstance(base_url, str)
        or not base_url.startswith("https://")
        or not isinstance(to_email, str)
        or "@" not in to_email
        or not isinstance(token, str)
        or len(token) < 20
    ):
        return False
    link = f"{base_url.rstrip('/')}/invite/{quote(token, safe='')}"
    safe_link = html.escape(link)
    text = (
        "You were invited to join a workspace in Evidence Layer.\n\n"
        f"Open this one-time link to accept: {link}\n\n"
        "The link expires in seven days. If you were not expecting this, ignore it."
    )
    markup = (
        "<p>You were invited to join a workspace in Evidence Layer.</p>"
        f'<p><a href="{safe_link}">Accept the invitation</a></p>'
        "<p>The link expires in seven days. If you were not expecting this, ignore it.</p>"
    )
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=False, trust_env=False
        ) as client:
            async with client.stream(
                "POST",
                RESEND_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "evidence-layer-invitation/1.0",
                },
                json={
                    "from": from_email,
                    "to": [to_email],
                    "subject": "You're invited to Evidence Layer",
                    "text": text,
                    "html": markup,
                },
            ) as response:
                received = bytearray()
                async for chunk in response.aiter_bytes():
                    received.extend(chunk)
                    if len(received) > _MAX_RESPONSE_BYTES:
                        return False
                return response.status_code in {200, 201}
    except (httpx.HTTPError, OSError, ValueError):
        return False
