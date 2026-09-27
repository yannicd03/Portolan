"""Check that a source keyword is supported by the work's own title and abstract.

OpenAlex assigns keywords with a classifier that recurrently confuses homonyms
("edge" becomes "Enhanced Data Rates for GSM Evolution", "token" becomes "Security
token").  A keyword is kept only when the paper's text actually mentions it.
The same classifier attaches Wikipedia-style disambiguators ("Tree (set theory)",
"Latency (audio)"); the disambiguator must be supported by the text as well.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from .normalize import acronym_of, normalize_keyword

GROUNDING_STOP_WORDS: Final[frozenset[str]] = frozenset(
    {"and", "of", "for", "the", "in", "on", "with", "to", "a", "an", "via"}
)

_TRAILING_PARENTHETICAL_RE: Final[re.Pattern[str]] = re.compile(r"\s*\(([^()]*)\)\s*$")
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[^\W_]+", flags=re.UNICODE)


def strip_disambiguator(term: str) -> str:
    """Remove a trailing parenthetical such as ``"Tree (set theory)"`` -> ``"Tree"``."""

    stripped = _TRAILING_PARENTHETICAL_RE.sub("", term).strip()
    return stripped or term.strip()


def disambiguator_of(term: str) -> str | None:
    """Return the trailing parenthetical of *term* (``"set theory"``), or ``None``."""

    match = _TRAILING_PARENTHETICAL_RE.search(term)
    if match is None or not match.group(1).strip():
        return None
    if not term[: match.start()].strip():
        return None
    return match.group(1).strip()


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


def _disambiguator_grounded(
    disambiguator: str, core_tokens: list[str], text_tokens: set[str]
) -> bool:
    """Return whether a parenthetical disambiguator is supported by the text.

    At least one content word of the disambiguator must occur in the text.  A
    parenthetical that is the core's own acronym (``"Key value (KV)"``) is an
    abbreviation rather than a sense label and is always accepted.
    """

    stripped = disambiguator.strip()
    is_acronym = stripped.isalnum() and stripped.isupper()
    if is_acronym and acronym_of(" ".join(core_tokens)) == stripped.casefold():
        return True
    content = [token for token in _normalized_tokens(stripped) if token not in GROUNDING_STOP_WORDS]
    return any(token in text_tokens for token in content)


def is_grounded(term: str, text: str) -> bool:
    """Return whether keyword *term* is supported by *text* (title plus abstract).

    A term is grounded when every content token of the term (stop words ignored,
    trailing parenthetical disambiguator removed) occurs among the text's tokens,
    both sides normalised with :func:`normalize_keyword`.  A multi-word term is also
    grounded when its acronym appears in the text in uppercase as a standalone token
    of at least two characters.  A term with a disambiguator, ``"X (Y)"``, further
    needs at least one content word of ``Y`` in the text, so "Tree (set theory)" is
    rejected for a paper about token trees.
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
    core_grounded = bool(content) and all(token in text_tokens for token in content)
    if not core_grounded:
        acronym = acronym_of(" ".join(term_tokens))
        core_grounded = acronym is not None and _acronym_in_text(acronym, raw_text)
    if not core_grounded:
        return False

    disambiguator = disambiguator_of(term)
    if disambiguator is None:
        return True
    return _disambiguator_grounded(disambiguator, term_tokens, text_tokens)


__all__ = ["GROUNDING_STOP_WORDS", "disambiguator_of", "is_grounded", "strip_disambiguator"]
