"""Names as people type them: folded for comparison, and matched when they miss.

Pure rules, with nothing of the CLI, a transport or the output boundary in
them, so they can move to wherever a second client can import them. Two things
live here.

**Folding.** A phone names its own entities with a typographic apostrophe, and
a name that can only be found by pasting that character cannot be found by
anybody typing it. Every search and every resolver that takes a name compares
through :func:`fold`, on both sides, so one spelling finds the same thing
everywhere.

**Near misses.** A name that matches nothing is answered with the ones nearest
it, rather than with an offer to create what was mistyped.
"""

from __future__ import annotations

import difflib

#: Typographic quotation marks, folded to the ones a keyboard types. A phone
#: names its own entities with U+2019, and a name that can only be found by
#: pasting that character cannot be found by anybody typing it.
_FOLDED = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u02bc": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)


def fold(text) -> str:
    """A name as it is compared: trimmed, case-folded, quotation marks plain.

    Every search and every resolver that takes a name compares through this, on
    both sides, so the same spelling finds the same thing everywhere.
    """
    return str(text or "").translate(_FOLDED).strip().casefold()


#: How many near misses a failed lookup offers.
MAX_CANDIDATES = 5


def close_matches(needle: str, labelled: list) -> list:
    """The entries of ``labelled`` whose names are nearest ``needle``.

    ``labelled`` is ``(name, rendering)`` pairs. A name that contains the
    needle, or is contained by it, ranks ahead of one that is merely similar:
    `Kitch` is asking for `Kitchen` before it is asking for `Kitsch`.
    """
    wanted = fold(needle)
    if not wanted:
        return []
    names = {fold(name): rendering for name, rendering in labelled if fold(name)}
    found = [name for name in names if wanted in name or name in wanted]
    for name in difflib.get_close_matches(wanted, list(names), n=MAX_CANDIDATES, cutoff=0.6):
        if name not in found:
            found.append(name)
    return [names[name] for name in found[:MAX_CANDIDATES]]
