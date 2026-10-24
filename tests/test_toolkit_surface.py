"""The public surface of `hass_axi.toolkit`, pinned by name and signature.

Other programs import these functions, and the package docstring promises that a
listed name is not removed or changed in meaning without a release note. Nothing
else fails if one is renamed, loses a parameter or gains a required one, so this
does: a deliberate change to the surface is made here, in the same commit, where
a reviewer sees it.

Annotations are strings (`from __future__ import annotations`), so the rendered
signatures are the same on every supported interpreter.
"""

from __future__ import annotations

import inspect

import pytest

from hass_axi import toolkit
from hass_axi.toolkit import names, recorder, shapes

SURFACE = {
    names: {
        "close_matches": "(needle, labelled: 'Iterable') -> 'list'",
        "fold": "(text) -> 'str'",
        "matches": "(needle, *values) -> 'bool'",
        "nearest": "(needle, entries: 'Iterable', labels: 'Callable[[Any], Iterable]') -> 'list'",
        "resolve": (
            "(needle, entries: 'Iterable', *, ident: 'Callable[[Any], Any]', "
            "name: 'Callable[[Any], Any]', by_name: 'bool' = True) -> 'Resolution'"
        ),
    },
    recorder: {
        "circular_mean": "(degrees: 'list') -> 'float'",
        "default_period": "(seconds: 'float') -> 'str'",
        "inside": "(rows: 'list', start, end) -> 'list'",
        "iso": "(moment: 'datetime') -> 'str'",
        "kind_of": "(meta: 'dict') -> 'str'",
        "missing_buckets": "(rows: 'list', start, period: 'str') -> 'str'",
        "overhang": "(rows: 'list', start, end) -> 'str'",
        "parse_timestamp": "(value) -> 'datetime | None'",
        "sum_caveats": "(rows: 'list', unit: 'str') -> 'list'",
        "summarize": (
            "(meta: 'dict', rows: 'list', start, period: 'str', *, end=None, hourly=None) -> 'dict'"
        ),
        "unit_of": "(meta: 'dict') -> 'str'",
        "wants_hourly": "(period: 'str', seconds: 'float') -> 'bool'",
    },
    shapes: {
        "describe": "(value) -> 'str'",
        "health_fault": "(value) -> 'str | None'",
        "is_entity_id": "(value: 'str') -> 'bool'",
        "is_text": "(content_type: 'str', raw: 'bytes') -> 'bool'",
        "shape_fault": "(value, kind: 'type') -> 'str | None'",
    },
}


def _public_functions(module) -> dict:
    return {
        name: str(inspect.signature(obj))
        for name, obj in vars(module).items()
        if inspect.isfunction(obj)
        and obj.__module__ == module.__name__
        and not name.startswith("_")
    }


def test_the_package_exports_exactly_the_three_submodules():
    assert sorted(toolkit.__all__) == ["names", "recorder", "shapes"]


@pytest.mark.parametrize("module", list(SURFACE), ids=lambda m: m.__name__)
def test_each_submodule_publishes_exactly_the_pinned_functions_and_signatures(module):
    """A change to a name or a signature is a change to the library's contract."""
    assert _public_functions(module) == SURFACE[module]
