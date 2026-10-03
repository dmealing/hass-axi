"""The documented developer setup must be isolated by construction.

The setup block is the first command a contributor or an agent runs, and until
this change it was an editable install into whatever interpreter happened to be
ambient. That is not a local decision: this tool is normally installed as an
isolated user-level tool, so an ambient editable install overwrites the
launcher in ``~/.local/bin``, binds it to the ambient interpreter and leaves an
editable pointer in the user site — and deleting the checkout, the ordinary end
of a throwaway clone, kills the reader's own installed command. It broke one
AXI CLI outright and pinned a sibling two releases behind, with nothing to
indicate anything was wrong.

``.github/workflows/ci.yml`` already got this right, which is the shape of the
fix: the workflows were correct and the documentation was wrong, so the
documented path becomes the path CI already proves works — ``.venv``, and tools
called as ``.venv/bin/<tool>``.

**The needle below is built at run time rather than written out**, the same way
``leakcheck.synthetic_jwt`` builds a credential shape, because this file is
swept along with every other tracked file and a literal here would be a finding
in the guard's own source. The same constraint falls on the prose in
``README.md`` and ``AGENTS.md``: describe the unsafe command, never quote it,
so no reader can copy it out of a warning about it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = REPO_ROOT / "scripts" / "dev-setup.sh"

# Built rather than spelled: see the module docstring. An editable install is
# only ever safe when the pip running it belongs to this checkout's virtualenv,
# so that spelling is what the pattern excuses — the hazard is the bare form.
EDITABLE_INSTALL = re.compile(r"pip\s+install\b[^\n]*\s-e\b")
VENV_PIP = ".venv/bin/pip"

# `.github/` is CI, which has created its own virtualenv since before this
# guard existed, and `scripts/dev-setup.sh` is the safe entry point itself —
# the one file allowed to describe in full the hazard it replaces.
# `scripts/ci-local.sh --matrix` installs only into throwaway `uv` venvs it
# has just created and deletes on exit.
ALLOWED = (r"\.github/", r"scripts/dev-setup\.sh", r"scripts/ci-local\.sh")


def tracked_text_files():
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for name in out.split("\0"):
        if not name or any(re.match(pattern, name) for pattern in ALLOWED):
            continue
        path = REPO_ROOT / name
        if not path.is_file():
            continue
        try:
            yield name, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def test_no_tracked_file_offers_an_editable_install_a_reader_could_copy():
    offenders = [
        f"{name}:{n}: {line.strip()}"
        for name, text in tracked_text_files()
        for n, line in enumerate(text.splitlines(), 1)
        if EDITABLE_INSTALL.search(line) and VENV_PIP not in line
    ]
    assert not offenders, (
        "an editable install outside a virtualenv replaces the reader's own "
        "installation of this tool; document scripts/dev-setup.sh instead:\n" + "\n".join(offenders)
    )


def test_the_setup_script_is_committed_and_runnable():
    assert SETUP_SCRIPT.is_file()
    assert os.access(SETUP_SCRIPT, os.X_OK), "scripts/dev-setup.sh must be executable"
    assert SETUP_SCRIPT.read_text(encoding="utf-8").startswith("#!")


def test_the_setup_script_and_ci_build_the_same_environment():
    """One pattern in this repository, not two.

    CI's isolation is the reason its jobs see only what they installed; a
    developer setup that chose a different directory or invocation style would
    make the documented commands wrong for one of the two readers.
    """
    script = SETUP_SCRIPT.read_text(encoding="utf-8")
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    # ci.yml builds the venv and then runs each check through scripts/ci-local.sh.
    assert "scripts/ci-local.sh --only test" in ci
    ci += (REPO_ROOT / "scripts" / "ci-local.sh").read_text(encoding="utf-8")
    for shared in ("-m venv", ".venv/bin/pip install", ".venv/bin/pytest"):
        assert shared in script, f"scripts/dev-setup.sh does not use {shared!r}"
        assert shared in ci, f"ci.yml does not use {shared!r}"
    for printed in (".venv/bin/ruff", ".venv/bin/hass-axi setup skill --check"):
        assert printed in script, f"scripts/dev-setup.sh does not point at {printed!r}"


def test_both_documents_point_a_reader_at_the_setup_script():
    for name in ("README.md", "AGENTS.md"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "scripts/dev-setup.sh" in text, f"{name} does not document the setup script"
        assert ".venv/bin/pytest" in text, f"{name} still runs pytest off PATH"
