"""The library surface of hass-axi: rules about Home Assistant, as plain functions.

Everything a program other than the CLI can import lives under this package.
Plain values go in and plain values come out; a failed lookup or a response of
the wrong shape is reported as data, never raised as a CLI error and never
phrased as a command to run. Nothing here imports the command modules, the
argument parser, the output boundary or a transport, and a test fails if that
stops being true.

* :mod:`hass_axi.toolkit.names` -- names compared the way people type them,
  resolution that reports a tie instead of picking one, and near misses.
* :mod:`hass_axi.toolkit.shapes` -- whether an answer is really Home
  Assistant's, and whether a value can be an entity id.
* :mod:`hass_axi.toolkit.recorder` -- what recorder statistics total to inside
  a window, and what their buckets show about themselves.

It is new, and it may grow. The CLI's own commands are built on it.
"""

from __future__ import annotations

from . import names, recorder, shapes

__all__ = ["names", "recorder", "shapes"]
