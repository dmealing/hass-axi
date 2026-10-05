"""The console script's entry point, and the version fast path.

An agent's harness probes ``--version`` constantly -- to confirm the tool is
installed, to check whether a fix has shipped -- so the answer is given here,
before anything else is imported. This module imports the standard library's
``sys`` and the package's own ``__init__``, which holds the version and imports
nothing; `hass_axi.cli`, and with it every transport and command module, is
loaded only once the invocation turns out to be something else.

Only a bare, single-argument version flag is answered here. Everything else --
a version flag after a command included -- falls through to `cli.main`, which
stays the single owner of the general case and prints the same line.

This is the one write to stdout that does not pass through `output`, because
importing the redactor is the cost the fast path exists to avoid. It is safe
only because what is written is the package's own version constant: nothing
read from the environment, the arguments or an installation may be printed
from this module.
"""

from __future__ import annotations

import sys

from . import __version__

#: The three spellings, every one of which prints the bare version and exits 0.
VERSION_FLAGS = ("-v", "-V", "--version")


def main(argv: list | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 1 and args[0] in VERSION_FLAGS:
        sys.stdout.write(__version__ + "\n")
        sys.stdout.flush()
        return 0
    from .cli import main as cli_main

    return cli_main(argv)
