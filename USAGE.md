# Using BirdBand in the field

Every example below assumes you are in the project root. `birdband` in the
examples means `python3 birdband.py` (or just `birdband` after
`pip install -e .`).

## Before the session: create your account and sign in

Records carry the name of the bander who saved them, so the two write commands
need a bander to sign in as. The passphrase is entered twice, hidden, and stored
as a salted PBKDF2-HMAC-SHA256 hash - never as text.

```console
$ birdband register Dana
passphrase for Dana:
repeat passphrase:
account created for Dana

$ birdband login Dana          # you can leave the name off if you have one account
passphrase for Dana:
signed in as Dana - session expires 2025-04-01T21:10:07+00:00
```

The session lasts 12 hours. `birdband logout` deletes it; until then, `add` and
`recap` run without a prompt, and `list` and `history` never need a sign-in at
all.

Wrong passphrase, unknown bander - same answer, so the tool never tells an
outsider which half was wrong:

```console
$ birdband login Dana
passphrase for Dana:
username or passphrase not recognised
```

## Logging a bird at the moment of banding

Band, date and site are required; the rest is whatever you actually measured.
Wing chord and weight are independent - take one, both or neither.

```console
$ birdband add --band 2621-48357 --date 2025-04-01 --site "Ridge Trail" \
      --species "Black-capped Chickadee" --age AHY --sex F --wing 62 --weight 11.4
banded 2621-48357 on 2025-04-01 at Ridge Trail  Black-capped Chickadee - wing 62 mm, weight 11.4 g, bander Dana

$ birdband add --band 2621-48360 --date 2025-04-01 --site "Ridge Trail" \
      --species "Dark-eyed Junco" --age HY --wing 74
banded 2621-48360 on 2025-04-01 at Ridge Trail  Dark-eyed Junco - wing 74 mm, weight not recorded, bander Dana
```

The confirmation line echoes every field, so a typo is visible before the next
bird. A second `add` for a band you already logged is refused with the original
date rather than silently overwriting it:

```console
$ birdband add --band 2621-48357 --date 2025-04-02 --site "Ridge Trail"
band 2621-48357 is already banded on 2025-04-01
```

A bad value is refused before anything is written:

```console
$ birdband add --band 2621-48357 --date 04/02/2025 --site "Ridge Trail"
invalid date: expected YYYY-MM-DD (for example 2025-04-01)
$ birdband add --band 2621-48357 --date 2025-04-01 --site "Ridge Trail" --wing 5000
invalid wing chord: 5000 mm is outside 1-1000 mm
$ birdband add --band 2621-48357 --date 2025-04-01 --site "Ridge Trail" --weight abc
invalid weight: expected grams between 0.1 and 2000 (got 'abc')
$ birdband add --band 1234 --date 2025-04-01 --site "Ridge Trail"
invalid band: expected four digits, a hyphen, then five digits (for example 1234-56789)
```

## Logging a recapture

Same validation, same plain messages - but the band must already be banded.

```console
$ birdband recap 2621-48357 --date 2025-04-02 --site "Mill Creek" --weight 11.2
recaptured 2621-48357 on 2025-04-02 at Mill Creek - banded 2025-04-01, wing not recorded, weight 11.2 g, bander Dana

$ birdband recap 9999-00000 --date 2025-04-02 --site "Mill Creek"
no record for that band - band it first with: birdband add --band 9999-00000 --date YYYY-MM-DD --site <site>
```

Two recaptures on the same date are both kept, in the order you entered them.

## Asking one band's whole story

The question at the end of a session is usually about a single bird:
**when was it banded, and when has it been caught since?**

```console
$ birdband recap 2621-48357 --date 2025-04-19 --site "Ridge Trail" --wing 63 --weight 11.6
$ birdband recap 2621-48357 --date 2025-03-15 --site "Mill Creek"
$ birdband history 2621-48357
2621-48357  banded 2025-04-01 at Ridge Trail  Black-capped Chickadee  (AHY, F)  bander Dana
EVENT   DATE        SITE         WING          WEIGHT        BANDER
──────  ──────────  ───────────  ────────────  ────────────  ──────
banded  2025-04-01  Ridge Trail  62 mm         11.4 g        Dana
recap   2025-03-15  Mill Creek   not recorded  not recorded  Dana
recap   2025-04-02  Mill Creek   not recorded  11.2 g        Dana
recap   2025-04-19  Ridge Trail  63 mm         11.6 g        Dana
```

The banding event always prints first, then the recaptures oldest to newest -
even when you typed them in a different order, which is what makes the wing
chord and weight trend readable at a glance. A band with no recaptures still
prints its banding line, followed by `no recaptures yet`, so you can see the
bird has not been retrapped.

`history` on a band that was never banded exits 1 with
`no record for that band`; nothing is written.

## Reviewing the session

```console
$ birdband list
BAND         DATE        SITE         SPECIES                 BANDER  WING          WEIGHT
───────────  ──────────  ───────────  ──────────────────────  ──────  ────────────  ────────────
2621-48366   2025-04-05  Mill Creek   American Goldfinch      Dana    61 mm         13.1 g
2621-48357   2025-04-01  Ridge Trail  Black-capped Chickadee  Dana    62 mm         11.4 g
2621-48360   2025-04-01  Ridge Trail  Dark-eyed Junco         Dana    74 mm         not recorded
```

Newest date first (records sharing a date keep the order you logged them in), one line per banding record, `not recorded` wherever a
measurement was not taken. `--band` narrows it to one bird:

```console
$ birdband list --band 2621-48357
```

With nothing banded yet, `list` invites the first add and exits 0:

```console
$ birdband list
no records yet - add one with: birdband add --band 1234-56789 --date YYYY-MM-DD --site <site>
```

## When something is wrong with the data file

If `records.json` is truncated, hand-edited into invalid JSON, or unreadable,
every command says so plainly instead of printing a traceback:

```console
$ birdband list
cannot read data file /home/dana/.birdband/records.json
```

Exit code 1, one line, nothing written.

## Keeping two seasons apart

Point `BIRDBAND_HOME` at a directory and BirdBand uses it for everything:

```console
$ BIRDBAND_HOME=~/banding/2025 python3 birdband.py list
```

This is how the test suite and `demo.sh` keep their hands off your real records.
