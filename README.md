# Slow-Pitch Team Manager — API

Django + Django REST Framework API for running a rec slow-pitch softball team:
roster, schedule, availability, lineups, results and season stats. The React
frontend lives in its own repository and talks to this API with token auth.

Everything runs in Docker; you don't need Python or Postgres installed.

## Getting started

```bash
make migrations   # first time only: creates softball/migrations/0001_initial.py — commit it
make up           # Postgres + API at http://localhost:8000 (migrations apply automatically)
```

In a second terminal:

```bash
make test         # run the test suite against Postgres
make superuser    # create an admin login for /admin/
make seed         # optional demo team: log in as manager / softball-demo-1
```

`make help` lists everything else (`down`, `logs`, `shell`, `psql`,
`reset-db`, `prod-image`). Code changes reload automatically because the
project folder is mounted into the container. After changing models, run
`make migrations` and restart with `make up`.

Browse the API at http://localhost:8000/api/ (log in through `/admin/` first
to use the browsable API).

### Connecting the React app

The dev setup allows requests from Vite's default `http://localhost:5173`.
Point the React app at `http://localhost:8000/api`, call
`POST /api/auth/login/`, and send the returned token on every request as
`Authorization: Token <key>`. For other origins, change
`CORS_ALLOWED_ORIGINS` in `docker-compose.yml` (or in your host's settings
in production).

### Docker files

| File | Purpose |
|---|---|
| `Dockerfile` | Production image: installs requirements, collects static files, runs gunicorn as a non-root user |
| `docker/entrypoint.sh` | Runs `migrate` before every command (skip with `DJANGO_MIGRATE=0`) |
| `docker-compose.yml` | Local dev: Postgres 16 + Django dev server, source mounted for live reload |
| `Makefile` | Shortcuts for the compose commands |

### Without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # DJANGO_DEBUG=1, SQLite by default
python manage.py migrate && python manage.py runserver
```

## Layout

```
config/            settings (env-driven), urls
accounts/          register / login / logout / me (token auth)
softball/
  models.py        Team, Season, TeamMembership, Game, GameAvailability, Lineup, LineupSpot, PlayerGameStats
  permissions.py   team-scoped access helpers
  serializers.py   validation for roster, games, lineups, stats
  stats.py         the one place batting stats are calculated
  views.py         DRF viewsets + bulk actions
  tests/           API tests for every safeguard in the spec
