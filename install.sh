#!/usr/bin/env bash
#
# BirdBand setup: installs the project-level dependency (the pytest runner),
# self-checks that the tool imports, and EXITS.  It never starts anything in the
# foreground and never installs a system package.
#
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: '$PYTHON' not found on PATH. Install Python 3.9+ first (see INSTALL.md)." >&2
  exit 1
fi

echo "==> Using $("$PYTHON" --version 2>&1)"

if "$PYTHON" -c "import pytest" >/dev/null 2>&1; then
  echo "==> pytest already available; skipping dependency install"
else
  echo "==> Installing project dependencies (pytest) from requirements.txt"
  "$PYTHON" -m pip install --upgrade pip setuptools wheel
  "$PYTHON" -m pip install -r requirements.txt
fi

# Optional convenience: a `birdband` console command on PATH.  The tool runs
# perfectly well as `python3 birdband.py`, so a failure here is not fatal.
if "$PYTHON" -m pip install -e . --no-build-isolation --no-deps >/dev/null 2>&1; then
  echo "==> Installed the 'birdband' console command (python3 birdband.py also works)"
else
  echo "==> Skipped the optional 'birdband' console command; use python3 birdband.py"
fi

echo "==> Self-check: import the module"
"$PYTHON" -c "import birdband; print('   birdband', birdband.__version__, 'imports cleanly')"
"$PYTHON" birdband.py --version

cat <<'EOF'

Setup complete - nothing is running in the foreground.

Next steps:
  1. ./demo.sh                      record a full banding session in a scratch store
  2. python3 birdband.py register Dana
     python3 birdband.py login Dana
  3. python3 -m pytest              run the test suite
EOF
