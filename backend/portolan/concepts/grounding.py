"""Check that a source keyword is supported by the work's own title and abstract.

OpenAlex assigns keywords with a classifier that recurrently confuses homonyms
("edge" becomes "Enhanced Data Rates for GSM Evolution", "token" becomes "Security
token").  A keyword is kept only when the paper's text actually mentions it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from .normalize import acronym_of, normalize_keyword

GROUNDING_STOP_WORDS: Final[frozenset[str]] = frozenset(
    {"and", "of", "for", "the", "in", "on", "with", "to", "a", "an", "via"}
)

_TRAILING_PARENTHETICAL_RE: Final[re.Pattern[str]] = re.compile(r"\s*\([^()]*\)\s*$")
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[^\W_]+", flags=re.UNICODE)


def strip_disambiguator(term: str) -> str:
    """Remove a trailing parenthetical such as ``"Tree (set theory)"`` -> ``"Tree"``."""

    stripped = _TRAILING_PARENTHETICAL_RE.sub("", term).strip()
    return stripped or term.strip()


def _normalized_tokens(text: str) -> list[str]:
    """Split *text* into words, each normalised (casefolded, NFKC, singularised)."""

    folded = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for word in _WORD_RE.findall(folded):
        normalized = normalize_keyword(word)
        if normalized:
            tokens.append(normalized)
    return tokens


def _acronym_in_text(acronym: str, raw_text: str) -> bool:
    """Return whether the uppercase *acronym* stands alone in the raw text.

    The check is case-sensitive on purpose: the lowercase word "edge" must not
    ground an "EDGE" acronym.  A plural ``s`` (``"LLMs"``) is accepted.
    """

    upper = acronym.upper()
    if len(upper) < 2 or not upper.isalnum():
        return False
    pattern = rf"(?<![^\W_]){re.escape(upper)}s?(?![^\W_])"
    return re.search(pattern, raw_text) is not None


def is_grounded(term: str, text: str) -> bool:
    """Return whether keyword *term* is supported by *text* (title plus abstract).

    A term is grounded when every content token of the term (stop words ignored,
    trailing parenthetical disambiguator removed) occurs among the text's tokens,
    both sides normalised with :func:`normalize_keyword`.  A multi-word term is also
    grounded when its acronym appears in the text in uppercase as a standalone token
    of at least two characters.
    """

    if not isinstance(term, str) or not isinstance(text, str) or not text.strip():
        return False
    core = strip_disambiguator(term)
    term_tokens = _normalized_tokens(core)
    if not term_tokens:
        return False

    raw_text = unicodedata.normalize("NFKC", text)
    text_tokens = set(_normalized_tokens(raw_text))
    content = [token for token in term_tokens if token not in GROUNDING_STOP_WORDS]
    if content and all(token in text_tokens for token in content):
        return True

    acronym = acronym_of(" ".join(term_tokens))
    return acronym is not None and _acronym_in_text(acronym, raw_text)


__all__ = ["GROUNDING_STOP_WORDS", "is_grounded", "strip_disambiguator"]
