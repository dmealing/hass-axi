"""Names as people type them: folded for comparison, resolved, and matched when they miss.

Pure rules, with nothing of the CLI, a transport or the output boundary in
them. Three things live here.

**Folding.** A phone names its own entities with a typographic apostrophe, an
editor turns a hyphen into a dash and three dots into an ellipsis, and a
keyboard may not have the accent a name was given. A name that can only be
found by pasting those characters cannot be found by anybody typing it. Every
search and every resolver that takes a name compares through :func:`fold`, on
both sides, so one spelling finds the same thing everywhere.

**Resolution.** :func:`resolve` finds the one entry a typed value names, by
identifier first and then by folded name -- and when the folded name is shared
it reports every entry that shares it rather than picking one. Folding makes
ties more likely than exact comparison did (`Cafe` and `Café` are one name
here), so a tie is an answer of its own, with the candidates in it.

**Near misses.** A value that matches nothing is answered with the entries
nearest it, as data. What a caller says about them is the caller's business.
"""

from __future__ import annotations

import difflib
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Callable

#: Characters a keyboard does not type, folded to the ones it does. Quotation
#: marks, every dash and hyphen, and the minus sign; the ellipsis and the
#: no-break space are taken apart by the compatibility decomposition below.
_FOLDED = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "ʼ": "'",
        "′": "'",
        "´": "'",
        "`": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "″": '"',
        "«": '"',
        "»": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
        "­": "",
    }
)


def fold(text) -> str:
    """A name as it is compared.

    Case is folded; typographic quotation marks, dashes and the ellipsis become
    the characters a keyboard types; accents are dropped; runs of white space
    become one space and the ends are trimmed. The result is for comparing and
    is never shown: two names that fold alike are still two names.
    """
    value = str(text if text is not None else "").translate(_FOLDED).casefold()
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.translate(_FOLDED).split())


def matches(needle, *values) -> bool:
    """Whether the folded ``needle`` occurs in any of the folded ``values``."""
    wanted = fold(needle)
    return any(wanted in fold(value) for value in values)


#: How many near misses a failed lookup offers.
MAX_CANDIDATES = 5

#: How alike two folded names have to be to count as a near miss.
SIMILARITY = 0.6


def nearest(needle, entries: Iterable, labels: Callable[[Any], Iterable]) -> list:
    """The entries whose labels are nearest ``needle``, nearest first.

    ``labels`` gives the strings an entry answers to -- its name, its
    identifier. A label that contains the needle, or is contained by it, ranks
    ahead of one that is merely similar: `Kitch` is asking for `Kitchen` before
    it is asking for `Kitsch`. An entry is returned once however many of its
    labels matched.
    """
    wanted = fold(needle)
    if not wanted:
        return []
    by_label: dict = {}
    for entry in entries:
        for label in labels(entry):
            folded = fold(label)
            if folded:
                by_label.setdefault(folded, []).append(entry)
    ordered = [label for label in by_label if wanted in label or label in wanted]
    similar = difflib.get_close_matches(wanted, list(by_label), n=MAX_CANDIDATES, cutoff=SIMILARITY)
    ordered.extend(label for label in similar if label not in ordered)
    found: list = []
    for label in ordered:
        for entry in by_label[label]:
            if not any(entry is seen for seen in found):
                found.append(entry)
    return found[:MAX_CANDIDATES]


def close_matches(needle, labelled: Iterable) -> list:
    """The values of ``(name, value)`` pairs whose names are nearest ``needle``."""
    pairs = list(labelled)
    return [value for _, value in nearest(needle, pairs, lambda pair: [pair[0]])]


@dataclass(frozen=True)
class Resolution:
    """What a typed value named, as data.

    Exactly one of three things is true. ``match`` is the entry it named.
    ``ties`` holds every entry that shares the folded name, when more than one
    does -- a tie is reported, never broken. Or neither, and ``near`` holds the
    entries nearest to what was typed, which may be none.
    """

    match: Any = None
    ties: tuple = ()
    near: tuple = ()
    #: How the match was made: ``"id"``, ``"name"`` or ``""``.
    by: str = ""

    @property
    def found(self) -> bool:
        return self.match is not None

    @property
    def ambiguous(self) -> bool:
        return bool(self.ties)


def resolve(
    needle,
    entries: Iterable,
    *,
    ident: Callable[[Any], Any],
    name: Callable[[Any], Any],
    by_name: bool = True,
) -> Resolution:
    """Find the entry ``needle`` names, by identifier and then by folded name.

    The identifier is compared exactly and tried first, so an entry whose name
    happens to be another entry's identifier cannot shadow it. Then the name,
    folded on both sides: one entry is a match, several are a tie and all of
    them are returned. With ``by_name`` off only the identifier is tried.
    """
    entries = list(entries)
    typed = str(needle if needle is not None else "")
    for entry in entries:
        if ident(entry) == typed or ident(entry) == typed.strip():
            return Resolution(match=entry, by="id")
    wanted = fold(typed)
    if by_name and wanted:
        same = [entry for entry in entries if fold(name(entry)) == wanted]
        if len(same) == 1:
            return Resolution(match=same[0], by="name")
        if same:
            return Resolution(ties=tuple(same))
    labels = (lambda e: [name(e), ident(e)]) if by_name else (lambda e: [ident(e)])
    return Resolution(near=tuple(nearest(typed, entries, labels)))
