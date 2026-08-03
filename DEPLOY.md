# Deploying to Cloud Run

A small private table for a few friends, on the free tier.

## Why the service was returning 503

Two things in the old `Dockerfile`, both fatal, both fixed:

1. **It listened on port 8000.** Cloud Run injects `$PORT` (8080) and health-checks
   it. Nothing was listening there, so the revision never went healthy — which
   surfaces as a bare `503` with nothing useful in the logs.
2. **It never copied `tables.py` or `arcade.py`.** Even on the right port the
   container would have died on import.

## Read this before you deploy

**Cloud Run's disk is ephemeral.** The container filesystem is wiped on every
cold start, and the free tier scales to zero — so it *will* cold start. With
SQLite that means:

- **Balances reset to 10,000.** Fine for a test; not fine for anything you care about.
- **Card IDs would be regenerated, and nobody could log in.** This is the one
  that actually breaks the deployment, and the fix is to pin them (below).

Also set **`--max-instances=1`**. Two instances means two separate SQLite files
and two different casinos.

For "my friends are trying it out", ephemeral is fine. When it isn't, see
**Giving it a real database** below.

## Deploy

```bash
gcloud run deploy davidsino \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --max-instances=1 \
  --set-env-vars "DATABASE_URL=sqlite:////tmp/davidsino.db" \
  --set-env-vars "ADMIN_PIN=<pick something>,WORKER_PIN=<pick something>" \
  --set-env-vars "MAX_PLAYERS=3" \
  --set-env-vars "SEED_CARD_DAVID=BB9F6154A457C00E" \
  --set-env-vars "SEED_CARD_ALEX=4AC2ED5379495231" \
  --set-env-vars "SEED_CARD_GUEST=D1F04EA18812225A"
```

The container seeds the roster on boot — three players, 10,000 points each — and
seeding is idempotent, so a restart never duplicates anyone or resets a card.

To see the cards later:

```bash
python3 seed_players.py --show
```

## The card IDs are passwords

There is no second factor. On a public URL, a card ID of `david` is a password
of `david`, so the seeder uses random 16-hex-character IDs shaped like real RFID
UIDs. **Hand them out privately — a screenshot of one is a login.**

When you want the physical cards to work, set `SEED_CARD_*` to the real UIDs
your reader emits instead, on a fresh database.

## Change the PINs

`ADMIN_PIN` defaults to `1234`. On a public URL that is not a PIN, it is a
formality — the dealer panel moves points and confirms deposits. Set both.

## Turn the deposit flow off

Leave every `*_ADDRESS` and `*_HANDLE` variable unset. The Funds tab then shows
"no deposit methods", which is what you want on a public test site — the flow is
designed around a dealer confirming real transfers by hand, and it has no place
on something you are handing to friends to poke at.

**Do not put real wallet addresses on this.** The app never takes custody, but a
public page advertising your addresses is not something to do casually.

## What is exposed

Public without any credential:

| Endpoint | Why it is safe to expose |
|---|---|
| `GET /api/health` | liveness only |
| `POST /api/scan` | the front door — rate limited, 10 bad cards per address earns a 5 minute lockout |
| `GET /api/leaderboard` | names and standings, no card IDs |
| `GET /api/slots/machines`, `/api/tables/games` | paytables and limits |
| `POST /api/slots/verify` | the public fairness verifier; stateless |

Everything that moves points or reads a private ledger needs a session token, and
everything on the dealer side needs the PIN. See the "Who can get in" section of
the README.

One caveat specific to Cloud Run: the scan rate limiter counts failures **per
instance, in memory**. With `--max-instances=1` that is the whole service. If you
ever scale out, it becomes per-instance and correspondingly weaker.

## Check it came up

```bash
curl -s https://casino.davidgawel.com/api/health
gcloud run services logs read davidsino --region us-central1 --limit 50
```

A `503` after a deploy is almost always the container failing to start — read the
logs rather than guessing, the import error will be in there.


## Giving it a real database

The app already speaks Postgres — `psycopg2` is in `requirements.txt`, and the
three JSON columns switch to real `JSONB` automatically when `DATABASE_URL`
starts with `postgres`. Nothing in the code needs to change; it is one
environment variable.

### The options, honestly

| Option | Cost | Notes |
|---|---|---|
| **Neon** | free tier | Serverless Postgres, works over the internet, scales to zero like Cloud Run does. The natural fit for a free deployment. |
| **Supabase** | free tier | Same idea, also gives you a table browser. |
| **Cloud SQL** | **not free** — roughly $9/month for the smallest instance | The "proper" GCP answer. Integrates via the Cloud SQL connector, stays inside your VPC. Only worth it if you outgrow the free tiers. |

The free tiers idle-suspend after inactivity, so the first request after a quiet
spell takes a second or two to wake the database — on top of Cloud Run's own cold
start. For a private table that is unnoticeable.

### Switching over

```bash
gcloud run services update davidsino --region us-central1 \
  --set-env-vars "DATABASE_URL=postgresql://user:pass@host/dbname?sslmode=require"
```

Then seed the roster once against the new database:

```bash
DATABASE_URL="postgresql://..." python3 seed_players.py
```

With a persistent database you can drop the `SEED_CARD_*` variables — the cards
survive restarts on their own — and `--max-instances=1` stops being necessary,
since every instance now talks to the same database.

**One thing worth knowing:** the Postgres path is what the schema was originally
written for, but everything in this session has been exercised against SQLite,
because there was no Postgres to hand. The two differ only in those three JSON
columns. Run the app once against the new database and play a hand of each game
before you trust it with anything.

## Logging in to gcloud

`gcloud` is not installed on this machine. Install it, then authenticate:

```bash
# Debian/Ubuntu
curl https://sdk.cloud.google.com | bash
exec -l $SHELL
```

Then, because it opens a browser and wants input, run it yourself rather than
through me — in this session you can prefix a command with `!` to do that:

```
! gcloud auth login
! gcloud config set project <your-project-id>
```

If you would rather not install anything, Cloud Shell in the GCP console has
`gcloud` ready and can deploy straight from a cloned repo.
