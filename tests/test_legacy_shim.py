"""The transitional `ha-axi` package under `legacy/ha-axi/`.

It is published once, by hand, and never again, so the suite is the only thing
that ever runs it before somebody's upgrade does.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

LEGACY = Path(__file__).resolve().parent.parent / "legacy" / "ha-axi"


def _shim():
    spec = importlib.util.spec_from_file_location(
        "ha_axi_shim", LEGACY / "src" / "ha_axi" / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_old_command_forwards_with_a_notice_on_stderr_only(capsys):
    from hass_axi import __version__

    code = _shim().main(["--version"])
    captured = capsys.readouterr()
    assert code == 0
    assert f'"hass-axi": {__version__}' in captured.out
    assert "now hass-axi" not in captured.out
    assert "this tool is now hass-axi" in captured.err


def test_the_old_command_keeps_the_exit_code_of_the_new_one(capsys):
    assert _shim().main(["no-such-command"]) == 2


def test_the_shim_depends_on_the_renamed_package_and_ships_no_code_of_its_own():
    text = (LEGACY / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^name = "ha-axi"$', text, re.MULTILINE)
    assert re.search(r'^dependencies = \["hass-axi>=[0-9.]+"\]$', text, re.MULTILINE)
    assert re.search(r'^ha-axi = "ha_axi:main"$', text, re.MULTILINE)
    sources = sorted(p.name for p in (LEGACY / "src" / "ha_axi").iterdir() if p.suffix == ".py")
    assert sources == ["__init__.py"]


def test_the_shim_is_newer_than_the_last_release_under_the_old_name():
    """0.7.1 was the last `ha-axi` with code in it; pip only upgrades to something newer."""
    text = (LEGACY / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([0-9.]+)"$', text, re.MULTILINE).group(1)
    assert tuple(int(part) for part in version.split(".")) > (0, 7, 1)
