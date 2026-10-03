#!/usr/bin/env sh
# Create this checkout's own virtualenv and install the package into it.
#
# Isolation is the whole point of this script, and it is not a style
# preference. This tool is normally installed as a user-level tool with its
# own environment and a launcher on PATH. An editable install into whatever
# interpreter happens to be ambient overwrites that launcher, points it at
# this checkout, and leaves an editable pointer in the user site — so deleting
# the checkout, which is the ordinary end of a throwaway clone, leaves the
# reader's own installation dead with `ModuleNotFoundError`. A virtualenv
# cannot do that: everything written lives under .venv, and removing the
# checkout removes all of it.
#
# .venv, and calling tools as .venv/bin/<tool>, is what
# .github/workflows/ci.yml already does, so there is one pattern here and not
# two. Set PYTHON to build the environment from a different interpreter.
set -e

root=$(git rev-parse --show-toplevel)
cd "$root"

python=${PYTHON:-python3}

if [ -e .venv ] && [ ! -x .venv/bin/python ]; then
  echo "dev-setup: .venv exists and is not a virtualenv; move it aside and re-run" >&2
  exit 1
fi

if [ -x .venv/bin/python ]; then
  echo "venv: reusing .venv ($(.venv/bin/python -c 'import platform; print(platform.python_version())'))"
else
  "$python" -m venv .venv
  echo "venv: created .venv ($(.venv/bin/python -c 'import platform; print(platform.python_version())'))"
fi

.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/pip install -e ".[dev]"
echo "install: this checkout is installed in .venv, and nowhere else"

echo "next: run every development command out of .venv/bin, never off PATH"
echo "  .venv/bin/pytest"
echo "  .venv/bin/ruff check . && .venv/bin/ruff format --check ."
echo "  .venv/bin/hass-axi setup skill --check"
echo "  scripts/install-hooks.sh   # once per clone"
