#!/usr/bin/env python3
"""Obtain a short-lived Supabase access token for a dedicated test user, then
run the read-only production smoke.

Required environment (never pass secrets on the command line):
  EVIDENCE_SERVICE_URL
  SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL)
  SUPABASE_PUBLISHABLE_KEY (or NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY)
  SMOKE_USER_EMAIL
  SMOKE_USER_PASSWORD

The access token stays in process memory, is never printed, and is removed
from the environment before exit. Use a test account only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import production_smoke


def _password_grant(base_url: str, api_key: str, email: str, password: str) -> tuple[int, dict]:
    payload = json.dumps({"email": email, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/auth/v1/token?grant_type=password",
        data=payload,
        headers={"apikey": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read(8192) or b"{}")
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read(8192) or b"{}")
        except (ValueError, OSError):
            body = {}
        return error.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, {"error": type(error).__name__}


def main() -> int:
    base_url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "")
    api_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "")
    email = os.environ.get("SMOKE_USER_EMAIL", "")
    password = os.environ.get("SMOKE_USER_PASSWORD", "")
    if not base_url or not api_key or not email or not password:
        print(
            "SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SMOKE_USER_EMAIL and SMOKE_USER_PASSWORD are required",
            file=sys.stderr,
        )
        return 2

    status, body = _password_grant(base_url, api_key, email, password)
    token = body.get("access_token") if isinstance(body, dict) else None
    if status != 200 or not isinstance(token, str) or not token:
        code = body.get("error_code") or body.get("error") or "auth_failed"
        print(f"token grant failed: HTTP {status} {code}", file=sys.stderr)
        return 1

    os.environ["SMOKE_ACCESS_TOKEN"] = token
    try:
        return production_smoke.main()
    finally:
        os.environ.pop("SMOKE_ACCESS_TOKEN", None)


if __name__ == "__main__":
    raise SystemExit(main())