docker/            container entrypoint
```

## API

All endpoints are under `/api/` and need `Authorization: Token <key>` except
register, login and `/api/health/`.

| Method | Endpoint | Who | Purpose |
|---|---|---|---|
| POST | `/auth/register/` | anyone | Create an account, returns `{token, user}` |
| POST | `/auth/login/` | anyone | Returns `{token, user}` |
| POST | `/auth/logout/` | member | Revoke the token |
| GET | `/auth/me/` | member | Current user |
| GET / POST | `/teams/` | member | My teams / create one (you become manager) |
| GET / PATCH / DELETE | `/teams/{id}/` | member / manager | Team details |
| GET / PUT / DELETE | `/teams/{id}/logo/` | member / manager | Team logo image; PUT a multipart `logo` file (PNG/JPEG/WebP/GIF ≤ 5 MB, stored as a ≤256 px PNG) |
| GET / POST | `/teams/{id}/roster/` | member / manager | Roster (`?season=`, `?include_inactive=1`) / add player |
| GET / POST | `/teams/{id}/seasons/` | member / manager | Seasons |
| GET / PATCH / DELETE | `/seasons/{id}/` | member / manager | Season details |
| GET / POST | `/seasons/{id}/games/` | member / manager | Schedule (`?status=`) / add game |
| GET | `/seasons/{id}/leaderboard/?sort=hits` | member | Ranked season stats |
| GET / PATCH | `/memberships/{id}/` | member / manager | Role, jersey, sub flag, `is_active`, link account via `username` |
| GET | `/memberships/{id}/stats/?season=` | member | Player profile: season line + game log |
| GET / PATCH / DELETE | `/games/{id}/` | member / manager | Details; PATCH `status` to `scheduled`/`cancelled` |
| GET / PATCH | `/games/{id}/availability/` | member | Responses + who hasn't answered / `{"status": "yes"}` |
| GET / PUT | `/games/{id}/lineup/` | member / manager | Whole lineup in one request |
| POST | `/games/{id}/result/` | manager | `{"team_score", "opponent_score"}`, marks completed |
| GET / PUT | `/games/{id}/stats/` | member / manager | Every player's line in one request |

Lineup PUT body:

```json
{"players": [
  {"membership_id": 12, "batting_order": 1, "defensive_position": "SS"},
  {"membership_id": 7,  "batting_order": 2, "defensive_position": "CF"}
]}
```

Positions: `P C 1B 2B 3B SS LF LCF CF RCF RF ROV EH`. Stats PUT body: a list of
`{"membership_id", "at_bats", "singles", "doubles", "triples", "home_runs", "walks"}`
(`hits` is computed; if you send it, it must match).

Leaderboard `sort` options: `batting_average`, `on_base_percentage`,
`slugging_percentage`, `hits`, `home_runs`, `doubles`, `triples`, `walks`,
`at_bats`, `games_played`. Anything else is a 400.

## Decisions worth knowing

- **Roster entry = player identity.** Stats, lineups and availability point at
  `TeamMembership`, not `User`. Managers can add players before they sign up
  and link the account later by PATCHing `username`. A membership with no
  season spans every season (handy for managers and regulars); one with a
  season is just for that season.
- **Substitutes** are ordinary memberships with `is_substitute=true`. They
  aren't listed as "missing" for availability until a manager adds a response
  for them.
- **Results are derived.** `result` (W/L/T) comes from the scores. Games only
  become `completed` through `/result/`; reopening or cancelling a completed
  game clears its score.
- **Stats only count from completed games.** Season numbers are ORM
  aggregates over `PlayerGameStats`; nothing is stored twice. The leaderboard,
  player profile and box score all go through `stats.with_rates`.
  AVG with zero at-bats is `null` (`batting_average_display` is `—`). OBP is
  simplified: (H + BB) / (AB + BB).
- **Bulk PUTs replace everything** for that game inside a transaction, so a
  rejected save leaves the previous lineup/stats untouched.
- **Isolation.** Every queryset starts from the teams you actively belong to,
  so another team's IDs return 404; writes by players return 403. Database
  constraints back up the important rules (one stat line per player per game,
  hits = 1B+2B+3B+HR ≤ AB, unique batting slots and positions, completed games
  have scores).
- **Logos live in the database**, shrunk to a small PNG, because Render's
  free web service has no persistent disk for uploaded files. `logo_version`
  on a team changes whenever the logo does, for cache-busting.
- **Deactivate, don't delete** players (`is_active=false`) so their stats stay.
  A team can't lose its last active manager.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `DJANGO_DEBUG` | `0` | `1` for local dev |
| `DJANGO_SECRET_KEY` | — | Required when debug is off |
| `DATABASE_URL` | SQLite | e.g. `postgres://user:pass@host:5432/db` |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | `RENDER_EXTERNAL_HOSTNAME` is added automatically |
| `CORS_ALLOWED_ORIGINS` | Vite dev server | Frontend origins |
| `CSRF_TRUSTED_ORIGINS` | empty | Needed for admin behind a custom domain |
| `DJANGO_SECURE_SSL_REDIRECT` | `0` | Turn on once HTTPS is confirmed working |
| `AUTH_THROTTLE_RATE` | `20/minute` | Login/register rate limit |
| `DJANGO_MIGRATE` | `1` | Set `0` to skip the automatic `migrate` in the container entrypoint |
| `WEB_CONCURRENCY` | `2` | gunicorn workers |

## Deploying

The `Dockerfile` runs migrations on start and serves gunicorn on `$PORT`, so
it deploys as-is to Render as a Docker web service. Give it a Postgres
`DATABASE_URL`, a generated `DJANGO_SECRET_KEY`, and set
`CORS_ALLOWED_ORIGINS` to the React app's deployed URL. Use
`/api/health/` as the health check path.
