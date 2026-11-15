#!/usr/bin/env python3
"""Reconcile human A/B/Tie/Both-bad votes with a private comparison key."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

VOTES = ("A", "B", "Tie", "Both bad")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_votes(path: Path, key_pairs: dict[str, Any]) -> dict[str, str]:
    votes: dict[str, str] = {}
    if not path.is_file():
        raise SystemExit(f"reviewer file not found: {path}")
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        pair_token = row.get("pair_token")
        vote = row.get("vote")
        if pair_token not in key_pairs:
            raise SystemExit(f"unknown pair_token at {path}:{line_number}")
        if vote not in VOTES:
            raise SystemExit(f"invalid vote at {path}:{line_number}: {vote!r}")
        if pair_token in votes:
            raise SystemExit(f"duplicate vote at {path}:{line_number}")
        votes[pair_token] = vote
    return votes


def resolve(a: str | None, b: str | None, third: str | None) -> tuple[str | None, str]:
    if a is None or b is None:
        return None, "missing"
    if a == b:
        return a, "agreement"
    if third is None:
        return None, "third_required"
    if third == a or third == b:
        return third, "third_majority"
    return None, "panel_required"


def chosen_route(vote: str | None, entry: dict[str, Any]) -> str | None:
    if vote == "A":
        return entry["left_route"]
    if vote == "B":
        return entry["right_route"]
    if vote == "Tie":
        return "tie"
    if vote == "Both bad":
        return "both_bad"
    return None


def reconcile(
    *,
    key: dict[str, Any],
    reviewer_a: dict[str, str],
    reviewer_b: dict[str, str],
    third: dict[str, str] | None = None,
) -> dict[str, Any]:
    third = third or {}
    key_pairs = key.get("pairs", {})
    resolved: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    agreement = 0
    comparable = 0
    route_counts: Counter[str] = Counter()
    provider_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for pair_token, entry in sorted(key_pairs.items()):
        a = reviewer_a.get(pair_token)
        b = reviewer_b.get(pair_token)
        t = third.get(pair_token)
        if a is not None and b is not None:
            comparable += 1
            if a == b:
                agreement += 1
        vote, method = resolve(a, b, t)
        route = chosen_route(vote, entry)
        row = {
            "pair_token": pair_token,
            "case_id": entry["case_id"],
            "provider": entry["provider"],
            "reviewer_a": a,
            "reviewer_b": b,
            "reviewer_third": t,
            "method": method,
            "vote": vote,
            "chosen_route": route,
        }
        if route is None:
            pending.append(row)
            continue
        resolved.append(row)
        route_counts[route] += 1
        provider_counts[entry["provider"]][route] += 1

    return {
        "schema_version": 1,
        "summary": {
            "pairs": len(key_pairs),
            "comparable": comparable,
            "exact_agreement": agreement,
            "exact_agreement_rate": round(agreement / comparable, 4) if comparable else 0.0,
            "resolved": len(resolved),
            "pending": len(pending),
            "routes": dict(sorted(route_counts.items())),
        },
        "by_provider": {
            provider: dict(sorted(counts.items()))
            for provider, counts in sorted(provider_counts.items())
        },
        "resolved": resolved,
        "pending": pending,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--reviewer-a", type=Path, required=True)
    parser.add_argument("--reviewer-b", type=Path, required=True)
    parser.add_argument("--third", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--strict", action="store_true", help="fail when any pair remains unresolved")
    args = parser.parse_args(argv)

    key = load_json(args.key)
    key_pairs = key.get("pairs")
    if not isinstance(key_pairs, dict) or not key_pairs:
        print("key has no pairs", file=sys.stderr)
        return 2
    reviewer_a = load_votes(args.reviewer_a, key_pairs)
    reviewer_b = load_votes(args.reviewer_b, key_pairs)
    third = load_votes(args.third, key_pairs) if args.third else {}
    report = reconcile(key=key, reviewer_a=reviewer_a, reviewer_b=reviewer_b, third=third)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"wrote: {args.output}")
    if args.strict and report["summary"]["pending"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
