#!/usr/bin/env python3
"""Fetch and verify the offline tokenizer artifacts used by Phase 6.

This downloads static vocabulary files only; it sends no evaluation data and
calls no model API. Files are verified against SHA-256 pins in
`evidence_layer.tokenization`.
"""

from __future__ import annotations

import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from evidence_layer import tokenization as t  # noqa: E402


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - pinned static URL
        return response.read()


def main() -> None:
    root = t.tokenizer_dir()
    root.mkdir(parents=True, exist_ok=True)

    tik = _get(t.TIKTOKEN_O200K_URL)
    if hashlib.sha256(tik).hexdigest() != t.TIKTOKEN_O200K_SHA256:
        raise SystemExit("o200k_base SHA-256 mismatch")
    name = hashlib.sha1(t.TIKTOKEN_O200K_URL.encode()).hexdigest()
    (root / "tiktoken").mkdir(exist_ok=True)
    (root / "tiktoken" / name).write_bytes(tik)

    archive = _get(t.DEEPSEEK_ZIP_URL)
    if hashlib.sha256(archive).hexdigest() != t.DEEPSEEK_ZIP_SHA256:
        raise SystemExit("DeepSeek tokenizer archive SHA-256 mismatch")
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        data = bundle.read("deepseek_v4_tokenizer/tokenizer.json")
    if hashlib.sha256(data).hexdigest() != t.DEEPSEEK_TOKENIZER_JSON_SHA256:
        raise SystemExit("DeepSeek tokenizer.json SHA-256 mismatch")
    target = root / "deepseek_v4_tokenizer"
    target.mkdir(exist_ok=True)
    (target / "tokenizer.json").write_bytes(data)
    print(f"verified tokenizer artifacts in {root}")


if __name__ == "__main__":
    main()
