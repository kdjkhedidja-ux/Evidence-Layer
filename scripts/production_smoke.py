#!/usr/bin/env python3
"""Read-only smoke test for the deployed Evidence Service.

Usage:
  EVIDENCE_SERVICE_URL=https://service.example \
  SMOKE_ACCESS_TOKEN=<test-user-jwt> \
  python scripts/production_smoke.py [--workspace-id UUID] [--json]

The token is read from the environment, never printed, and only sent to the
configured service. No write, deletion, or provider call is performed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

TIMEOUT_SECONDS = 15
MAX_BODY = 4096


def _get(url: str, token: str | None) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_BODY)
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read(MAX_BODY)
        status = error.code
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, {"error": type(error).__name__}
    try:
        return status, json.loads(raw.decode("utf-8") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, {"error": "non_json_response"}


def _check(name: str, status: int, body: Any) -> dict[str, Any]:
    return {"check": name, "status": status, "ok": 200 <= status < 300, "body": body}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Evidence Service smoke test")
    parser.add_argument("--workspace-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    service = os.environ.get("EVIDENCE_SERVICE_URL", "").rstrip("/")
    token = os.environ.get("SMOKE_ACCESS_TOKEN", "")
    if not service:
        print("EVIDENCE_SERVICE_URL is required", file=sys.stderr)
        return 2
    if not token:
        print("SMOKE_ACCESS_TOKEN is required", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    results.append(_check("healthz", *_get(f"{service}/healthz", None)))

    me_status, me_body = _get(f"{service}/v1/me", token)
    results.append(_check("me", me_status, {"user_id_present": bool(isinstance(me_body, dict) and me_body.get("user_id")),
                                            "workspace_count": len(me_body.get("workspaces", [])) if isinstance(me_body, dict) else None}))

    workspace_id = args.workspace_id
    if not workspace_id and isinstance(me_body, dict) and me_body.get("workspaces"):
        first = me_body["workspaces"][0]
        if isinstance(first, dict) and isinstance(first.get("id"), str):
            workspace_id = first["id"]

    if workspace_id:
        status, body = _get(f"{service}/v1/workspaces/{workspace_id}/evidence", token)
        count = len(body) if isinstance(body, list) else None
        results.append(_check("evidence_list", status, {"records": count}))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            print(f"{result['check']}: HTTP {result['status']} {'ok' if result['ok'] else 'FAIL'} {result['body']}")

    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
