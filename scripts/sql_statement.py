"""Statement-level SQL lexing and tier classification.

This is deliberately NOT a PostgreSQL parser. It provides exactly the structure
the migration guard needs:

* split a migration into top-level statements without being fooled by line
  comments, nested block comments, quoted identifiers, string literals or
  dollar-quoted bodies carrying any tag;
* normalise a statement into a stable form used as its approval identity;
* classify a statement into an approval tier.

Anything the lexer cannot close - an unterminated block comment, string,
identifier or dollar-quoted body - raises :class:`SqlLexError` instead of
guessing. The guard treats a lexing error as a blocking failure, because an
ambiguous file cannot be reviewed.

Known limitations, by design and documented rather than papered over:

* statements nested inside a dollar-quoted body are opaque. A ``do`` block is
  therefore a single registry-gated statement regardless of the dynamic SQL it
  carries.
* ``E'...'`` escape strings are lexed with backslash escapes enabled only when
  the ``E`` is directly adjacent to the opening quote.
* the classifier looks at the shape of a statement, not at its meaning: it can
  tell that a statement is a ``grant``, but it does not decide whether that
  grant widens or narrows access. That judgement belongs to the reviewer who
  writes the ledger rationale.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

TIER_HARD_DENY = 0
TIER_REGISTRY = 1
TIER_ALLOWED = 2

TIER_NAMES = {
    TIER_HARD_DENY: "hard-deny",
    TIER_REGISTRY: "registry",
    TIER_ALLOWED: "allowed",
}


class SqlLexError(Exception):
    """Raised when a statement cannot be lexed unambiguously."""


class AmbiguousStatement(SqlLexError):
    """Raised when a statement cannot be classified with confidence."""


_DOLLAR_OPEN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]*)?")

_HARD_DENY_SEQUENCES = (
    ("disable", "row", "level", "security"),
    ("no", "force", "row", "level", "security"),
    ("superuser",),
    ("bypassrls",),
    ("replication",),
)

_ALLOWED_LEADING = (
    ("create", "table"),
    ("create", "temp"),
    ("create", "temporary"),
    ("create", "index"),
    ("create", "unique"),
    ("create", "schema"),
    ("create", "extension"),
    ("create", "trigger"),
    ("comment", "on"),
    ("set",),
    ("reset",),
    ("analyze",),
)

_REGISTRY_LEADING = (
    ("grant",),
    ("revoke",),
    ("do",),
    ("drop",),
    ("truncate",),
    ("delete",),
    ("update",),
    ("insert",),
    ("select",),
    ("with",),
    ("merge",),
    ("alter",),
    ("create", "or", "replace"),
    ("create", "function"),
    ("create", "policy"),
    ("create", "view"),
    ("create", "materialized"),
    ("create", "type"),
    ("create", "role"),
    ("create", "user"),
    ("create", "sequence"),
)


@dataclass(frozen=True)
class Token:
    kind: str  # comment | string | ident | dollar | word | number | punct
    text: str
    line: int

    @property
    def normalised(self) -> str:
        if self.kind == "word":
            return self.text.lower()
        return self.text


@dataclass(frozen=True)
class Statement:
    tokens: tuple[Token, ...]
    normalised: str
    line: int

    @property
    def words(self) -> tuple[str, ...]:
        return tuple(token.text.lower() for token in self.tokens if token.kind == "word")

    @property
    def statement_id(self) -> str:
        return hashlib.sha256(self.normalised.encode("utf-8")).hexdigest()[:16]

    @property
    def verb(self) -> str:
        return " ".join(self.words[:2]) if self.words else "(empty)"


def tokenise(text: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    length = len(text)
    line = 1
    previous_end = -1
    previous_kind = ""

    while index < length:
        char = text[index]
        if char == "\n":
            line += 1
            index += 1
            continue
        if char in " \t\r\f\v":
            index += 1
            continue

        start_line = line

        if text.startswith("--", index):
            end = text.find("\n", index)
            if end == -1:
                end = length
            tokens.append(Token("comment", text[index:end], start_line))
            index = end
            continue

        if text.startswith("/*", index):
            depth = 1
            cursor = index + 2
            while depth:
                if cursor >= length:
                    raise SqlLexError(f"unterminated block comment starting on line {start_line}")
                if text.startswith("/*", cursor):
                    depth += 1
                    cursor += 2
                elif text.startswith("*/", cursor):
                    depth -= 1
                    cursor += 2
                else:
                    if text[cursor] == "\n":
                        line += 1
                    cursor += 1
            tokens.append(Token("comment", text[index:cursor], start_line))
            index = cursor
            continue

        if char == "'":
            escape_backslash = previous_kind == "word" and previous_end == index - 1 and text[index - 1] in "Ee"
            cursor = index + 1
            while True:
                if cursor >= length:
                    raise SqlLexError(f"unterminated string literal starting on line {start_line}")
                if escape_backslash and text[cursor] == "\\":
                    cursor += 2
                    continue
                if text[cursor] == "'":
                    if cursor + 1 < length and text[cursor + 1] == "'":
                        cursor += 2
                        continue
                    cursor += 1
                    break
                if text[cursor] == "\n":
                    line += 1
                cursor += 1
            tokens.append(Token("string", text[index:cursor], start_line))
            previous_kind, previous_end = "string", cursor
            index = cursor
            continue

        if char == '"':
            cursor = index + 1
            while True:
                if cursor >= length:
                    raise SqlLexError(f"unterminated quoted identifier starting on line {start_line}")
                if text[cursor] == '"':
                    if cursor + 1 < length and text[cursor + 1] == '"':
                        cursor += 2
                        continue
                    cursor += 1
                    break
                if text[cursor] == "\n":
                    line += 1
                cursor += 1
            tokens.append(Token("ident", text[index:cursor], start_line))
            previous_kind, previous_end = "ident", cursor
            index = cursor
            continue

        if char == "$":
            match = _DOLLAR_OPEN.match(text, index)
            if match:
                tag = match.group(0)
                end = text.find(tag, match.end())
                if end == -1:
                    raise SqlLexError(
                        f"unterminated dollar-quoted body opening with {tag} on line {start_line}"
                    )
                body = text[index : end + len(tag)]
                tokens.append(Token("dollar", body, start_line))
                line += body.count("\n")
                previous_kind, previous_end = "dollar", index + len(body)
                index += len(body)
                continue

        match = _WORD.match(text, index)
        if match:
            tokens.append(Token("word", match.group(0), start_line))
            previous_kind, previous_end = "word", match.end()
            index = match.end()
            continue

        match = _NUMBER.match(text, index)
        if match:
            tokens.append(Token("number", match.group(0), start_line))
            previous_kind, previous_end = "number", match.end()
            index = match.end()
            continue

        tokens.append(Token("punct", char, start_line))
        previous_kind, previous_end = "punct", index + 1
        index += 1

    return tokens


def split_statements(text: str) -> list[Statement]:
    statements: list[Statement] = []
    current: list[Token] = []
    for token in tokenise(text):
        if token.kind == "comment":
            continue
        if token.kind == "punct" and token.text == ";":
            if current:
                statements.append(_make_statement(current))
                current = []
            continue
        current.append(token)
    if current:
        statements.append(_make_statement(current))
    return statements


def _make_statement(tokens: list[Token]) -> Statement:
    normalised = " ".join(token.normalised for token in tokens)
    return Statement(tokens=tuple(tokens), normalised=normalised, line=tokens[0].line)


def _contains_sequence(words: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    span = len(sequence)
    return any(words[i : i + span] == sequence for i in range(len(words) - span + 1))


def _starts_with(words: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return words[: len(prefix)] == prefix


def classify(statement: Statement) -> tuple[int, str]:
    """Return ``(tier, reason)`` for a statement.

    Raises :class:`AmbiguousStatement` when the shape is not recognised. Not
    recognising a statement is a blocking condition, never a default allow.
    """
    words = statement.words
    if not words:
        raise AmbiguousStatement(f"empty statement on line {statement.line}")

    if _starts_with(words, ("set", "role")) or _starts_with(words, ("set", "local", "role")):
        return TIER_HARD_DENY, "session role switch is never allowed in a migration"
    if _starts_with(words, ("set", "session", "role")) or _starts_with(words, ("reset", "role")):
        return TIER_HARD_DENY, "session role switch is never allowed in a migration"
    if _starts_with(words, ("drop", "schema")):
        return TIER_HARD_DENY, "dropping a schema removes every object it holds"
    for sequence in _HARD_DENY_SEQUENCES:
        if _contains_sequence(words, sequence):
            return TIER_HARD_DENY, f"contains {'.'.join(sequence)}"
    if _starts_with(words, ("grant",)) and "to" in words:
        grantees = words[words.index("to") + 1 :]
        if grantees[:1] == ("public",):
            return TIER_HARD_DENY, "granting to PUBLIC is never allowed in a migration"
    if _contains_sequence(words, ("security", "definer")) and not _contains_sequence(
        words, ("set", "search_path")
    ):
        return TIER_HARD_DENY, "SECURITY DEFINER without SET search_path"

    for prefix in _ALLOWED_LEADING:
        if _starts_with(words, prefix):
            return TIER_ALLOWED, f"{' '.join(prefix)} statement"
    for prefix in _REGISTRY_LEADING:
        if _starts_with(words, prefix):
            return TIER_REGISTRY, f"{prefix[0]} statement"

    raise AmbiguousStatement(
        f"unrecognised statement starting on line {statement.line}: "
        f"{statement.normalised[:120]!r}"
    )


def analyse(text: str) -> list[Statement]:
    """Lex, split and classify one migration body."""
    statements = split_statements(text)
    for statement in statements:
        classify(statement)
    return statements
