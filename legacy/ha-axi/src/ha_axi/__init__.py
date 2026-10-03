"""The transitional ``ha-axi`` package. The tool it installed is ``hass-axi`` now.

This module replaces the old ``ha_axi`` package in place, so upgrading removes
the 0.7.1 code rather than leaving it beside the renamed tool. Its ``ha-axi``
command forwards to ``hass-axi`` unchanged -- same arguments, same output, same
exit code -- so a script or a session hook written for the old name keeps
working until it is moved. The notice goes to stderr, because stdout is the
document an agent parses and a deprecation is not part of it.
"""

from __future__ import annotations

import sys

NOTICE = (
    "ha-axi: this tool is now hass-axi -- run `hass-axi` instead, then "
    "`hass-axi setup hooks` and `pip uninstall ha-axi`"
)


def main(argv: list | None = None) -> int:
    sys.stderr.write(NOTICE + "\n")
    sys.stderr.flush()
    from hass_axi.cli import main as hass_axi_main

    return hass_axi_main(argv)
