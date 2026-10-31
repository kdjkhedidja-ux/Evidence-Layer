#!/usr/bin/env python3
"""Validate blind relevance reviews and resolve disagreements without averaging.

Reviewer JSONL rows have this form:
{"case_token":"case_...","candidate_token":"item_...","grade":0}
The third reviewer sees only the question and passage text. If all three grades
differ, a separate panel adjudication is required and recorded with a rationale.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return rows


def _grade_map(
    rows: list[dict], expected: set[tuple[str, str]], label: str
) -> dict[tuple[str, str], int]:
    result: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row.get("case_token"), row.get("candidate_token"))
        grade = row.get("grade")
        if key not in expected:
            raise ValueError(f"{label} contains an unknown case/candidate token: {key}")
        if key in result:
            raise ValueError(f"{label} has a duplicate review for: {key}")
        if isinstance(grade, bool) or not isinstance(grade, int) or grade not in (0, 1, 2):
            raise ValueError(f"{label} has an invalid grade for: {key}")
        result[key] = grade
    missing = expected - set(result)
    if missing:
        raise ValueError(f"{label} is missing {len(missing)} candidate grades")
    return result


def reconcile_reviews(
    key: dict,
    reviewer_a: list[dict],
    reviewer_b: list[dict],
    third_review: list[dict] | None = None,
    panel_adjudications: list[dict] | None = None,
) -> dict:
    if key.get("private_coordinator_key") is not True:
        raise ValueError("key is not marked as a private coordinator key")
    case_map = key.get("cases", {})
    expected = {
        (case_token, candidate_token)
        for case_token, case in case_map.items()
        for candidate_token in case["candidates"]
    }
    a = _grade_map(reviewer_a, expected, "reviewer A")
    b = _grade_map(reviewer_b, expected, "reviewer B")
    disagreements = {token for token in expected if a[token] != b[token]}
    third = _grade_map(third_review, disagreements, "third reviewer") if third_review is not None else {}
    panel_needed = {
        token for token in disagreements
        if token in third and third[token] not in {a[token], b[token]}
    }

    panel: dict[tuple[str, str], int] = {}
    panel_rationales: dict[tuple[str, str], str] = {}
    if panel_adjudications is not None:
        panel = _grade_map(panel_adjudications, panel_needed, "panel adjudication")
        for row in panel_adjudications:
            token = (row["case_token"], row["candidate_token"])
            rationale = row.get("rationale")
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError(f"panel adjudication requires a rationale for: {token}")
            panel_rationales[token] = rationale.strip()

    rows = []
    third_review_queue = []
    panel_queue = []
    for case_token, candidate_token in sorted(expected):
        case = case_map[case_token]
        item = case["candidates"][candidate_token]
        token = (case_token, candidate_token)
        if token not in disagreements:
            resolved = a[token]
            resolution = "two_reviewer_agreement"
        elif token not in third:
            resolved = None
            resolution = "pending_third_review"
            third_review_queue.append(
                {
                    "case_token": case_token,
                    "candidate_token": candidate_token,
                    "question": case["question"],
                    "text": item["text"],
                }
            )
        elif token in panel_needed and token not in panel:
            resolved = None
            resolution = "pending_panel_adjudication"
            panel_queue.append(
                {
                    "case_token": case_token,
                    "candidate_token": candidate_token,
                    "question": case["question"],
                    "text": item["text"],
                    "reviewer_a_grade": a[token],
                    "reviewer_b_grade": b[token],
                    "third_reviewer_grade": third[token],
                }
            )
        elif token in panel_needed:
            resolved = panel[token]
            resolution = "panel_adjudicated"
        else:
            resolved = third[token]
            resolution = "majority_after_blind_third_review"

        rows.append(
            {
                "case_id": case["case_id"],
                "case_type": case["case_type"],
                "candidate_id": item["candidate_id"],
                "source_id": item["source_id"],
                "developer_grade": item["developer_grade"],
                "reviewer_a_grade": a[token],
                "reviewer_b_grade": b[token],
                "third_reviewer_grade": third.get(token),
                "panel_grade": panel.get(token),
                "panel_rationale": panel_rationales.get(token),
                "independent_grade": resolved,
                "resolution": resolution,
            }
        )

    unresolved = len(third_review_queue) + len(panel_queue)
    return {
        "schema_version": 1,
        "review_batch": key["review_batch"],
        "candidate_count": len(expected),
        "agreement_count": len(expected) - len(disagreements),
        "two_reviewer_disagreement_count": len(disagreements),
        "agreement_rate": (len(expected) - len(disagreements)) / len(expected) if expected else 1.0,
        "third_review_count": len(third),
        "panel_adjudication_count": len(panel),
        "adjudication_status": "complete" if unresolved == 0 else "pending",
        "disagreements_for_third_review": third_review_queue,
        "disagreements_for_panel": panel_queue,
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=Path, required=True, help="private coordinator key")
    parser.add_argument("--reviewer-a", type=Path, required=True)
    parser.add_argument("--reviewer-b", type=Path, required=True)
    parser.add_argument("--third-review", type=Path, help="third reviewer JSONL; only two-review disagreements")
    parser.add_argument("--panel-adjudication", type=Path, help="panel JSONL for three-way splits; include rationale")
    parser.add_argument("--output", type=Path, required=True, help="coordinator-only reconciliation report")
    args = parser.parse_args()
    key = json.loads(args.key.read_text())
    third = _read_jsonl(args.third_review) if args.third_review else None
    panel = _read_jsonl(args.panel_adjudication) if args.panel_adjudication else None
    result = reconcile_reviews(
        key,
        _read_jsonl(args.reviewer_a),
        _read_jsonl(args.reviewer_b),
        third,
        panel,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        f"agreement={result['agreement_rate']:.3f}; "
        f"two-review disagreements={result['two_reviewer_disagreement_count']}; "
        f"third reviews={result['third_review_count']}; "
        f"panel adjudications={result['panel_adjudication_count']}; "
        f"status={result['adjudication_status']}"
    )


if __name__ == "__main__":
    main()
