#!/usr/bin/env bash
#
# BirdBand smoke run: the happy path and every documented error path, each with
# an asserted exit code and error shape.  Uses a scratch BIRDBAND_HOME and never
# touches your real store.  Exits non-zero if any step misbehaves.
#
#   bash tests/smoke.sh
#
set -uo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
SMOKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/birdband-smoke.XXXXXX")"
trap 'rm -rf "$SMOKE_HOME"' EXIT

export BIRDBAND_HOME="$SMOKE_HOME"
export BIRDBAND_PASSPHRASE="smoke-test-passphrase"
export NO_COLOR=1

FAILURES=0
LAST_OUT=""
LAST_ERR=""

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1" >&2; FAILURES=$((FAILURES + 1)); }

# expect_exit <wanted-code> <label> <command...>
expect_exit() {
  local want="$1"
  local label="$2"
  shift 2
  local out_file err_file got
  out_file="$(mktemp)"
  err_file="$(mktemp)"
  "$@" >"$out_file" 2>"$err_file"
  got=$?
  LAST_OUT="$(cat "$out_file")"
  LAST_ERR="$(cat "$err_file")"
  rm -f "$out_file" "$err_file"
  if [ "$got" -eq "$want" ]; then
    pass "$label (exit $got)"
  else
    fail "$label (exit $got, wanted $want)"
  fi
}

expect_one_error_line() {
  local label="$1"
  local lines
  lines="$(printf '%s\n' "$LAST_ERR" | grep -c .)"
  if [ "$lines" -eq 1 ] && [ -n "$LAST_ERR" ]; then
    pass "$label: exactly one error line"
  else
    fail "$label: wanted one error line, got $lines"
  fi
  case "$LAST_ERR" in
    *Traceback*) fail "$label: a Python traceback leaked to stderr" ;;
  esac
}

expect_output_contains() {
  local label="$1"
  local needle="$2"
  case "$LAST_OUT" in
    *"$needle"*) pass "$label" ;;
    *) fail "$label: stdout did not contain '$needle'" ;;
  esac
}

expect_error_contains() {
  local label="$1"
  local needle="$2"
  case "$LAST_ERR" in
    *"$needle"*) pass "$label" ;;
    *) fail "$label: stderr did not contain '$needle'" ;;
  esac
}

printf 'BirdBand smoke run against %s\n\n' "$SMOKE_HOME"

printf -- '-- usage -------------------------------------------------\n'
expect_exit 0 "birdband --help" "$PYTHON" birdband.py --help
for command in add list recap history register login logout; do
  expect_output_contains "--help lists $command" "$command"
done
expect_exit 0 "birdband --version" "$PYTHON" birdband.py --version

printf '\n-- sign-in ----------------------------------------------\n'
expect_exit 0 "register Dana" "$PYTHON" birdband.py register Dana
expect_exit 1 "register Dana twice" "$PYTHON" birdband.py register Dana
expect_one_error_line "duplicate account"
expect_exit 1 "login without an account name" "$PYTHON" birdband.py login Nobody
expect_one_error_line "unknown username"

export BIRDBAND_PASSPHRASE="wrong-passphrase"
expect_exit 1 "login with the wrong passphrase" "$PYTHON" birdband.py login Dana
expect_one_error_line "wrong passphrase"
export BIRDBAND_PASSPHRASE="smoke-test-passphrase"

expect_exit 0 "login Dana" "$PYTHON" birdband.py login Dana
expect_output_contains "signed in" "signed in as Dana"
if [ "$(stat -c '%a' "$SMOKE_HOME/session.json")" = "600" ]; then
  pass "session file is mode 0600"
else
  fail "session file is mode $(stat -c '%a' "$SMOKE_HOME/session.json"), wanted 600"
fi
if grep -q "smoke-test-passphrase" "$SMOKE_HOME/credentials.json"; then
  fail "the passphrase was stored in plaintext"
else
  pass "credentials.json holds only a salt and a PBKDF2 hash"
fi

printf '\n-- banding ----------------------------------------------\n'
expect_exit 0 "add 1234-56789" "$PYTHON" birdband.py add --band 1234-56789 \
  --date 2025-04-01 --site "Ridge Trail" --species "Black-capped Chickadee" \
  --age AHY --sex F --wing 62 --weight 11.4
expect_output_contains "confirmation echoes the band" "1234-56789"
expect_output_contains "confirmation echoes the wing chord" "62 mm"
expect_output_contains "confirmation echoes the weight" "11.4 g"
expect_exit 0 "add 1234-56790 without measurements" "$PYTHON" birdband.py add \
  --band 1234-56790 --date 2025-04-05 --site "Mill Creek" --species "Dark-eyed Junco"
expect_exit 1 "duplicate band" "$PYTHON" birdband.py add --band 1234-56789 \
  --date 2025-04-02 --site "Ridge Trail"
expect_error_contains "duplicate message" "is already banded on 2025-04-01"
expect_one_error_line "duplicate band"
expect_exit 1 "malformed date" "$PYTHON" birdband.py add --band 1234-56799 \
  --date not-a-date --site "Ridge Trail"
