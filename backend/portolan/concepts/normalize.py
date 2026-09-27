"""Deterministic normalization helpers for keyword concepts."""

from __future__ import annotations

import re
import unicodedata
from typing import Final

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
_SLUG_SEPARATOR_RE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")

_SINGULAR_EXCEPTIONS: Final[frozenset[str]] = frozenset(
    {
        "analysis",
        "bias",
        "gas",
        "lens",
        "physics",
        "mathematics",
        "series",
        "species",
        "news",
        "process",
        "class",
        "access",
        "corpus",
        "status",
        "basis",
        "axis",
        "thesis",
    }
)

# These plurals do not have a reliable suffix-only singularization rule.  Keep
# this short list explicit so the general rules below remain conservative.
_IRREGULAR_PLURALS: Final[dict[str, str]] = {
    "analyses": "analysis",
    "axes": "axis",
    "bases": "basis",
    "biases": "bias",
    "crises": "crisis",
    "diagnoses": "diagnosis",
    "gases": "gas",
    "hypotheses": "hypothesis",
    "lenses": "lens",
    "oases": "oasis",
    "statuses": "status",
    "theses": "thesis",
}

_ACRONYM_STOP_WORDS: Final[frozenset[str]] = frozenset(
    {"of", "for", "and", "the", "in", "on", "with", "to", "a", "an"}
)


def _is_surrounding_punctuation(character: str) -> bool:
    """Return whether *character* is punctuation that can frame a term."""

    return unicodedata.category(character).startswith("P") or character in {"`"}


def _strip_surrounding_punctuation(value: str) -> str:
    """Remove punctuation and quote characters from both ends of *value*."""

    start = 0
    end = len(value)
    while start < end and _is_surrounding_punctuation(value[start]):
        start += 1
    while end > start and _is_surrounding_punctuation(value[end - 1]):
        end -= 1
    return value[start:end]


def _singularize(token: str) -> str:
    """Apply a deliberately small set of English plural rules to one token."""

    if not token or token in _SINGULAR_EXCEPTIONS:
        return token
    if token in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[token]

    # Very short words are more likely to be names or already singular forms.
    if len(token) <= 3:
        return token
    if token.endswith("ies"):
        return f"{token[:-3]}y"
    if token.endswith(("sses", "ches", "shes", "xes", "zes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def normalize_keyword(term: str) -> str:
    """Normalize a keyword into a deterministic, singular concept form.

    Unicode compatibility forms are folded first, separators are treated as
    spaces, and only the last token receives conservative singularization.
    Punctuation-only values are discarded as empty terms.
    """

    if not isinstance(term, str):
        return ""

    value = unicodedata.normalize("NFKC", term).casefold()
    value = value.replace("_", " ").replace("-", " ").replace("/", " ")
    value = _strip_surrounding_punctuation(value)
    value = _WHITESPACE_RE.sub(" ", value).strip()
    value = _strip_surrounding_punctuation(value).strip()
    if not value or not any(character.isalnum() for character in value):
        return ""

    tokens = value.split(" ")
    tokens[-1] = _singularize(tokens[-1])
    return " ".join(tokens)


def acronym_of(normalized_term: str) -> str | None:
    """Return initials for a multi-word normalized term, or ``None``."""

    if not isinstance(normalized_term, str):
        return None

    words = normalized_term.casefold().split()
    if len(words) < 2:
        return None
    initials = "".join(word[0] for word in words if word not in _ACRONYM_STOP_WORDS)
    return initials or None


def slugify(text: str) -> str:
    """Convert text to a lowercase ASCII slug separated by hyphens."""

    if not isinstance(text, str):
        return ""

    ascii_text = (
        unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode("ascii")
    )
    return _SLUG_SEPARATOR_RE.sub("-", ascii_text).strip("-")


__all__ = ["acronym_of", "normalize_keyword", "slugify"]
