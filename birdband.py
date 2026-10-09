#!/usr/bin/env python3
"""BirdBand - an offline record keeper for bird banders.

One banding event per band, recaptures appended forever, and one band's whole
capture story replayed in date order.  Everything lives in a single validated
JSON store (``$BIRDBAND_HOME/records.json``, default ``~/.birdband/records.json``)
written atomically with the standard library only.

Run it as::

    python3 birdband.py --help
    python3 birdband.py add --band 1234-56789 --date 2025-04-01 --site "Ridge Trail"
    python3 -m birdband list

Design direction (calm-precise, terminal dark, mono, compact - see README):
accent ``oklch(0.58 0.12 192)`` / ``#00908d``, a neutral grey ramp, aligned
columns and box-drawing rules.  Colour is decoration only: every status is also
carried by plain words, so nothing is signalled by colour alone.
"""

from __future__ import annotations

import argparse
import gc
import getpass
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from operator import itemgetter
from pathlib import Path
from typing import Any, TypedDict

__version__ = "1.0.0"

PROGRAM = "birdband"
STORE_VERSION = 1
BAND_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{5}$")
DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,32}$")

WING_MIN, WING_MAX = 1, 1000
WEIGHT_MIN, WEIGHT_MAX = 0.1, 2000.0

PBKDF2_ITERATIONS = 200_000
SESSION_HOURS = 12
FILE_MODE = 0o600
DIR_MODE = 0o700

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

NOT_RECORDED = "not recorded"
ADD_EXAMPLE = "birdband add --band 1234-56789 --date YYYY-MM-DD --site <site>"

# --------------------------------------------------------------------------
# Design tokens.  The committed palette, in one place: calm-precise, terminal
# dark, monospace, compact.  ANSI only when stdout is a real terminal.
# --------------------------------------------------------------------------
ACCENT_OKLCH = "oklch(0.58 0.12 192)"
ACCENT_HEX = "#00908d"  # rgb(0, 144, 141) - non-CSS consumers use this
NEUTRAL_RAMP = (
    "#0b0f10",  # surface
    "#141a1b",  # raised surface
    "#5c6b6c",  # muted text
    "#c8d2d2",  # body text
    "#f2f7f7",  # strong text
)
_RULE_UNICODE = "\u2500"  # box drawing light horizontal


class BirdBandError(Exception):
    """A user-facing failure: one plain line on stderr, exit code 1."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class StoreError(BirdBandError):
    """The local data file exists but cannot be read as a valid store."""


class NotSignedIn(BirdBandError):
    """A write command was attempted without a valid local session."""


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------
class Palette:
    """ANSI decoration for a terminal; a no-op when piped or NO_COLOR is set."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.enabled else text

    def accent(self, text: str) -> str:
        return self._wrap("38;2;0;144;141", text)

    def strong(self, text: str) -> str:
        return self._wrap("1", text)

    def muted(self, text: str) -> str:
        """The #5c6b6c step of the neutral ramp, for rules and separators."""
        return self._wrap("38;2;92;107;108", text)


def make_palette(stream: Any) -> Palette:
    """Colour only when the stream is an interactive terminal."""
    if os.environ.get("NO_COLOR") or os.environ.get("TERM", "") == "dumb":
        return Palette(False)
    return Palette(bool(getattr(stream, "isatty", lambda: False)()))


def rule_char(stream: Any) -> str:
    """Box drawing needs a UTF-8 capable stream; fall back to ASCII."""
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        _RULE_UNICODE.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return "-"
    return _RULE_UNICODE


