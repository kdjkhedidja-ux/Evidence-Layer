#!/usr/bin/env python3
"""Create a blind relevance-review pack and a separate coordinator key.

The public pack deliberately omits passage IDs, source metadata, case types,
required facts, gold IDs, and developer-authored grades. Keep the key private.
No network access is used.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
from pathlib import Path
from random import Random

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_real_world_dataset as rw  # noqa: E402

SCALE = {
    "0": "not relevant to the question",
    "1": "partially relevant; supports only part of an answer",
    "2": "directly relevant; supports the answer",
}


def _token(seed: int, namespace: str, value: str) -> str:
    key = seed.to_bytes(max(1, (seed.bit_length() + 7) // 8), "big")
    digest = hmac.new(key, f"{namespace}:{value}".encode(), hashlib.sha256).hexdigest()
    return f"{namespace}_{digest[:16]}"


def build_review_artifacts(cases: list[dict], seed: int) -> tuple[dict, dict]:
    """Return a reviewer-safe pack and a private mapping key."""
    if seed < 0:
        raise ValueError("seed must be non-negative")

    ordered_cases = list(cases)
    rng = Random(seed)
    rng.shuffle(ordered_cases)
    public_cases: list[dict] = []
    private_cases: dict[str, dict] = {}

    for case in ordered_cases:
        case_token = _token(seed, "case", case["case_id"])
        candidates = list(case["candidates"])
        rng.shuffle(candidates)
        public_candidates: list[dict] = []
        candidate_map: dict[str, dict] = {}
        for candidate in candidates:
            candidate_token = _token(
                seed,
                "item",
                f"{case['case_id']}\x00{candidate['candidate_id']}",
            )
            public_candidates.append(
                {"candidate_token": candidate_token, "text": candidate["text"]}
            )
            candidate_map[candidate_token] = {
                "candidate_id": candidate["candidate_id"],
                "source_id": candidate["source_id"],
                "source_url": candidate["source_url"],
                "section": candidate.get("metadata", {}).get("section"),
                "developer_grade": case["relevance_scores"][candidate["candidate_id"]],
                "text": candidate["text"],
            }
        public_cases.append(
            {
                "case_token": case_token,
                "question": case["task"]["input"],
                "candidates": public_candidates,
            }
        )
        private_cases[case_token] = {
            "case_id": case["case_id"],
            "case_type": case["case_type"],
            "required_facts": case["required_facts"],
            "question": case["task"]["input"],
            "candidates": candidate_map,
        }

    batch_id = hashlib.sha256(
        "\n".join(sorted(row["case_token"] for row in public_cases)).encode()
    ).hexdigest()[:16]
    pack = {
        "schema_version": 1,
        "review_batch": f"real-world-v1-{batch_id}",
        "instructions": (
            "Judge every candidate independently using only its question and text. "
            "Do not consult prior grades or discuss ratings with other reviewers."
        ),
        "scale": SCALE,
        "cases": public_cases,
    }
    key = {
        "schema_version": 1,
        "review_batch": pack["review_batch"],
        "shuffle_seed_private": str(seed),
        "private_coordinator_key": True,
        "cases": private_cases,
    }
    return pack, key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True, help="reviewer-facing JSON output")
    parser.add_argument("--key", type=Path, required=True, help="private coordinator key; do not share")
    parser.add_argument("--seed", type=int, help="private shuffle seed; random if omitted")
    args = parser.parse_args()
    if args.pack.resolve() == args.key.resolve():
        parser.error("--pack and --key must be different paths")

    seed = args.seed if args.seed is not None else secrets.randbits(256)
    cases = [case for split in rw.build_real_world_cases() for case in split]
    pack, key = build_review_artifacts(cases, seed)
    args.pack.parent.mkdir(parents=True, exist_ok=True)
    args.key.parent.mkdir(parents=True, exist_ok=True)
    args.pack.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n")
    args.key.write_text(json.dumps(key, indent=2, ensure_ascii=False) + "\n")
    os.chmod(args.key, 0o600)
    print(f"wrote blind review pack ({len(pack['cases'])} cases): {args.pack}")
    print(f"wrote private coordinator key; do not share: {args.key}")


if __name__ == "__main__":
    main()
