"""Deterministic keyphrase extraction from a project's titles and abstracts.

Source keyword fields miss many of a field's own terms ("speculative decoding",
"draft model").  This module adds a second, text-derived keyword source: 1-3-word
phrases scored by title bonus x term frequency x project-level inverse document
frequency, kept only when at least two works of the project use them.  No network,
model, or third-party dependency is involved, so the output is fully reproducible.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Final

from .merge import KeywordOccurrence
from .normalize import normalize_keyword

KEYPHRASE_KIND: Final[str] = "keyphrase"
TITLE_BONUS: Final[float] = 2.0
MAX_NGRAM: Final[int] = 3
MIN_WORK_SUPPORT: Final[int] = 2
# A shorter phrase is dropped in favour of a qualifying longer phrase containing it
# when the longer one appears in at least this share of the shorter one's works.
SIMILAR_DF_RATIO: Final[float] = 0.8


def _words(block: str) -> list[str]:
    """Split a whitespace-separated word block into words."""

    return block.split()


# Words that may not start or end a phrase.  Compact English function words plus
# generic academic vocabulary; phrases may still contain them in the middle
# ("mixture of experts").
_ENGLISH_STOP_WORDS: Final[frozenset[str]] = frozenset(
    _words(
        """
    a about above after again against all almost also although always am among an and
    another any are as at be because been before being below between both but by can
    cannot could did do does doing done down during each either else even ever every
    few for from further had has have having he her here hers him his how however i if
    in into is it its itself just less like many may me might more most much must my
    neither no nor not now of off often on once one only onto or other others otherwise
    our ours out over own per quite rather same several she should since so some such
    than that the their theirs them then there therefore these they this those though
    through thus to too toward towards under until up upon us very via was we well were
    what when where whether which while who whom whose why will with within without
    would yet you your
    """
    )
)
_ACADEMIC_STOP_WORDS: Final[frozenset[str]] = frozenset(
    _words(
        """
    paper papers propose proposes proposed proposing present presents presented method
    methods approach approaches result results show shows shown showing demonstrate
    demonstrates demonstrated work works study studies novel new based using use used
    uses performance existing recent recently state-of-the-art significantly significant
    et al e.g i.e etc various different several effective effectively efficient
    efficiently improve improves improved improving improvement improvements achieve
    achieves achieved achieving outperform outperforms outperforming experiment
    experiments experimental extensive evaluate evaluated evaluation findings introduce
    introduces introduced first second third two three four five high higher low lower
    large larger small smaller better best good key main important however furthermore
    moreover additionally specifically particular particularly well-known widely often
    typically across without including include includes compared comparison respectively
    up to while leading lead leads provide provides provided enable enables enabling
    address addresses addressing problem problems challenge challenges way ways number
    numbers range order aim goal
    """
    )
)
# Words that are meaningful at the edge of a longer phrase ("draft model",
# "downstream task") but too generic to stand alone as a concept.
_UNIGRAM_ONLY_STOP_WORDS: Final[frozenset[str]] = frozenset(
    _words(
        """
    model models task tasks data dataset datasets system systems framework frameworks
    network networks algorithm algorithms technique techniques application applications
    analysis process result level time times speed speedup cost costs quality accuracy
    set sets step steps output outputs input inputs setting settings strategy strategies
    """
    )
)
STOP_WORDS: Final[frozenset[str]] = _ENGLISH_STOP_WORDS | _ACADEMIC_STOP_WORDS

# Split into segments at any character that is not a word character, whitespace, or
# an intra-word hyphen/apostrophe.  Phrases never cross segment boundaries.
_SEGMENT_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[^\w\s\-']+|_+", flags=re.UNICODE)
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", flags=re.UNICODE)

WorkText = tuple[str, str | None, str | None]


def _segments(text: str) -> list[list[str]]:
    folded = unicodedata.normalize("NFKC", text).casefold()
    result: list[list[str]] = []
    for segment in _SEGMENT_SPLIT_RE.split(folded):
        tokens = _TOKEN_RE.findall(segment)
        if tokens:
            result.append(tokens)
    return result


def _is_edge_stop(token: str) -> bool:
    return token in STOP_WORDS or len(token) < 2 or token.isdigit()


def _phrase_key(tokens: Sequence[str]) -> str | None:
    """Return the normalised phrase for an n-gram, or ``None`` when it is not a candidate."""

    if _is_edge_stop(tokens[0]) or _is_edge_stop(tokens[-1]):
        return None
    if all(any(character.isdigit() for character in token) for token in tokens):
        return None
    if len(tokens) == 1 and tokens[0] in _UNIGRAM_ONLY_STOP_WORDS:
        return None
    key = normalize_keyword(" ".join(tokens))
    if not key:
        return None
    if len(tokens) == 1 and key in _UNIGRAM_ONLY_STOP_WORDS | STOP_WORDS:
        return None
    return key


def _phrase_counts(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for tokens in _segments(text):
        for size in range(1, MAX_NGRAM + 1):
            for start in range(len(tokens) - size + 1):
                key = _phrase_key(tokens[start : start + size])
                if key is not None:
                    counts[key] += 1
    return counts


def _contains(longer: str, shorter: str) -> bool:
    return f" {shorter} " in f" {longer} "


def _subsumed_phrases(document_frequency: Counter[str], qualifying: set[str]) -> set[str]:
    """Return shorter phrases that a qualifying longer phrase with similar df replaces."""

    by_length: dict[int, list[str]] = {}
    for phrase in qualifying:
        by_length.setdefault(phrase.count(" ") + 1, []).append(phrase)
    subsumed: set[str] = set()
    for phrase in sorted(qualifying):
        length = phrase.count(" ") + 1
        df = document_frequency[phrase]
        for longer_length in range(length + 1, MAX_NGRAM + 1):
            if any(
                _contains(longer, phrase) and document_frequency[longer] >= SIMILAR_DF_RATIO * df
                for longer in by_length.get(longer_length, ())
            ):
                subsumed.add(phrase)
                break
    return subsumed


def extract_keyphrases(
    works: Sequence[WorkText],
    *,
    max_per_work: int = 8,
) -> list[KeywordOccurrence]:
    """Extract text keyphrases supported by at least two works of the project.

    ``works`` holds ``(work_id, title, abstract)`` triples for the whole project.  A
    phrase's per-work score is ``title_bonus x tf x idf`` with ``title_bonus`` 2.0 when
    the phrase occurs in the title, and ``idf = log((N + 1) / (df + 1)) + 1`` over the
    project's ``N`` works.  Scores are divided by the work's best score, so each work's
    top phrase has score 1.0.  A shorter phrase is dropped when a longer phrase that
    contains it qualifies with a similar document frequency.  Output is ordered by
    input work order, then descending score, then phrase.
    """

    if max_per_work <= 0:
        return []
    per_work: list[tuple[str, Counter[str], set[str]]] = []
    document_frequency: Counter[str] = Counter()
    for work_id, title, abstract in works:
        title_text = title or ""
        counts = _phrase_counts(title_text) + _phrase_counts(abstract or "")
        title_phrases = set(_phrase_counts(title_text))
        per_work.append((work_id, counts, title_phrases))
        document_frequency.update(set(counts))

    qualifying = {phrase for phrase, df in document_frequency.items() if df >= MIN_WORK_SUPPORT}
    qualifying -= _subsumed_phrases(document_frequency, qualifying)
    total = len(works)

    occurrences: list[KeywordOccurrence] = []
    for work_id, counts, title_phrases in per_work:
        raw: dict[str, float] = {}
        for phrase, tf in counts.items():
            if phrase not in qualifying:
                continue
            idf = math.log((total + 1) / (document_frequency[phrase] + 1)) + 1.0
            bonus = TITLE_BONUS if phrase in title_phrases else 1.0
            raw[phrase] = bonus * tf * idf
        if not raw:
            continue
        best = max(raw.values())
        ranked = sorted(raw.items(), key=lambda item: (-item[1], item[0]))[:max_per_work]
        occurrences.extend(
            KeywordOccurrence(
                work_id=work_id,
                term=phrase,
                score=round(score / best, 6),
                kind=KEYPHRASE_KIND,
            )
            for phrase, score in ranked
        )
    return occurrences


__all__ = ["KEYPHRASE_KIND", "STOP_WORDS", "WorkText", "extract_keyphrases"]
