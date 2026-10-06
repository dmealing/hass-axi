"""Command modules. Each exposes a ``COMMAND`` declaration and a ``run`` entry point.

The columns a list command can print are declared in ``metaobjects/meta.rows.yaml``
and generated into :mod:`hass_axi.model.rows`; a module reads its row's vocabulary
from there and keeps no column list of its own.
"""

from __future__ import annotations