expect_error_contains "date message" "invalid date"
expect_one_error_line "malformed date"
expect_exit 1 "out-of-range wing chord" "$PYTHON" birdband.py add --band 1234-56799 \
  --date 2025-04-02 --site "Ridge Trail" --wing 5000
expect_error_contains "wing message" "invalid wing chord"
expect_one_error_line "out-of-range wing chord"
expect_exit 2 "missing required flags" "$PYTHON" birdband.py add

printf '\n-- recaptures -------------------------------------------\n'
expect_exit 0 "recap later" "$PYTHON" birdband.py recap 1234-56789 \
  --date 2025-04-19 --site "Ridge Trail" --wing 63 --weight 11.6
expect_exit 0 "recap earlier (inserted out of order)" "$PYTHON" birdband.py recap \
  1234-56789 --date 2025-03-15 --site "Mill Creek"
expect_exit 1 "recap of a band never banded" "$PYTHON" birdband.py recap 9999-00000 \
  --date 2025-04-02 --site "Ridge Trail"
expect_one_error_line "unknown band on recap"
expect_error_contains "unknown band points at add" "no record for that band"
expect_exit 1 "recap with a malformed date" "$PYTHON" birdband.py recap 1234-56789 \
  --date 04/02/2025 --site "Ridge Trail"
expect_one_error_line "malformed date on recap"

printf '\n-- history and list -------------------------------------\n'
expect_exit 0 "history 1234-56789" "$PYTHON" birdband.py history 1234-56789
band_line="$(printf '%s\n' "$LAST_OUT" | grep -n '^banded' | head -1 | cut -d: -f1)"
recap_first="$(printf '%s\n' "$LAST_OUT" | grep -n '^recap' | head -1 | cut -d: -f1)"
early_date="$(printf '%s\n' "$LAST_OUT" | grep -n '2025-03-15' | head -1 | cut -d: -f1)"
late_date="$(printf '%s\n' "$LAST_OUT" | grep -n '2025-04-19' | head -1 | cut -d: -f1)"
if [ -n "$band_line" ] && [ -n "$recap_first" ] && [ "$band_line" -lt "$recap_first" ]; then
  pass "history prints the banding event before the recaptures"
else
  fail "history did not print the banding event first"
fi
if [ -n "$early_date" ] && [ -n "$late_date" ] && [ "$early_date" -lt "$late_date" ]; then
  pass "history sorts recaptures ascending by date"
else
  fail "history did not sort recaptures ascending by date"
fi
expect_output_contains "history shows an unmeasured recapture" "not recorded"

expect_exit 0 "history without recaptures" "$PYTHON" birdband.py history 1234-56790
expect_output_contains "empty history says so" "no recaptures yet"
expect_exit 1 "history of an unknown band" "$PYTHON" birdband.py history 9999-00000
expect_one_error_line "unknown band on history"

expect_exit 0 "list" "$PYTHON" birdband.py list
expect_output_contains "list shows the first band" "1234-56789"
expect_output_contains "list shows the second band" "1234-56790"
expect_output_contains "list shows 'not recorded' for a missing measurement" "not recorded"
expect_exit 0 "list --band" "$PYTHON" birdband.py list --band 1234-56790
case "$LAST_OUT" in
  *1234-56789*) fail "list --band leaked another band" ;;
  *) pass "list --band narrows to one band" ;;
esac
expect_exit 0 "list with a band that was never used" "$PYTHON" birdband.py list --band 9999-00000
expect_output_contains "list filter empty state" "no records yet for band 9999-00000"

printf '\n-- the write gate ---------------------------------------\n'
expect_exit 0 "logout" "$PYTHON" birdband.py logout
expect_output_contains "logout names the bander" "signed out Dana"
expect_exit 1 "add after logout" "$PYTHON" birdband.py add --band 1234-56799 \
  --date 2025-05-01 --site "Ridge Trail"
expect_error_contains "gate message" "not signed in - run birdband login"
expect_one_error_line "add after logout"
expect_exit 0 "reads stay open" "$PYTHON" birdband.py list

printf '\n-- a corrupt store --------------------------------------\n'
printf '{"version": 1, "banding": [' >"$SMOKE_HOME/records.json"
expect_exit 1 "list with a truncated store" "$PYTHON" birdband.py list
expect_error_contains "corrupt store message" "cannot read data file"
expect_one_error_line "corrupt store"
expect_exit 1 "history with a truncated store" "$PYTHON" birdband.py history 1234-56789
expect_one_error_line "corrupt store on history"

printf '\n-- usage errors -----------------------------------------\n'
expect_exit 2 "unknown subcommand" "$PYTHON" birdband.py frobnicate
expect_exit 2 "unknown flag" "$PYTHON" birdband.py list --nonsense

printf '\n'
if [ "$FAILURES" -eq 0 ]; then
  printf 'smoke run passed: every step behaved as documented\n'
  exit 0
fi
printf 'smoke run failed: %s step(s) misbehaved\n' "$FAILURES" >&2
exit 1
