# Installing and running BirdBand

BirdBand is one standard-library Python file. There is nothing to compile, no
service to start, and no package manager to pick: **pip only, and only for the
test runner.** The tool itself needs no installation.

## 1. Prerequisites

- CPython **3.9 or newer** (`python3 --version`). Tested on 3.9, 3.10 and 3.11.
- That is all. No compiler, no system library, no network access.

## 2. Install the project dependencies

```bash
bash install.sh
```

`install.sh` upgrades pip/setuptools/wheel, installs `pytest` from
`requirements.txt` (skipping the install when pytest is already importable),
optionally adds a `birdband` console command with `pip install -e . --no-deps`,
verifies that the module imports, and **exits**. It never starts anything in the
foreground and never uses `sudo`, `apt-get` or any other system package tool.

If you would rather do it by hand:

```bash
python3 -m pip install -r requirements.txt
```

Running the tool requires no dependencies at all:

```bash
python3 birdband.py --help
```

## 3. Seed the demo data

```bash
bash demo.sh
```

This is the seed step. It creates the demo bander (see below) plus a plausible
banding session in a fresh scratch store, exercises every command, and then
removes the scratch store. It is safe to re-run: each run gets a fresh
`mktemp -d` store. `BIRDBAND_DEMO_KEEP=1 bash demo.sh` keeps it,
`BIRDBAND_HOME=/tmp/my-session bash demo.sh` seeds into a directory you choose.

## 4. Run it in dev

```bash
python3 birdband.py --help                      # every command and flag
python3 birdband.py register Dana               # passphrase entered twice, hidden
python3 birdband.py login Dana
python3 birdband.py add --band 2621-48357 --date 2025-04-01 --site "Ridge Trail" \
    --species "Black-capped Chickadee" --wing 62 --weight 11.4
python3 birdband.py recap 2621-48357 --date 2025-04-02 --site "Mill Creek" --weight 11.2
python3 birdband.py history 2621-48357
python3 birdband.py list
```

Or, after `pip install -e .`, the same thing as `birdband list`. The data store
defaults to `~/.birdband/records.json`; export `BIRDBAND_HOME` to point it
somewhere else (this is how the tests and the demo keep their hands off your
real records).

Non-interactive use (scripts, CI): export `BIRDBAND_PASSPHRASE` and stdin is
never read. Everything else is arguments only.

## 5. Test

```bash
python3 -m pytest      # unit + end-to-end CLI tests, including the 10,000-record budget
bash tests/smoke.sh    # scripted happy path and error paths with asserted exit codes
```

## 6. Uninstall

Delete the file and the store:

```bash
rm -rf ~/.birdband
```

## Demo accounts

`demo.sh` seeds exactly one bander, matching the README table:

| Username | Password | Role                                                               |
| -------- | -------- | ------------------------------------------------------------------ |
| `cenius` | `cenius` | Primary demo bander - owns the seeded sample records               |

The passphrase goes through the same PBKDF2-HMAC-SHA256 path as a hand-registered
account, so it is never stored in plaintext.

## Environment variables

| Variable               | Default            | Purpose                                                        |
| ---------------------- | ------------------ | -------------------------------------------------------------- |
| `BIRDBAND_HOME`        | `~/.birdband`      | Where `records.json`, `credentials.json` and `session.json` live |
| `BIRDBAND_PASSPHRASE`  | *(unset)*          | Passphrase for non-interactive `register` / `login`            |
| `NO_COLOR`             | *(unset)*          | Set to switch ANSI colour off                                  |
| `BIRDBAND_DEBUG`       | *(unset)*          | Set to `1` to let unexpected failures raise                    |

See `.env.example`. BirdBand reads the environment directly and never loads a
`.env` file, so a fresh checkout runs with no configuration at all.
