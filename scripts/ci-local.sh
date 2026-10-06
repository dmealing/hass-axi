#!/usr/bin/env bash
# Local CI — every check `.github/workflows/ci.yml` runs, runnable without GitHub.
#
# GitHub Actions is disabled on this repository, so the workflow's jobs run
# nowhere unless something local runs them. This script is that something: the
# no-mistakes gate calls it (see `.no-mistakes.yaml`), a developer can run it
# before pushing, and `ci.yml` calls it one section per job, so the checks are
# the same whether or not Actions ever comes back.
#
# Sections, in the order a bare run executes them:
#
#   leakcheck  scripts/leakcheck.py --demo, then the scan of every tracked file
#   commits    scripts/commitcheck.py --demo, then --since-release with
#              --pull-requests require, or auto with no GitHub token (see below)
#   lint       ruff check . && ruff format --check .
#   test       pytest, once, on the interpreter that built .venv
#   skill      hass-axi setup skill --check
#   model      metaobjects verify --codegen, then the capture report
#
# Usage:
#   scripts/ci-local.sh                        # every section
#   scripts/ci-local.sh --only lint            # one section; repeat --only for more
#   scripts/ci-local.sh --matrix               # also run pytest on every MATRIX_PYTHONS
#   scripts/ci-local.sh --help
#
# THE TEST MATRIX DECISION. `test` runs the suite once, on the interpreter that
# built .venv. That is what the gate runs, because it is the cost every change
# pays. The supported range (3.9 through 3.12; `requires-python = ">=3.9"` in
# pyproject.toml) is checked on demand with `--matrix`, which builds a throwaway
# `uv` venv per version and runs pytest in each. `--matrix` needs `uv` on PATH
# and fails if it is missing, because a matrix that silently ran nothing reads
# like one that passed. Override the versions with MATRIX_PYTHONS="3.9 3.12".
#
# THE TOKEN. `commits` reads pull request bodies from GitHub, because a body can
# replace a commit message outright (AGENTS.md, "Releasing"). The token comes
# from GITHUB_TOKEN, then GH_TOKEN, then `gh auth token`. When none can be
# obtained the --since-release audit still runs, over every git-side message,
# under --pull-requests auto: only the pull-request-body half is skipped, the
# SKIP line says so, and commitcheck prints its own NOT-consulted note beside
# the verdict.
#
# THE MODEL TOOLCHAIN. `model` proves the committed generated files are what
# metaobjects/ and the shared generators in axi-toolkit emit. The toolchain needs
# a newer Python than this package's floor, so it runs under `uvx --python 3.12`
# and never touches .venv; like `--matrix`, it fails without `uv` on PATH rather
# than passing unrun.
#
# NOT COVERED. hygiene.yml's pull request title and body leak scan
# (`leakcheck.py --pull-request N`) has no local home: the text it scans exists
# only on GitHub, after the pull request is opened.
#
# The environment is .venv, built by scripts/dev-setup.sh when it is missing,
# and every tool is called as .venv/bin/<tool> — never off PATH, for the reason
# AGENTS.md gives under "Continuous integration".
#
# To reuse this shape elsewhere: each section is one function named `sec_<name>`
# and one entry in SECTIONS. Nothing else needs to change.
set -uo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root" || exit 1

SECTIONS=(leakcheck commits lint test skill model)
METAOBJECTS=${METAOBJECTS:-"metaobjects==1.0.13"}
AXI_TOOLKIT=${AXI_TOOLKIT:-"axi-toolkit[metagen]==0.6.0"}
MATRIX_PYTHONS=${MATRIX_PYTHONS:-"3.9 3.10 3.11 3.12"}

usage() { sed -n '2,/^set -uo/p' "$0" | sed '$d; s/^# \{0,1\}//'; }

only=()
matrix=0
while [ $# -gt 0 ]; do
  case $1 in
    --only)
      [ $# -ge 2 ] || { echo "ci-local: --only needs a section: ${SECTIONS[*]}" >&2; exit 2; }
      case " ${SECTIONS[*]} " in
        *" $2 "*) only+=("$2") ;;
        *) echo "ci-local: unknown section '$2'; sections: ${SECTIONS[*]}" >&2; exit 2 ;;
      esac
      shift 2 ;;
    --matrix) matrix=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "ci-local: unknown argument '$1'; see --help" >&2; exit 2 ;;
  esac
done
[ ${#only[@]} -gt 0 ] || only=("${SECTIONS[@]}")

ensure_venv() {
  if [ ! -x .venv/bin/hass-axi ]; then
    scripts/dev-setup.sh
  fi
}

sec_leakcheck() {
  python3 scripts/leakcheck.py --demo
  python3 scripts/leakcheck.py
}

sec_commits() {
  python3 scripts/commitcheck.py --demo
  # Ask commitcheck's own resolver whether a token exists, so the order it
  # tries them in is written once.
  if python3 -c 'import sys; sys.path.insert(0, "scripts"); import commitcheck; sys.exit(not commitcheck.github_token())'; then
    python3 scripts/commitcheck.py --since-release --pull-requests require
  else
    echo "SKIP: pull request bodies not consulted: no GitHub token (GITHUB_TOKEN, GH_TOKEN, gh auth token)"
    python3 scripts/commitcheck.py --since-release --pull-requests auto
  fi
}

sec_lint() {
  ensure_venv
  .venv/bin/ruff check .
  .venv/bin/ruff format --check .
}

sec_test() {
  ensure_venv
  .venv/bin/pytest
  [ "$matrix" -eq 1 ] || return 0
  command -v uv >/dev/null 2>&1 || { echo "ci-local: --matrix needs uv on PATH" >&2; return 1; }
  local v
  # Not local: the EXIT trap fires after this function has returned.
  matrix_tmp=$(mktemp -d)
  trap 'rm -rf "$matrix_tmp"' EXIT
  for v in $MATRIX_PYTHONS; do
    echo "--- pytest on Python $v"
    uv venv --quiet --python "$v" "$matrix_tmp/py$v"
    uv pip install --quiet --python "$matrix_tmp/py$v/bin/python" -e ".[dev]"
    "$matrix_tmp/py$v/bin/pytest"
  done
}

sec_skill() {
  ensure_venv
  .venv/bin/hass-axi setup skill --check
}

sec_model() {
  command -v uvx >/dev/null 2>&1 || { echo "ci-local: model needs uv on PATH" >&2; return 1; }
  uvx --quiet --python 3.12 --from "$METAOBJECTS" --with "$AXI_TOOLKIT" metaobjects verify --codegen
  # Both directions of the capture check, in words; pytest runs the failing one.
  python3 tests/hamodel/capture_contract.py
}

# Each section runs in its own subshell under `set -e`, so its first failing
# command ends that section and not the run. The subshell must not sit in an
# `if` condition: bash ignores `set -e` there, and a section would run on past
# its own failure and report the last command's status. The same holds for the
# left side of `||`, which is why the status is read on the next line.
failed=()
for s in "${only[@]}"; do
  printf '\n========== %s ==========\n' "$s"
  (set -e; "sec_$s")
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "PASS: $s"
  else
    echo "FAIL: $s" >&2
    failed+=("$s")
  fi
done

echo
if [ ${#failed[@]} -gt 0 ]; then
  echo "ci-local: FAILED: ${failed[*]}" >&2
  exit 1
fi
echo "ci-local: passed: ${only[*]}"
