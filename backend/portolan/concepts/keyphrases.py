"""Deterministic keyphrase extraction from a project's titles and abstracts.

Source keyword fields miss many of a field's own terms ("speculative decoding",
"draft model").  This module adds a second, text-derived keyword source: 1-3-word
phrases scored by title bonus x term frequency x project-level inverse document
frequency, kept only when at least two works of the project use them.  No network,
model, or third-party dependency is involved, so the output is fully reproducible.

Before extraction, venue and licence boilerplate ("(c) 2023 Association for
Computational Linguistics", "Published as a conference paper at ...") is cut from
the abstracts.  Single words are held to a stricter standard than phrases: an
uppercase acronym (GPU, LLM) is kept in its canonical form; any other word must be
a noun-like domain term that is not generic and not redundant with a kept phrase.
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
    numbers range order aim goal ability abilities alleviate alleviates current
    currently exploit exploits exploiting original real
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
# Single words too generic to be a concept of their own, even when they pass the
# stop-word lists (they may still appear inside a phrase such as "query rewriting").
_GENERIC_SINGLE_WORDS: Final[frozenset[str]] = frozenset(
    _words(
        """
    fast faster fastest efficient efficiency adaptive accelerating accelerate
    accelerated acceleration generate generates generated execution resource
    constraint parameter optimization optimisation training survey query deep design
    """
    )
)
# Suffixes of words that usually act as verbs, participles, adjectives or adverbs.
# A single word with one of them is kept only as a clear title-level term.
_NON_NOUN_SUFFIXES: Final[tuple[str, ...]] = ("ing", "ed", "ly", "ive", "ous", "able", "ful")
# The suffix must follow a stem of at least this length ("string", "table" and
# "bed" are nouns, not inflections).
_MIN_SUFFIX_STEM: Final[int] = 4
# Common nouns that happen to carry one of the suffixes above.
_SUFFIX_NOUN_EXCEPTIONS: Final[frozenset[str]] = frozenset(
    _words(
        """
    anomaly assembly family supply objective archive derivative incentive initiative
    variable
    """
    )
)
# A non-noun-like single word survives only when at least this many titles use it.
MIN_TITLE_SUPPORT: Final[int] = 2
# A single word whose occurrences fall inside one kept phrase at least this often
# ("speculative" inside "speculative decoding") is redundant with that phrase.
REDUNDANT_WORD_RATIO: Final[float] = 0.8

# Venue, licence and citation boilerplate that some sources append to abstracts.
# Matching text is cut from the match to the end of its sentence.
_BOILERPLATE_RE: Final[re.Pattern[str]] = re.compile(
    r"association\s+for\s+computational\s+linguistics"
    r"|proceedings\s+of"
    r"|©|\(c\)\s*\d{4}|\bcopyright\b"
    r"|\barxiv\b"
    r"|published\s+as\s+a\s+conference\s+paper"
    r"|licen[sc]ed\s+under"
    r"|all\s+rights\s+reserved"
    r"|(?<![^\W_])in:\s",
    flags=re.IGNORECASE,
)
_SENTENCE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")
# Phrases contained in these boilerplate strings are never emitted, even when the
# text escaped the sentence-level stripping ("computational linguistic").
_BOILERPLATE_PHRASES: Final[tuple[str, ...]] = (
    "association for computational linguistics",
    "proceedings",
    "copyright",
    "arxiv",
    "published as a conference paper",
    "licensed under",
    "all rights reserved",
)

# Split into segments at any character that is not a word character, whitespace, or
# an intra-word hyphen/apostrophe.  Phrases never cross segment boundaries.
_SEGMENT_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[^\w\s\-']+|_+", flags=re.UNICODE)
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", flags=re.UNICODE)

WorkText = tuple[str, str | None, str | None]
# Uppercase acronyms in raw text, with an optional plural "s" ("LLMs").
_ACRONYM_RE: Final[re.Pattern[str]] = re.compile(r"(?<![^\W_])([A-Z][A-Z0-9]*[A-Z])s?(?![^\W_])")


def strip_boilerplate(text: str) -> str:
    """Remove venue, licence and citation boilerplate from an abstract.

    Each sentence is cut at the first boilerplate match, so a trailing
    "(c) 2023 Association for Computational Linguistics" or "In: Proceedings of ..."
    fragment disappears while the preceding content of the sentence stays.
    """

    if not text:
        return ""
    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        match = _BOILERPLATE_RE.search(sentence)
        if match is not None:
            sentence = sentence[: match.start()]
        sentence = sentence.strip()
        if sentence:
            kept.append(sentence)
    return " ".join(kept)


_NORMALIZED_BOILERPLATE: Final[tuple[str, ...]] = tuple(
    " ".join(normalize_keyword(word) for word in phrase.split()) for phrase in _BOILERPLATE_PHRASES
)


def _is_boilerplate_phrase(phrase: str) -> bool:
    """Return whether *phrase* is a word-level substring of a boilerplate string."""

    tokens = " ".join(normalize_keyword(word) for word in phrase.split())
    return any(_contains(boilerplate, tokens) for boilerplate in _NORMALIZED_BOILERPLATE)


def _acronyms(text: str) -> set[str]:
    """Return the uppercase acronyms of *text*, singular and uppercase ("GPUs" -> "GPU").

    Text without any lowercase letter (an all-caps title) says nothing about which
    of its words are acronyms and is ignored.
    """

    if not any(character.islower() for character in text):
        return set()
    return {match.group(1) for match in _ACRONYM_RE.finditer(unicodedata.normalize("NFKC", text))}


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


def _plural_acronym_keys(acronym_forms: dict[str, str]) -> dict[str, str]:
    """Map the lowercase plural keys of acronyms ("gpus") to their singular key."""

    plurals: dict[str, str] = {}
    for key in acronym_forms:
        for plural in (f"{key}s", normalize_keyword(f"{key}s")):
            if plural != key and plural not in acronym_forms:
                plurals[plural] = key
    return plurals


def _merge_plural_acronyms(counts: Counter[str], plurals: dict[str, str]) -> Counter[str]:
    """Count phrases ending in an acronym plural ("multiple gpus") under the singular."""

    merged: Counter[str] = Counter()
    for phrase, count in counts.items():
        head, _, last = phrase.rpartition(" ")
        singular = plurals.get(last, last)
        merged[f"{head} {singular}" if head else singular] += count
    return merged


def _has_non_noun_suffix(word: str) -> bool:
    if word in _SUFFIX_NOUN_EXCEPTIONS or word.endswith("eed"):
        return False
    return any(
        word.endswith(suffix) and len(word) - len(suffix) >= _MIN_SUFFIX_STEM
        for suffix in _NON_NOUN_SUFFIXES
    )


def _phrase_contains_word(phrase: str, word: str) -> bool:
    """Return whether *word* is a token of *phrase*, comparing singular forms."""

    return word in {normalize_keyword(token) for token in phrase.split()}


def _rejected_single_words(
    candidates: set[str],
    kept_phrases: set[str],
    total_counts: Counter[str],
    title_frequency: Counter[str],
    acronyms: set[str],
) -> set[str]:
    """Return single words that are not specific enough to be a concept alone.

    Acronyms are always accepted.  Any other word is rejected when it is generic,
    when at least :data:`REDUNDANT_WORD_RATIO` of its occurrences are inside one
    kept phrase, or when it looks like a verb, participle, adjective or adverb and
    is not a title term (in at least :data:`MIN_TITLE_SUPPORT` titles) that no kept
    phrase contains.
    """

    rejected: set[str] = set()
    for word in candidates:
        if word in acronyms:
            continue
        if word in _GENERIC_SINGLE_WORDS:
            rejected.add(word)
            continue
        containing = [phrase for phrase in kept_phrases if _phrase_contains_word(phrase, word)]
        occurrences = total_counts[word]
        if occurrences and any(
            total_counts[phrase] >= REDUNDANT_WORD_RATIO * occurrences for phrase in containing
        ):
            rejected.add(word)
            continue
        if _has_non_noun_suffix(word) and (containing or title_frequency[word] < MIN_TITLE_SUPPORT):
            rejected.add(word)
    return rejected


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
    input work order, then descending score, then phrase.  A single-word acronym
    is emitted in uppercase ("GPU"); every other term is the normalised phrase.
    """

    if max_per_work <= 0:
        return []
    texts = [
        (work_id, title or "", strip_boilerplate(abstract or ""))
        for work_id, title, abstract in works
    ]
    acronym_forms: dict[str, str] = {}
    for _, title_text, abstract_text in texts:
        for acronym in sorted(_acronyms(title_text) | _acronyms(abstract_text)):
            acronym_forms.setdefault(acronym.casefold(), acronym)
    plural_acronyms = _plural_acronym_keys(acronym_forms)

    per_work: list[tuple[str, Counter[str], set[str]]] = []
    document_frequency: Counter[str] = Counter()
    total_counts: Counter[str] = Counter()
    title_frequency: Counter[str] = Counter()
    for work_id, title_text, abstract_text in texts:
        title_counts = _merge_plural_acronyms(_phrase_counts(title_text), plural_acronyms)
        counts = title_counts + _merge_plural_acronyms(
            _phrase_counts(abstract_text), plural_acronyms
        )
        title_phrases = set(title_counts)
        per_work.append((work_id, counts, title_phrases))
        document_frequency.update(set(counts))
        total_counts.update(counts)
        title_frequency.update(title_phrases)

    qualifying = {
        phrase
        for phrase, df in document_frequency.items()
        if df >= MIN_WORK_SUPPORT and not _is_boilerplate_phrase(phrase)
    }
    qualifying -= _subsumed_phrases(document_frequency, qualifying)
    single_words = {phrase for phrase in qualifying if " " not in phrase}
    qualifying -= _rejected_single_words(
        single_words,
        qualifying - single_words,
        total_counts,
        title_frequency,
        set(acronym_forms),
    )
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
                term=acronym_forms.get(phrase, phrase),
                score=round(score / best, 6),
                kind=KEYPHRASE_KIND,
            )
            for phrase, score in ranked
        )
    return occurrences


__all__ = [
    "KEYPHRASE_KIND",
    "STOP_WORDS",
    "WorkText",
    "extract_keyphrases",
    "strip_boilerplate",
]
