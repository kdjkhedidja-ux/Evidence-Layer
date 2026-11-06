"""Deterministic synthetic answers of an exact visible length.

Used only by offline stress tests. Text is built from a fixed word pool and
checked against the shared `o200k_base` meter, so `exact_visible_text(n)`
produces a string whose visible count is exactly `n`. No provider is involved.
"""

from __future__ import annotations

from typing import Callable

WORD_POOL: tuple[str, ...] = tuple((
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
    "xray yankee zulu"
).split())


def exact_visible_text(target_tokens: int, counter: Callable[[str], int]) -> str:
    """Return text whose `counter` value is exactly `target_tokens`.

    Greedy and deterministic: each step appends the first pool word that keeps
    the count at or below the target and, if possible, lands exactly on it.
    Raises if no exact construction is found, rather than returning an
    approximation.
    """
    if target_tokens < 0:
        raise ValueError("target_tokens must be non-negative")
    if target_tokens == 0:
        return ""
    text = ""
    cursor = 0
    while True:
        current = counter(text) if text else 0
        if current == target_tokens:
            return text
        if current > target_tokens:
            raise RuntimeError("overshot target; pool word is not single-token")
        chosen: str | None = None
        for offset in range(len(WORD_POOL)):
            word = WORD_POOL[(cursor + offset) % len(WORD_POOL)]
            candidate = f"{text} {word}" if text else word
            count = counter(candidate)
            if count == target_tokens:
                return candidate
            if current < count < target_tokens and chosen is None:
                chosen = candidate
        if chosen is None:
            raise RuntimeError(f"no exact construction for {target_tokens} tokens")
        text = chosen
        cursor += 1