def write_lines(lines: Iterable[str], stream: Any = None) -> None:
    """One write per command instead of one print per row."""
    target = stream if stream is not None else sys.stdout
    target.write("\n".join(lines))
    target.write("\n")


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    palette: Palette,
    char: str,
    min_widths: Sequence[int] | None = None,
) -> list[str]:
    """Aligned columns with a box-drawing rule under the header.

    Widths come from the header and every cell, so nothing is ever truncated
    (``not recorded`` keeps its full width).  The widths are measured with the
    C-level ``zip``/``max``/``map`` path so the 10,000-record budget holds.
    """
    widths = [len(header) for header in headers]
    if min_widths:
        widths = [max(width, floor) for width, floor in zip(widths, min_widths)]
    if rows:
        for index, column in enumerate(zip(*rows)):
            widths[index] = max(widths[index], max(map(len, column)))

    # The last column is left unpadded so no row needs a trailing rstrip: at
    # 10,000 records that difference is measurable.
    line_format = "".join(f"{{:<{width}}}  " for width in widths[:-1]) + "{}"
    output = [
        palette.strong(line_format.format(*headers)),
        palette.muted("  ".join(char * width for width in widths).rstrip()),
    ]
    output.extend(line_format.format(*row) for row in rows)
    return output


def format_measure(value: Any, unit: str) -> str:
    """A measurement, or the words 'not recorded' - never a blank cell.

    Anything that is not a real number reads as 'not recorded' rather than
    crashing a listing, so a hand-edited store degrades instead of exploding.
    """
    if type(value) is int:
        return f"{value} {unit}"
    if type(value) is float:
        return f"{value:g} {unit}"
    return NOT_RECORDED


def format_text(value: Any) -> str:
    """Free-text field, or the words 'not recorded' when it was never read."""
    return value if type(value) is str and value else NOT_RECORDED


# --------------------------------------------------------------------------
# Domain model.  The store is JSON, so the record shapes are TypedDicts: no
# per-record object construction cost when a command reads 10,000 of them.
# --------------------------------------------------------------------------
class BandingRecord(TypedDict):
    band: str
    date: str
    site: str
    species: str | None
    age_code: str | None
    sex: str | None
    wing_chord_mm: int | None
    weight_g: float | None
    bander: str
    created_at: str


class Recapture(TypedDict):
    id: str
    band: str
    date: str
    site: str
    wing_chord_mm: int | None
    weight_g: float | None
    bander: str
    created_at: str


class User(TypedDict):
    username: str
    salt: str
    password_hash: str
    iterations: int
    created_at: str


@dataclass
class Store:
    """The whole local store, parsed once per command."""

    banding: list[BandingRecord]
    recaptures: list[Recapture]

    def banding_index(self) -> dict[str, BandingRecord]:
        return {record["band"]: record for record in self.banding}

    def recaptures_for(self, band: str) -> list[Recapture]:
        return [record for record in self.recaptures if record["band"] == band]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": STORE_VERSION,
            "banding": self.banding,
            "recaptures": self.recaptures,
        }


