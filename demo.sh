#!/usr/bin/env bash
#
# BirdBand end-to-end demo.  Seeds the standard demo bander (cenius / cenius)
# plus a plausible banding session into a scratch BIRDBAND_HOME, then walks the
# happy path and every documented error path.  Non-interactive, offline, exits 0
# when every step behaved as expected.
#
#   ./demo.sh                     use a fresh temporary store, then remove it
#   BIRDBAND_DEMO_KEEP=1 ./demo.sh   keep the scratch store for inspection
#   BIRDBAND_HOME=/tmp/x ./demo.sh   use your own directory instead
#
set -uo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if [ -n "${BIRDBAND_HOME:-}" ]; then
  DEMO_HOME="$BIRDBAND_HOME"
  CLEANUP=0
else
  DEMO_HOME="$(mktemp -d "${TMPDIR:-/tmp}/birdband-demo.XXXXXX")"
  CLEANUP=1
fi
if [ "${BIRDBAND_DEMO_KEEP:-0}" = "1" ]; then
  CLEANUP=0
fi

export BIRDBAND_HOME="$DEMO_HOME"
export BIRDBAND_PASSPHRASE="cenius"
export NO_COLOR=1

PASS=0
FAIL=0

banner() {
  printf '\n================================================================\n'
  printf '  %s\n' "$1"
  printf '================================================================\n'
}

check() {
  local want="$1"
  shift
  printf '\n$ %s\n' "$*"
  "$@"
  local code=$?
  if [ "$code" -ne "$want" ]; then
    printf 'DEMO FAIL: expected exit %s, got %s from: %s\n' "$want" "$code" "$*" >&2
    FAIL=$((FAIL + 1))
  else
    PASS=$((PASS + 1))
  fi
}

banner "BirdBand demo - scratch store: $DEMO_HOME"
printf 'Demo account seeded below: cenius / cenius\n'

banner "1. Help and version"
check 0 "$PYTHON" birdband.py --version
check 0 "$PYTHON" birdband.py --help

banner "2. Create the local bander account and sign in"
check 0 "$PYTHON" birdband.py register cenius
check 0 "$PYTHON" birdband.py login cenius

banner "3. Log three banded birds on the Ridge Trail"
check 0 "$PYTHON" birdband.py add --band 2621-48357 --date 2025-04-01 \
  --site "Ridge Trail" --species "Black-capped Chickadee" --age AHY --sex F \
  --wing 62 --weight 11.4
check 0 "$PYTHON" birdband.py add --band 2621-48360 --date 2025-04-01 \
  --site "Ridge Trail" --species "Dark-eyed Junco" --age HY --sex U \
  --wing 74
check 0 "$PYTHON" birdband.py add --band 2621-48366 --date 2025-04-05 \
  --site "Mill Creek" --species "American Goldfinch" --age SY --sex M \
  --weight 13.1

banner "4. Recapture the first bird twice - the second one dated out of order"
check 0 "$PYTHON" birdband.py recap 2621-48357 --date 2025-04-19 \
  --site "Ridge Trail" --wing 63 --weight 11.6
check 0 "$PYTHON" birdband.py recap 2621-48357 --date 2025-04-02 \
  --site "Mill Creek" --wing 63

banner "5. One band's whole capture history (banding event first, then recaptures in date order)"
check 0 "$PYTHON" birdband.py history 2621-48357

banner "6. The session's work, newest date first"
check 0 "$PYTHON" birdband.py list
check 0 "$PYTHON" birdband.py list --band 2621-48357

banner "7. Error paths - each one plain line and a non-zero exit code"
check 1 "$PYTHON" birdband.py add --band 2621-48357 --date 2025-04-03 \
  --site "Ridge Trail"
check 1 "$PYTHON" birdband.py add --band 2621-48357 --date 04/02/2025 \
  --site "Ridge Trail"
check 1 "$PYTHON" birdband.py add --band 1234 --date 2025-04-01 --site "Ridge Trail"
check 1 "$PYTHON" birdband.py add --band 2621-48370 --date 2025-04-01 \
  --site "Ridge Trail" --wing 5000
check 1 "$PYTHON" birdband.py add --band 2621-48370 --date 2025-04-01 \
  --site "Ridge Trail" --weight abc
check 1 "$PYTHON" birdband.py recap 9999-00000 --date 2025-04-02 --site "Mill Creek"
check 1 "$PYTHON" birdband.py recap 2621-48357 --date 04/02/2025 --site "Mill Creek"
check 1 "$PYTHON" birdband.py history 9999-00000
check 2 "$PYTHON" birdband.py list --nonsense
check 2 "$PYTHON" birdband.py frobnicate
check 2 "$PYTHON" birdband.py add

banner "8. A corrupt store is reported, never a traceback"
printf '{"version": 1, "banding": [' >"$DEMO_HOME/records.json"
check 1 "$PYTHON" birdband.py list
check 1 "$PYTHON" birdband.py history 2621-48357

banner "9. Sign out - the write commands are refused again"
check 0 "$PYTHON" birdband.py logout
check 1 "$PYTHON" birdband.py add --band 2621-48390 --date 2025-05-02 \
  --site "Ridge Trail"

banner "Result"
printf 'steps passed: %s   steps failed: %s\n' "$PASS" "$FAIL"
printf 'demo credentials: cenius / cenius\n'

if [ "$CLEANUP" = "1" ]; then
  rm -rf "$DEMO_HOME"
  printf 'scratch store removed (set BIRDBAND_DEMO_KEEP=1 to keep it)\n'
else
  printf 'scratch store kept at %s\n' "$DEMO_HOME"
  printf 'to record against it again: BIRDBAND_HOME=%s ./demo.sh\n' "$DEMO_HOME"
fi

if [ "$FAIL" -ne 0 ]; then
  exit 1
fi
exit 0
