"""Pinned, offline tokenizers for visible-answer token counting.

Each tokenizer is identified by an ID and verified by SHA-256 before use. No
network access occurs at import or count time. Files are fetched once by
`scripts/fetch_phase6_tokenizers.py` into `EVIDENCE_TOKENIZER_DIR`.

Roles:
- The shared meter (`o200k_base`) classifies every visible answer against the
  800-token target. It is a common yardstick, not the exact billing count of any
  provider.
- Provider-native tokenizers (DeepSeek V4 vendor tokenizer) are recorded as
  additional telemetry where available.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

TIKTOKEN_O200K_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
TIKTOKEN_O200K_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
DEEPSEEK_ZIP_URL = "https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip"
DEEPSEEK_ZIP_SHA256 = "e7310d1dafe0a86d8a5629fe78a7c763760f651db9b8682718a1781dcd6fe495"
DEEPSEEK_TOKENIZER_JSON_SHA256 = "89085f12ef79460ac5f66d1119325ddfc694b4ab209d80bbd81d35f081dc9614"


@dataclass(frozen=True)
class TokenizerSpec:
    tokenizer_id: str
    library: Literal["tiktoken", "tokenizers"]
    library_version: str
    artifact_sha256: str
    artifact_name: str


TOKENIZERS: dict[str, TokenizerSpec] = {
    "o200k_base-tiktoken-0.14.0": TokenizerSpec(
        tokenizer_id="o200k_base-tiktoken-0.14.0",
        library="tiktoken",
        library_version="0.14.0",
        artifact_sha256=TIKTOKEN_O200K_SHA256,
        artifact_name="o200k_base.tiktoken",
    ),
    "deepseek-v4-hf-tokenizers-0.23.3": TokenizerSpec(
        tokenizer_id="deepseek-v4-hf-tokenizers-0.23.3",
        library="tokenizers",
        library_version="0.23.3",
        artifact_sha256=DEEPSEEK_TOKENIZER_JSON_SHA256,
        artifact_name="deepseek_v4_tokenizer/tokenizer.json",
    ),
}

# Shared comparison meter: every candidate's visible answer is counted with this
# tokenizer so that the 800-token target means the same thing across models.
SHARED_VISIBLE_TOKENIZER_ID = "o200k_base-tiktoken-0.14.0"

# Optional provider-native telemetry, recorded in addition to the shared meter.
# It is never used for the target classification and is never presented as the
# exact billing count for every build.
NATIVE_TOKENIZERS: dict[str, str] = {
    "deepseek-flash": "deepseek-v4-hf-tokenizers-0.23.3",
    "deepseek-v4-pro": "deepseek-v4-hf-tokenizers-0.23.3",
}


def tokenizer_dir() -> Path:
    configured = os.environ.get("EVIDENCE_TOKENIZER_DIR")
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "evidence-layer" / "tokenizers"


def _verified_bytes(path: Path, expected_sha256: str) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(
            f"missing tokenizer artifact {path}; run scripts/fetch_phase6_tokenizers.py"
        )
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"tokenizer artifact {path} failed SHA-256 verification")
    return data


def load_counter(tokenizer_id: str) -> Callable[[str], int]:
    """Return a verified, offline token counter for visible text."""
    spec = TOKENIZERS[tokenizer_id]
    root = tokenizer_dir()
    if spec.library == "tiktoken":
        import tiktoken

        cache = root / "tiktoken"
        name = hashlib.sha1(TIKTOKEN_O200K_URL.encode()).hexdigest()
        _verified_bytes(cache / name, spec.artifact_sha256)
        os.environ["TIKTOKEN_CACHE_DIR"] = str(cache)
        encoding = tiktoken.get_encoding("o200k_base")
        return lambda text: len(encoding.encode(text, disallowed_special=()))
    from tokenizers import Tokenizer

    path = root / spec.artifact_name
    _verified_bytes(path, spec.artifact_sha256)
    tokenizer = Tokenizer.from_file(str(path))
    return lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids)


def shared_visible_counter() -> tuple[Callable[[str], int], str, str]:
    """Return (counter, tokenizer_id, sha256) for the shared visible meter."""
    spec = TOKENIZERS[SHARED_VISIBLE_TOKENIZER_ID]
    return load_counter(SHARED_VISIBLE_TOKENIZER_ID), spec.tokenizer_id, spec.artifact_sha256


def native_counter(model_id: str) -> tuple[Callable[[str], int], str, str] | None:
    """Return (counter, tokenizer_id, sha256) for provider-native telemetry, if any."""
    tokenizer_id = NATIVE_TOKENIZERS.get(model_id)
    if tokenizer_id is None:
        return None
    spec = TOKENIZERS[tokenizer_id]
    return load_counter(tokenizer_id), spec.tokenizer_id, spec.artifact_sha256
