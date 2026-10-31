#!/usr/bin/env python3
"""Verify Real-World Benchmark v1 inputs, generated splits, and lexical results.

No network access and no JEV. `--write-manifest` writes only real-world split
files, the lexical report, and the independent real-world verification
manifest. It never writes the synthetic freeze manifest or synthetic
evaluation artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_real_world_dataset as rw  # noqa: E402
import run_real_world_evaluation as real_eval  # noqa: E402

MANIFEST_PATH = ROOT / "data" / "real_world" / "verification_manifest.json"
REPORT_PATH = real_eval.OUTPUT_PATH
SPLIT_OUTPUTS = {
    "data/real_world_dev/cases.jsonl": rw.DEV_OUT,
    "data/real_world_validation/cases.jsonl": rw.VALIDATION_OUT,
    "data/real_world_test/cases.jsonl": rw.TEST_OUT,
}
INPUT_FILES = [
    "data/real_world/passages.jsonl",
    "data/real_world/sources.json",
    "data/real_world/manifest.json",
    "data/real_world/NOTICE.md",
    "docs/phase6_real_world_benchmark.md",
    "docs/phase6_real_world_review_protocol.md",
    "scripts/build_real_world_dataset.py",
    "scripts/run_real_world_evaluation.py",
    "scripts/verify_real_world_benchmark.py",
    "scripts/prepare_real_world_review.py",
    "scripts/reconcile_real_world_review.py",
    "src/evidence_layer/metrics.py",
    "src/evidence_layer/scoring/lexical.py",
    "src/evidence_layer/schemas.py",
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(relative_path: str) -> str:
    return _sha256((ROOT / relative_path).read_bytes())


def _split_bytes(cases: list[dict]) -> bytes:
    return ("\n".join(json.dumps(case) for case in cases) + "\n").encode()


def _membership(splits: tuple[list[dict], list[dict], list[dict]]) -> dict[str, list[str]]:
    dev, validation, exposed_test = splits
    return {
        "dev": sorted(case["case_id"] for case in dev),
        "validation": sorted(case["case_id"] for case in validation),
        "exposed_test": sorted(case["case_id"] for case in exposed_test),
    }


def _validate_passages(passages: list[dict], sources: dict, dataset_manifest: dict) -> None:
    source_map = {row["source_id"]: row for row in sources["sources"]}
    ids = [row.get("passage_id") for row in passages]
    if len(ids) != len(set(ids)):
        raise ValueError("real-world passage IDs must be unique")
    if len(passages) != dataset_manifest["counts"]["passages"]:
        raise ValueError("passage count disagrees with data/real_world/manifest.json")
    for row in passages:
        if row.get("source_id") not in source_map:
            raise ValueError(f"unknown source for passage {row.get('passage_id')}")
        if not row.get("section") or not row.get("source_url"):
            raise ValueError(f"missing section/source URL for passage {row.get('passage_id')}")
        source = source_map[row["source_id"]]
        if row.get("license") != source.get("license") or row.get("license_reference") != source.get("license_reference"):
            raise ValueError(f"license metadata disagrees for passage {row['passage_id']}")
        if row.get("quote_status") not in {"verbatim", "paraphrase"}:
            raise ValueError(f"invalid quote_status for passage {row['passage_id']}")
        if row["quote_status"] == "paraphrase" and not row.get("quote_note"):
            raise ValueError(f"paraphrase has no note for passage {row['passage_id']}")
        if row["quote_status"] == "verbatim" and row.get("quote_note"):
            raise ValueError(f"verbatim passage has a paraphrase note: {row['passage_id']}")


def _validate_dataset_manifest(dataset_manifest: dict, splits: tuple[list[dict], list[dict], list[dict]]) -> None:
    dev, validation, exposed_test = splits
    counts = dataset_manifest["counts"]
    if counts.get("cases") != len(dev) + len(validation) + len(exposed_test):
        raise ValueError("case count disagrees with generated splits")
    for name, actual in (("dev", len(dev)), ("validation", len(validation)), ("held_out_test", len(exposed_test))):
        if counts.get(name) != actual:
            raise ValueError(f"{name} count disagrees with generated splits")
    if dataset_manifest.get("split", {}).get("seed") != rw.REAL_WORLD_SPLIT_SEED:
        raise ValueError("split seed disagrees with the real-world builder")
    membership = dataset_manifest.get("held_out_membership", [])
    if sorted(membership) != sorted(case["case_id"] for case in exposed_test):
        raise ValueError("held-out membership disagrees with the real-world builder")
    validation_membership = dataset_manifest.get("validation_membership", [])
    if sorted(validation_membership) != sorted(case["case_id"] for case in validation):
        raise ValueError("validation membership disagrees with the real-world builder")
    if "EXPOSED" not in dataset_manifest.get("held_out_status", ""):
        raise ValueError("held-out status must be explicitly exposed")
    if "blind test" not in dataset_manifest.get("usage", ""):
        raise ValueError("usage must warn against blind testing on the exposed split")
    if "developer-authored" not in dataset_manifest.get("judgment_protocol", ""):
        raise ValueError("judgment authorship must be explicit")
    expected_files = {
        "passages": "data/real_world/passages.jsonl",
        "sources": "data/real_world/sources.json",
        "notice": "data/real_world/NOTICE.md",
        "builder": "scripts/build_real_world_dataset.py",
    }
    for key, relative_path in expected_files.items():
        actual = _file_sha256(relative_path)
        if dataset_manifest.get("file_sha256", {}).get(key) != actual:
            raise ValueError(f"data/real_world/manifest.json fingerprint mismatch for {key}")


def build_manifest() -> dict:
    passages = [json.loads(line) for line in (ROOT / "data/real_world/passages.jsonl").read_text().splitlines() if line.strip()]
    sources = json.loads((ROOT / "data/real_world/sources.json").read_text())
    dataset_manifest = json.loads((ROOT / "data/real_world/manifest.json").read_text())
    _validate_passages(passages, sources, dataset_manifest)
    splits = rw.build_real_world_cases()
    _validate_dataset_manifest(dataset_manifest, splits)
    dev, validation, exposed_test = splits
    report_data = real_eval.report_bytes()
    split_files = {
        "data/real_world_dev/cases.jsonl": dev,
        "data/real_world_validation/cases.jsonl": validation,
        "data/real_world_test/cases.jsonl": exposed_test,
    }
    return {
        "schema_version": 1,
        "benchmark": "phase6-real-world-v1",
        "manifest_kind": "independent deterministic verification",
        "verified_content": {
            "passages": len(passages),
            "cases": len(dev) + len(validation) + len(exposed_test),
            "candidates_per_case": sorted({len(case["candidates"]) for split in splits for case in split}),
            "source_counts": {
                source_id: sum(row["source_id"] == source_id for row in passages)
                for source_id in sorted({row["source_id"] for row in passages})
            },
            "quote_status_counts": {
                status: sum(row["quote_status"] == status for row in passages)
                for status in ("verbatim", "paraphrase")
            },
            "source_ids": sorted(source["source_id"] for source in sources["sources"]),
        },
        "evaluation_protocol": {
            "scorer": "lexical_stopword_filtered",
            "metrics": ["average_precision", "mrr", "precision_at_k", "recall_at_k", "ndcg_at_k", "graded_ndcg_at_5"],
            "tie_break": "score descending, candidate_id ascending",
            "network": "none",
            "jev": "not run",
            "judgments": "developer-authored before scoring; carried forward after passage audit; independent blind review pending",
            "held_out_status": "exposed; not a blind test for future JEV comparison",
        },
        "split": {
            "seed": rw.REAL_WORLD_SPLIT_SEED,
            "dev_fraction": rw.DEV_FRACTION,
            "validation_fraction": rw.VALIDATION_FRACTION,
            "membership": _membership(splits),
        },
        "input_sha256": {name: _file_sha256(name) for name in INPUT_FILES},
        "generated_sha256": {
            **{name: _sha256(_split_bytes(rows)) for name, rows in split_files.items()},
            "reports/phase6_real_world_lexical.json": _sha256(report_data),
        },
        "lexical_summary": {
            split_name: json.loads(report_data)[split_name]["lexical_summary"]
            for split_name in ("dev", "validation", "exposed_test", "overall")
        },
    }


def verify() -> list[str]:
    problems: list[str] = []
    if not MANIFEST_PATH.exists():
        return [f"missing {MANIFEST_PATH.relative_to(ROOT)}"]
    expected_report = real_eval.report_bytes()
    if not REPORT_PATH.exists():
        problems.append(f"missing {REPORT_PATH.relative_to(ROOT)}")
    elif REPORT_PATH.read_bytes() != expected_report:
        problems.append(f"lexical report mismatch: {REPORT_PATH.relative_to(ROOT)}")
    splits = rw.build_real_world_cases()
    split_rows = dict(zip(SPLIT_OUTPUTS, splits, strict=True))
    for relative_path, path in SPLIT_OUTPUTS.items():
        if not path.exists():
            problems.append(f"missing generated split: {relative_path}")
        elif path.read_bytes() != _split_bytes(split_rows[relative_path]):
            problems.append(f"generated split mismatch: {relative_path}")
    try:
        recorded = json.loads(MANIFEST_PATH.read_text())
    except json.JSONDecodeError as exc:
        return problems + [f"invalid verification manifest: {exc}"]
    expected = build_manifest()
    if recorded != expected:
        for key in expected:
            if recorded.get(key) != expected[key]:
                problems.append(f"manifest mismatch in {key}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify", action="store_true", help="verify the pinned real-world artifacts")
    mode.add_argument("--write-manifest", action="store_true", help="write the real-world report and verification manifest")
    args = parser.parse_args()
    if args.write_manifest:
        splits = rw.build_real_world_cases()
        for (_, path), rows in zip(SPLIT_OUTPUTS.items(), splits, strict=True):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_split_bytes(rows))
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_bytes(real_eval.report_bytes())
        MANIFEST_PATH.write_text(json.dumps(build_manifest(), indent=2, ensure_ascii=False) + "\n")
        print(
            "wrote real-world split files, "
            f"{REPORT_PATH.relative_to(ROOT)}, and {MANIFEST_PATH.relative_to(ROOT)}"
        )
        return 0

    problems = verify()
    if problems:
        print("real-world benchmark verification failed:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    manifest = json.loads(MANIFEST_PATH.read_text())
    print(
        "real-world benchmark verified: "
        f"{manifest['verified_content']['passages']} passages, "
        f"{manifest['verified_content']['cases']} cases; lexical results reproduced; "
        "no network or JEV"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