# --------------------------------------------------------------------------
# Paths and the atomic JSON store
# --------------------------------------------------------------------------
def data_dir() -> Path:
    """``$BIRDBAND_HOME`` or ``~/.birdband`` - never anything outside it."""
    override = os.environ.get("BIRDBAND_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".birdband"


def records_path() -> Path:
    return data_dir() / "records.json"


def credentials_path() -> Path:
    return data_dir() / "credentials.json"


def session_path() -> Path:
    return data_dir() / "session.json"


def read_json(path: Path) -> Any:
    """Read one JSON document, or raise StoreError for anything less.

    The standard library's JSON parser is the only deserializer in this file:
    nothing here rebuilds live objects from untrusted bytes, executes what it
    reads, or imports code named by the file.  A store that is not clean JSON -
    truncated, hand-edited, binary, or a directory - becomes one plain message.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise StoreError(f"cannot read data file {path}") from exc
    try:
        return json.loads(text)
    except ValueError as exc:
        raise StoreError(f"cannot read data file {path}") from exc


def write_json_atomic(path: Path, payload: Any, *, mode: int = FILE_MODE) -> None:
    """Write via a temp file in the same directory, then os.replace.

    An interrupted command therefore leaves either the old file or the new one,
    never a half-written store.
    """
    directory = path.parent
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        handle_fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(directory)
        )
    except OSError as exc:
        raise BirdBandError(f"cannot write data file {path}") from exc
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except OSError as exc:
        _unlink_quietly(temp_name)
        raise BirdBandError(f"cannot write data file {path}") from exc
    except BaseException:
        _unlink_quietly(temp_name)
        raise
    _fsync_directory(directory)


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def load_store() -> Store:
    """Parse the store once; a present-but-unreadable file is a hard error.

    Only the identity fields are checked here, flat and inline, because this
    loop runs over every stored record on every command and the 10,000-record
    budget depends on it.  A damaged measurement is not fatal: the formatters
    render it as 'not recorded'.
    """
    path = records_path()
    raw = read_json(path)
    if raw is None:
        return Store(banding=[], recaptures=[])
    unreadable = f"cannot read data file {path}"
    if type(raw) is not dict or raw.get("version") != STORE_VERSION:
        raise StoreError(unreadable)
    banding = raw.get("banding")
    recaptures = raw.get("recaptures")
    if type(banding) is not list or type(recaptures) is not list:
        raise StoreError(unreadable)

    for entry in banding:
        if type(entry) is not dict:
            raise StoreError(unreadable)
        try:
            band = entry["band"]
            day = entry["date"]
            site = entry["site"]
            bander = entry["bander"]
        except KeyError:
            raise StoreError(unreadable) from None
        if type(band) is not str or type(day) is not str or type(site) is not str or type(bander) is not str:
            raise StoreError(unreadable)

    for entry in recaptures:
        if type(entry) is not dict:
            raise StoreError(unreadable)
        try:
            identifier = entry["id"]
            band = entry["band"]
            day = entry["date"]
            site = entry["site"]
            bander = entry["bander"]
        except KeyError:
            raise StoreError(unreadable) from None
        if (
            type(identifier) is not str
            or type(band) is not str
            or type(day) is not str
            or type(site) is not str
            or type(bander) is not str
        ):
            raise StoreError(unreadable)

    return Store(banding=banding, recaptures=recaptures)


def save_store(store: Store) -> None:
    write_json_atomic(records_path(), store.to_dict())


def load_users() -> list[User]:
    path = credentials_path()
    raw = read_json(path)
    if raw is None:
        return []
    if not isinstance(raw, dict) or not isinstance(raw.get("users"), list):
        raise StoreError(f"cannot read data file {path}")
    for entry in raw["users"]:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(key), str) for key in ("username", "salt", "password_hash")
        ):
            raise StoreError(f"cannot read data file {path}")
    return list(raw["users"])


def save_users(users: Sequence[User]) -> None:
    write_json_atomic(credentials_path(), {"version": STORE_VERSION, "users": list(users)})


def now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# Validation (all input is checked before the store is opened)
# --------------------------------------------------------------------------
def parse_band(value: str) -> str:
    band = value.strip()
    if not BAND_PATTERN.match(band):
        raise BirdBandError(
            "invalid band: expected four digits, a hyphen, then five digits "
            "(for example 1234-56789)"
        )
    return band


def parse_iso_date(value: str, *, label: str = "date") -> str:
    text = value.strip()
    if not DATE_PATTERN.match(text):
        raise BirdBandError(f"invalid {label}: expected YYYY-MM-DD (for example 2025-04-01)")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise BirdBandError(
            f"invalid {label}: {text} is not a real calendar date (expected YYYY-MM-DD)"
        ) from exc
    return parsed.isoformat()


def parse_site(value: str) -> str:
    site = value.strip()
    if not site:
        raise BirdBandError("invalid site: site must not be empty")
    return site


def parse_wing(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not re.fullmatch(r"[0-9]+", text):
        raise BirdBandError(
            f"invalid wing chord: expected a whole number of millimetres between "
            f"{WING_MIN} and {WING_MAX} (got {value!r})"
        )
    number = int(text)
    if not WING_MIN <= number <= WING_MAX:
        raise BirdBandError(
            f"invalid wing chord: {number} mm is outside {WING_MIN}-{WING_MAX} mm"
        )
    return number


def parse_weight(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    try:
        number = float(text)
    except ValueError as exc:
        raise BirdBandError(
            f"invalid weight: expected grams between {WEIGHT_MIN:g} and {WEIGHT_MAX:g} "
            f"(got {value!r})"
        ) from exc
    # A NaN or an infinity simply fails the range test below, which is why
    # there is no separate "not a number" branch here.
    if not WEIGHT_MIN <= number <= WEIGHT_MAX:
        raise BirdBandError(
            f"invalid weight: {number:g} g is outside {WEIGHT_MIN:g}-{WEIGHT_MAX:g} g"
        )
    return number


def parse_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def parse_username(value: str) -> str:
    username = value.strip()
    if not USERNAME_PATTERN.match(username):
        raise BirdBandError(
            "invalid username: use 1-32 letters, digits, dot, dash or underscore"
        )
    return username


# --------------------------------------------------------------------------
# Local sign-in
# --------------------------------------------------------------------------
def hash_passphrase(passphrase: str, salt_hex: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), bytes.fromhex(salt_hex), iterations
    ).hex()


def read_passphrase(prompt: str) -> str:
    """One passphrase, from ``BIRDBAND_PASSPHRASE`` or an interactive prompt.

    The environment variable keeps scripted runs (and the recorded demo)
    non-interactive; a TTY without it still gets the hidden getpass prompt.
    """
    preset = os.environ.get("BIRDBAND_PASSPHRASE")
    if preset is not None:
        return preset
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        if line == "":
            raise BirdBandError("no passphrase given - nothing was saved")
        return line.rstrip("\n")
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt) as exc:
        raise BirdBandError("no passphrase given - nothing was saved") from exc


def find_user(users: Sequence[User], username: str) -> User | None:
    for user in users:
        if user["username"] == username:
            return user
    return None


def authenticate(users: Sequence[User], username: str | None, passphrase: str) -> User | None:
    """Constant-time verification; never says which half was wrong."""
    candidates = [user for user in users if username is None or user["username"] == username]
    for user in candidates:
        candidate = hash_passphrase(passphrase, user["salt"], int(user.get("iterations") or PBKDF2_ITERATIONS))
        if hmac.compare_digest(candidate, user["password_hash"]):
            return user
    return None


def read_session() -> dict[str, Any] | None:
    """The stored session, or None when absent, unreadable or expired."""
    try:
        raw = read_json(session_path())
    except StoreError:
        return None
    if not isinstance(raw, dict):
        return None
    username = raw.get("username")
    expires_at = raw.get("expires_at")
    if not isinstance(username, str) or not isinstance(expires_at, str):
        return None
    try:
        expires = datetime.fromisoformat(expires_at)
    except ValueError:
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        _unlink_quietly(str(session_path()))
        return None
    return raw


def require_session() -> str:
    """The signed-in username, or a plain refusal (write commands only)."""
    session = read_session()
    if session is None:
        raise NotSignedIn("not signed in - run birdband login")
    return str(session["username"])


def clear_session() -> None:
    _unlink_quietly(str(session_path()))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_add(args: argparse.Namespace) -> int:
    band = parse_band(args.band)
    when = parse_iso_date(args.date)
    site = parse_site(args.site)
    wing = parse_wing(args.wing)
    weight = parse_weight(args.weight)
    species = parse_optional_text(args.species)
    age_code = parse_optional_text(args.age)
    sex = parse_optional_text(args.sex)

    bander = require_session()
    store = load_store()
    existing = store.banding_index().get(band)
    if existing is not None:
        raise BirdBandError(f"band {band} is already banded on {existing['date']}")

    store.banding.append(
        {
            "band": band,
            "date": when,
            "site": site,
            "species": species,
            "age_code": age_code,
            "sex": sex,
            "wing_chord_mm": wing,
            "weight_g": weight,
            "bander": bander,
            "created_at": now_stamp(),
        }
    )
    save_store(store)

    palette = make_palette(sys.stdout)
    species_text = f"  {species}" if species else ""
    print(
        f"banded {palette.accent(band)} on {when} at {site}{species_text} - "
        f"wing {format_measure(wing, 'mm')}, weight {format_measure(weight, 'g')}, "
        f"bander {bander}"
    )
    return EXIT_OK


def cmd_recap(args: argparse.Namespace) -> int:
    band = parse_band(args.band)
    when = parse_iso_date(args.date)
    site = parse_site(args.site)
    wing = parse_wing(args.wing)
    weight = parse_weight(args.weight)

    bander = require_session()
    store = load_store()
    original = store.banding_index().get(band)
    if original is None:
        raise BirdBandError(unknown_band_message(band))

    sequence = len(store.recaptures_for(band)) + 1
    store.recaptures.append(
        {
            "id": f"{band}#{sequence}",
            "band": band,
            "date": when,
            "site": site,
            "wing_chord_mm": wing,
            "weight_g": weight,
            "bander": bander,
            "created_at": now_stamp(),
        }
    )
    save_store(store)

    palette = make_palette(sys.stdout)
    print(
        f"recaptured {palette.accent(band)} on {when} at {site} - banded {original['date']}, "
        f"wing {format_measure(wing, 'mm')}, weight {format_measure(weight, 'g')}, "
        f"bander {bander}"
    )
    return EXIT_OK


def list_row(record: BandingRecord) -> tuple[str, ...]:
    """One aligned row for `birdband list` - flat on purpose: this runs 10,000
    times per command and the sub-100 ms budget depends on it."""
    wing = record["wing_chord_mm"]
    weight = record["weight_g"]
    species = record["species"]
    return (
        record["band"],
        record["date"],
        record["site"],
        species if type(species) is str and species else NOT_RECORDED,
        record["bander"],
        f"{wing} mm" if type(wing) is int else NOT_RECORDED,
        f"{weight:g} g" if type(weight) is int or type(weight) is float else NOT_RECORDED,
    )


def unknown_band_message(band: str) -> str:
    return (
        f"no record for that band - band it first with: birdband add --band {band} "
        "--date YYYY-MM-DD --site <site>"
    )


def cmd_history(args: argparse.Namespace) -> int:
    band = parse_band(args.band)
    store = load_store()
    original = store.banding_index().get(band)
    if original is None:
        raise BirdBandError(unknown_band_message(band))

    palette = make_palette(sys.stdout)
    header = f"{palette.accent(band)}  banded {original['date']} at {original['site']}"
    if format_text(original["species"]) != NOT_RECORDED:
        header += f"  {original['species']}"
    codes = ", ".join(
        code
        for code in (original["age_code"], original["sex"])
        if type(code) is str and code
    )
    if codes:
        header += f"  ({codes})"
    header += f"  bander {original['bander']}"
    print(header)

    ordered = sorted(store.recaptures_for(band), key=itemgetter("date"))
    rows = [
        (
            "banded",
            original["date"],
            original["site"],
            format_measure(original["wing_chord_mm"], "mm"),
            format_measure(original["weight_g"], "g"),
            original["bander"],
        )
    ]
    rows.extend(
        (
            "recap",
            item["date"],
            item["site"],
            format_measure(item["wing_chord_mm"], "mm"),
            format_measure(item["weight_g"], "g"),
            item["bander"],
        )
        for item in ordered
    )
    lines = render_table(
        ("EVENT", "DATE", "SITE", "WING", "WEIGHT", "BANDER"),
        rows,
        palette=palette,
        char=rule_char(sys.stdout),
        min_widths=(6, 10, 4, 12, 12, 6),
    )
    if not ordered:
        lines.append("no recaptures yet")
    write_lines(lines)
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    band = parse_band(args.band) if args.band else None
    store = load_store()
    records = store.banding
    if band is not None:
        records = [record for record in records if record["band"] == band]
    if not records:
        if band is None:
            print(f"no records yet - add one with: {ADD_EXAMPLE}")
        else:
            print(
                f"no records yet for band {band} - add one with: birdband add --band "
                f"{band} --date YYYY-MM-DD --site <site>"
            )
        return EXIT_OK

    ordered = sorted(records, key=itemgetter("date"), reverse=True)
    rows = [list_row(record) for record in ordered]
    del ordered
    write_lines(
        render_table(
            ("BAND", "DATE", "SITE", "SPECIES", "BANDER", "WING", "WEIGHT"),
            rows,
            palette=make_palette(sys.stdout),
            char=rule_char(sys.stdout),
            min_widths=(11, 10, 4, 12, 6, 12, 12),
        )
    )
    return EXIT_OK


def cmd_register(args: argparse.Namespace) -> int:
    username = parse_username(args.username)
    users = load_users()
    if find_user(users, username) is not None:
        raise BirdBandError(f"account already exists: {username}")

    first = read_passphrase(f"passphrase for {username}: ")
    if not first:
        raise BirdBandError("passphrase must not be empty - nothing was saved")
    second = read_passphrase("repeat passphrase: ")
    if first != second:
        raise BirdBandError("passphrases did not match - nothing was saved")

    salt = os.urandom(16).hex()
    users.append(
        {
            "username": username,
            "salt": salt,
            "password_hash": hash_passphrase(first, salt, PBKDF2_ITERATIONS),
            "iterations": PBKDF2_ITERATIONS,
            "created_at": now_stamp(),
        }
    )
    save_users(users)
    print(f"account created for {username}")
    return EXIT_OK


def cmd_login(args: argparse.Namespace) -> int:
    users = load_users()
    if not users:
        raise BirdBandError(
            "no bander account yet - create one with: birdband register <username> "
            "(demo account seeded by demo.sh: cenius / cenius)"
        )
    username = parse_username(args.username) if args.username else None
    prompt = f"passphrase for {username}: " if username else "passphrase: "
    user = authenticate(users, username, read_passphrase(prompt))
    if user is None:
        raise BirdBandError("username or passphrase not recognised")

    expires = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).replace(microsecond=0)
    write_json_atomic(
        session_path(),
        {
            "username": user["username"],
            "created_at": now_stamp(),
            "expires_at": expires.isoformat(),
        },
    )
    print(
        f"signed in as {make_palette(sys.stdout).accent(user['username'])} - "
        f"session expires {expires.isoformat()}"
    )
    return EXIT_OK


def cmd_logout(args: argparse.Namespace) -> int:
    session = read_session()
    if session is None:
        raise NotSignedIn("not signed in - run birdband login")
    username = str(session["username"])
    clear_session()
    print(f"signed out {username}")
    return EXIT_OK


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
EPILOG = """\
commands and flags:
  add       --band --date --site [--species] [--age] [--sex] [--wing] [--weight]  (needs a session)
  recap     <band> --date --site [--wing] [--weight]                              (needs a session)
  history   <band>
  list      [--band]
  register  <username>
  login     [<username>]
  logout

examples:
  birdband add --band 1234-56789 --date 2025-04-01 --site "Ridge Trail" \\
      --species "Black-capped Chickadee" --age AHY --sex F --wing 62 --weight 11.4
  birdband recap 1234-56789 --date 2025-04-02 --site "Mill Creek" --weight 11.2
  birdband history 1234-56789
  birdband list
  birdband list --band 1234-56789
  birdband register Dana
  birdband login
  birdband logout

exit codes: 0 success, 1 user or data error, 2 usage error
data file:  $BIRDBAND_HOME/records.json  (default ~/.birdband/records.json)
demo:       ./demo.sh seeds the demo account cenius / cenius into a scratch home
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "BirdBand - an offline banding record keeper: one banding event per band, "
            "recaptures appended forever, and one band's whole capture story in date order."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    add_parser = subparsers.add_parser(
        "add",
        help="log a newly banded bird (needs a session)",
        description="Log a banding event for a band that has not been banded yet.",
        epilog=(
            "example:\n  birdband add --band 1234-56789 --date 2025-04-01 "
            '--site "Ridge Trail" --species "Black-capped Chickadee" --wing 62 --weight 11.4\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_parser.add_argument("--band", required=True, metavar="NNNN-NNNNN", help="band number")
    add_parser.add_argument("--date", required=True, metavar="YYYY-MM-DD", help="banding date")
    add_parser.add_argument("--site", required=True, metavar="SITE", help="banding site")
    add_parser.add_argument("--species", metavar="NAME", help="species name")
    add_parser.add_argument("--age", metavar="CODE", help="age code, for example AHY")
    add_parser.add_argument("--sex", metavar="CODE", help="sex code, for example F")
    add_parser.add_argument("--wing", metavar="MM", help=f"wing chord, {WING_MIN}-{WING_MAX} mm")
    add_parser.add_argument("--weight", metavar="G", help=f"weight, {WEIGHT_MIN:g}-{WEIGHT_MAX:g} g")
    add_parser.set_defaults(handler=cmd_add)

    recap_parser = subparsers.add_parser(
        "recap",
        help="append a recapture of an already-banded bird (needs a session)",
        description="Append a recapture event to a band that was banded earlier.",
        epilog=(
            "example:\n  birdband recap 1234-56789 --date 2025-04-02 "
            '--site "Mill Creek" --weight 11.2\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    recap_parser.add_argument("band", metavar="NNNN-NNNNN", help="band already on the bird")
    recap_parser.add_argument("--date", required=True, metavar="YYYY-MM-DD", help="recapture date")
    recap_parser.add_argument("--site", required=True, metavar="SITE", help="recapture site")
    recap_parser.add_argument("--wing", metavar="MM", help=f"wing chord, {WING_MIN}-{WING_MAX} mm")
    recap_parser.add_argument("--weight", metavar="G", help=f"weight, {WEIGHT_MIN:g}-{WEIGHT_MAX:g} g")
    recap_parser.set_defaults(handler=cmd_recap)

    history_parser = subparsers.add_parser(
        "history",
        help="print one band's banding event then its recaptures in date order",
        description="Print the banding event first, then every recapture oldest first.",
        epilog="example:\n  birdband history 1234-56789\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    history_parser.add_argument("band", metavar="NNNN-NNNNN", help="band to look up")
    history_parser.set_defaults(handler=cmd_history)

    list_parser = subparsers.add_parser(
        "list",
        help="print every banding record, newest date first",
        description="Print one line per banding record, newest date first.",
        epilog="examples:\n  birdband list\n  birdband list --band 1234-56789\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    list_parser.add_argument("--band", metavar="NNNN-NNNNN", help="only this band")
    list_parser.set_defaults(handler=cmd_list)

    register_parser = subparsers.add_parser(
        "register",
        help="create the local bander account",
        description="Create the local bander account as a salted PBKDF2 hash.",
        epilog="example:\n  birdband register Dana\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    register_parser.add_argument("username", metavar="USERNAME", help="bander name to create")
    register_parser.set_defaults(handler=cmd_register)

    login_parser = subparsers.add_parser(
        "login",
        help="verify the passphrase and start a session",
        description="Verify the passphrase against the stored hash and write a session file.",
        epilog="examples:\n  birdband login\n  birdband login Dana\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    login_parser.add_argument(
        "username", nargs="?", metavar="USERNAME", help="bander name (optional with one account)"
    )
    login_parser.set_defaults(handler=cmd_login)

    logout_parser = subparsers.add_parser(
        "logout",
        help="delete the local session file",
        description="Sign out by deleting the local session file.",
    )
    logout_parser.set_defaults(handler=cmd_logout)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    handler = getattr(args, "handler", None)
    if handler is None:
        # No command at all is not an error: print the usage block, exit 0.
        parser.print_help(sys.stdout)
        return EXIT_OK
    # The store is acyclic data, so the cyclic collector has nothing to find
    # and only costs wall time on a 10,000-record listing.
    was_collecting = gc.isenabled()
    gc.disable()
    try:
        return handler(args)
    except BirdBandError as exc:
        print(exc.message, file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # process boundary: one line, never a traceback
        if os.environ.get("BIRDBAND_DEBUG"):
            raise
        print(f"{PROGRAM}: unexpected failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        if was_collecting:
            gc.enable()


if __name__ == "__main__":
    sys.exit(main())
