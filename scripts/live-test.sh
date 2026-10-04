#!/usr/bin/env bash
# Run the opt-in live suite: this build, against a real Home Assistant.
#
# Usage:
#   scripts/live-test.sh --house           # reads, previews and loopback faults (tiers A, B, D)
#   scripts/live-test.sh --house-writes    # --house, plus self-reversing writes (tier C)
#   scripts/live-test.sh --lab             # a disposable container: rejected credentials
#                                          #   and destructive writes (tier E)
#   scripts/live-test.sh --all             # --house-writes and --lab
#   scripts/live-test.sh --house --env-file PATH   # read HA_URL and HA_TOKEN from a file
#   scripts/live-test.sh --house -- -k sweeps      # anything after -- goes to pytest
#
# Nothing here runs from `pytest`, from scripts/ci-local.sh or from the gate:
# pyproject.toml deselects the `live` marker and every test in tests/live/ skips
# without HASS_AXI_LIVE=1. This script is the one way in, and it is run by hand.
#
# The house tiers read HA_URL and HA_TOKEN from the environment, or from
# --env-file, which is sourced in this process only and never printed. They
# send no invalid credential, call no service on a real device, and rename or
# move nothing real; tier C creates and removes one scratch area and one
# notification, and sets two stored registry values to themselves. The lab tier
# needs docker and never touches the installation at all.
#
# It makes no LLM call and uses no paid API, and it schedules nothing.
set -euo pipefail

cd "$(dirname "$0")/.."

house=0
writes=0
lab=0
env_file=""
extra=()

while [ $# -gt 0 ]; do
  case "$1" in
    --house) house=1; shift ;;
    --house-writes) house=1; writes=1; shift ;;
    --lab) lab=1; shift ;;
    --all) house=1; writes=1; lab=1; shift ;;
    --env-file)
      [ $# -ge 2 ] || { echo "live-test: --env-file needs a path" >&2; exit 2; }
      env_file=$2; shift 2 ;;
    --) shift; extra=("$@"); break ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "live-test: unknown argument $1 (try --help)" >&2; exit 2 ;;
  esac
done

if [ "$house" -eq 0 ] && [ "$lab" -eq 0 ]; then
  echo "live-test: choose --house, --house-writes, --lab or --all" >&2
  exit 2
fi

pytest_bin=.venv/bin/pytest
[ -x "$pytest_bin" ] || { echo "live-test: no .venv; run scripts/dev-setup.sh first" >&2; exit 2; }

if [ -n "$env_file" ]; then
  [ -r "$env_file" ] || { echo "live-test: cannot read the --env-file" >&2; exit 2; }
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a
fi

export HASS_AXI_LIVE=1
status=0

if [ "$house" -eq 1 ]; then
  if [ -z "${HA_URL:-}${HASS_SERVER:-}" ] || [ -z "${HA_TOKEN:-}${HASS_TOKEN:-}" ]; then
    echo "live-test: the house tiers need HA_URL and HA_TOKEN (or --env-file)" >&2
    exit 2
  fi
  if [ "$writes" -eq 1 ]; then
    export HASS_AXI_LIVE_WRITES=1
    echo "live-test: house tiers A, B, C and D"
  else
    echo "live-test: house tiers A, B and D (reads, previews, loopback faults)"
  fi
  "$pytest_bin" -m live tests/live --deselect tests/live/test_lab.py -q -rs "${extra[@]}" || status=$?
fi

if [ "$lab" -eq 1 ]; then
  command -v docker >/dev/null 2>&1 || { echo "live-test: --lab needs docker on PATH" >&2; exit 2; }
  echo "live-test: lab tier E, in a disposable container"
  HASS_AXI_LIVE_LAB=1 "$pytest_bin" -m live tests/live/test_lab.py -q -rs "${extra[@]}" || status=$?
fi

exit "$status"
