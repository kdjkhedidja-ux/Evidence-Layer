#!/usr/bin/env python3
"""Build a blinded A/B comparison pack from saved Phase 7 answer pairs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ROUTES = ("evidence_layer", "traditional_rag")
VOTES = ("A", "B", "Tie", "Both bad")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_questions(paths: list[Path]) -> dict[str, str]:
    questions: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"case file not found: {path}")
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            case_id = row.get("case_id")
            task = row.get("task") or {}
            question = task.get("input")
            if not isinstance(case_id, str) or not isinstance(question, str) or not question.strip():
                raise SystemExit(f"invalid case at {path}:{line_number}")
            if case_id in questions:
                raise SystemExit(f"duplicate case_id {case_id} in {path}")
            questions[case_id] = question.strip()
    return questions


def load_answer_pairs(report: dict[str, Any]) -> tuple[dict[tuple[str, str], dict[str, dict[str, Any]]], int]:
    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    skipped = 0
    for answer in report.get("answers", []):
        if not isinstance(answer, dict):
            skipped += 1
            continue
        provider = answer.get("provider")
        case_id = answer.get("case_id")
        route = answer.get("route")
        if not isinstance(provider, str) or not isinstance(case_id, str) or route not in ROUTES:
            skipped += 1
            continue
        if answer.get("status") != "ok" or not isinstance(answer.get("answer"), str) or not answer["answer"].strip():
            skipped += 1
            continue
        bucket = grouped.setdefault((provider, case_id), {})
        if route in bucket:
            raise SystemExit(f"duplicate answer for provider={provider} case={case_id} route={route}")
        bucket[route] = answer
    return grouped, skipped


def token(prefix: str, seed: int, *parts: str) -> str:
    digest = sha256_text("|".join([prefix, str(seed), *parts]))
    return f"{prefix}_{digest[:16]}"


def build_pack(
    *,
    report_path: Path,
    case_paths: list[Path],
    seed: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    report = load_json(report_path)
    questions = load_questions(case_paths)
    grouped, skipped = load_answer_pairs(report)
    pairs: list[dict[str, Any]] = []
    key_pairs: dict[str, dict[str, Any]] = {}
    skipped_pairs: list[str] = []
    for (provider, case_id), bucket in sorted(grouped.items()):
        if set(bucket) != set(ROUTES):
            skipped_pairs.append(f"{provider}/{case_id}: missing route")
            continue
        question = questions.get(case_id)
        if not question:
            skipped_pairs.append(f"{provider}/{case_id}: missing question")
            continue
        pair_token = token("pair", seed, provider, case_id)
        case_token = token("case", seed, provider, case_id)
        rng = random.Random(f"{seed}|{pair_token}")
        left_route = ROUTES[0] if rng.random() < 0.5 else ROUTES[1]
        right_route = ROUTES[1] if left_route == ROUTES[0] else ROUTES[0]
        left = bucket[left_route]["answer"].strip()
        right = bucket[right_route]["answer"].strip()
        pairs.append(
            {
                "pair_token": pair_token,
                "case_token": case_token,
                "question": question,
                "left": left,
                "right": right,
            }
        )
        key_pairs[pair_token] = {
            "case_id": case_id,
            "task_id": bucket[left_route].get("task_id"),
            "provider": provider,
            "left_route": left_route,
            "right_route": right_route,
            "left_answer_sha256": hashlib.sha256(left.encode("utf-8")).hexdigest(),
            "right_answer_sha256": hashlib.sha256(right.encode("utf-8")).hexdigest(),
        }
    pack = {
        "schema_version": SCHEMA_VERSION,
        "benchmark": report.get("benchmark", "phase7"),
        "instructions": (
            "Vote A, B, Tie, or Both bad for each pair. Judge only the answer against the question. "
            "Do not infer route or provider labels."
        ),
        "pairs": pairs,
    }
    key = {
        "schema_version": SCHEMA_VERSION,
        "seed": seed,
        "report_path": report_path.as_posix(),
        "report_sha256": sha256_file(report_path),
        "pairs": key_pairs,
    }
    summary = {
        "pairs": len(pairs),
        "skipped_answers": skipped,
        "skipped_pairs": skipped_pairs,
        "providers": sorted({entry["provider"] for entry in key_pairs.values()}),
    }
    return pack, key, summary


def write_private_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("reports/phase7_end_to_end_rag.json"))
    parser.add_argument(
        "--cases",
        nargs="+",
        type=Path,
        default=[
            Path("data/real_world_dev/cases.jsonl"),
            Path("data/real_world_validation/cases.jsonl"),
            Path("data/real_world_test/cases.jsonl"),
        ],
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if (args.pack.exists() or args.key.exists()) and not args.force:
        print("refusing to overwrite pack/key; pass --force", file=sys.stderr)
        return 2
    pack, key, summary = build_pack(report_path=args.report, case_paths=args.cases, seed=args.seed)
    if not pack["pairs"]:
        print("no comparable pairs found", file=sys.stderr)
        return 1
    write_private_json(args.pack, pack)
    write_private_json(args.key, key)
    print(json.dumps(summary, indent=2))
    print(f"wrote pack: {args.pack}")
    print(f"wrote key: {args.key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
