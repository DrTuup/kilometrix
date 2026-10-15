# Kilometrix: plan

Track kilometres driven in a Renault Megane E-Tech, with start and end location per trip. Trips are shown in a web UI and can be tagged with free-form labels (work, personal, anything).

## Scope

- Poll one VIN for odometer and location.
- Detect trips from odometer changes.
- Store per trip: start/end time, km, start/end location (nullable), tags.
- Do **not** store intermediate positions. They are only used for trip detection, in memory or in `poll_state`.
- Web UI: trip list, totals per tag and period, date filter, tagging.
- Run as a single poller replica plus a web deployment in Kubernetes.

## Decisions

| Topic | Decision |
|---|---|
| Language | Python 3.12+, managed with `uv` |
| Renault access | `renault-api` (unofficial MyRenault/Kamereon API). One login can expose several Kamereon accounts; select the account whose `vehicleLinks` contain the VIN. Do not assume `accounts[0]`. |
| Web | FastAPI, plain HTML + small JS served by FastAPI |
| ORM / migrations | SQLAlchemy + Alembic |
| Database (local) | SQLite file, selected via `DATABASE_URL` |
| Database (k8s) | PostgreSQL via CloudNativePG (CNPG), selected via `DATABASE_URL` |
| Tags | Free-form strings, normalised to lowercase. Not an enum. |
| Timezone | Store UTC (`timestamptz`). Display zone configurable via `DISPLAY_TIMEZONE`, default `Europe/Brussels`. |
| Location | Start/end lat/lon stored when the API returns them; nullable. UI shows "no location" when missing. |
| Car | Renault Megane E-Tech (electric) |

## Data model

```
trips
  id               bigint primary key
  started_at       timestamptz
  ended_at         timestamptz
  start_odometer   numeric (km)
  end_odometer     numeric (km)
  distance_km      numeric  (end - start; stored for fast totals)
  start_lat, start_lon   double precision, nullable
  end_lat, end_lon       double precision, nullable
  position_at      timestamptz  (when end position was read)
  created_at       timestamptz

tags
  id    primary key
  name  text unique  (free-form, lowercase)

trip_tags
  trip_id  -> trips
  tag_id   -> tags
  primary key (trip_id, tag_id)

poll_state            (single row, id = 1)
  last_odometer   numeric
  last_seen_at    timestamptz
  last_lat, last_lon   double precision, nullable
  in_trip         boolean
  trip_id         -> trips, nullable (open trip)
```

`poll_state` keeps detection state across restarts, so a pod restart does not lose or duplicate an in-progress trip.

## Trip detection

- **Start:** odometer increases compared with `poll_state.last_odometer`. `start_odometer` is the previous reading. `start_lat/lon` is the last position seen while parked.
- **Ongoing:** odometer keeps increasing. Update `end_odometer` and the latest position.
- **End:** odometer unchanged for N consecutive polls (default 2) to avoid false ends. Record end location and timestamp.
- **Restart:** if `poll_state.in_trip` is true at startup, resume that trip instead of creating a new one.
- Distance always comes from the odometer, not from GPS.

## Components

1. **Poller** (worker process)
   - Polls every `POLL_INTERVAL_SECONDS` (default 300).
   - Refreshes the Renault session as needed, backs off on API errors.
   - Logs when location is missing.
   - Runs trip detection and writes to the database.
2. **Web app** (FastAPI)
   - `GET /trips`: list, with date and tag filters.
   - `GET /totals`: km per tag and per period.
   - `PATCH /trips/{id}/tags`: set tags.
   - Static HTML page calling these endpoints.
   - Formats and filters dates in `DISPLAY_TIMEZONE`.
3. **Migrations:** Alembic. Run locally on startup or manually; in k8s as a Job before rollout.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `RENAULT_USERNAME` | MyRenault login | required |
| `RENAULT_PASSWORD` | MyRenault password | required |
| `RENAULT_VIN` | Car VIN | required |
| `RENAULT_LOCALE` | e.g. `nl_BE` (underscore, `renault-api` rejects `nl-BE`) | `nl_BE` |
| `RENAULT_COUNTRY` | e.g. `BE` | `BE` |
| `DATABASE_URL` | SQLite locally, Postgres in k8s | `sqlite:///./kilometrix.db` |
| `POLL_INTERVAL_SECONDS` | Poll frequency | `300` |
| `DISPLAY_TIMEZONE` | UI timezone | `Europe/Brussels` |

## Kubernetes

- **Poller:** Deployment, `replicas: 1`, strategy `Recreate`.
- **Web:** Deployment (1+ replicas), Service, Ingress.
- **Migration:** Job before rollout (Helm/Argo/Flux hook if used).
- **Secrets:** Renault credentials and VIN in a Secret.
- **Database:** CNPG `Cluster`, or a database in the existing CNPG cluster. The app reads the connection from the CNPG `-app` secret.

## Phases

1. **Spike** (done, see Spike result): a one-off script logged in and printed odometer and location for the VIN. The script was removed once the poller covered the same read path.
2. **Poller + detection:** SQLAlchemy models, Alembic migration, detection logic, tests against SQLite. Verify against Postgres in a container.
3. **Web API + UI:** list, totals, tagging, timezone display.
4. **Kubernetes manifests:** image build, CNPG database, Deployments, Job, Secret.
4b. **Container and release pipeline** (done locally, not yet pushed): `Dockerfile` (multi-stage, uv locked deps, non-root, runtime `DATABASE_URL=sqlite:////data/kilometrix.db`), `.dockerignore` (no `.env`, `*.db`). CI in `.github/workflows/ci.yml` (pytest + Postgres service, `alembic check`, commitlint on PRs, Docker build without push). Release in `.github/workflows/release.yml`: semantic-release on `main` (conventional commits) tags `vX.Y.Z`, updates `CHANGELOG.md`, creates a GitHub release, then builds and pushes `ghcr.io/<owner>/kilometrix` (`X.Y.Z` and `latest`, linux/amd64 and arm64). Versioning: Hatchling with `hatch-vcs` derives the version from git tags (no version in `pyproject.toml`). Docker builds receive it as `--build-arg VERSION=...` because `.git` is excluded from the build context. Web container needs no Renault credentials (`WebSettings` in `web.py`).
5. **Later:** tag rules (e.g. "starts near home" → personal), map view, CSV export.

## Risks and notes

- The Renault API is unofficial and reverse-engineered. Endpoints and keys can change. Keep the poll rate modest and the use personal.
- Start/end positions can be a few minutes off, depending on the poll interval. Km stay exact.
- If the car has no connectivity, the end location may be stale. Show the position timestamp in the UI.
- Trips shorter than one poll interval that return the odometer to the same value can be missed. Unlikely in practice.

## Spike result

Recorded 2026-10-15 from the one-off spike script (since removed).

- **Login:** ok (`RENAULT_LOCALE=nl_BE`, `RENAULT_COUNTRY=BE`).
- **Accounts:** the login exposes 2 Kamereon accounts. The first is empty; the VIN is in the second. Account selection matches the VIN against `vehicleLinks`.
- **Odometer (`totalMileage`):** 69881.0 km.
- **Location:** fields present. `lastUpdateTime` 2026-10-15T17:48:30Z. Lat/lon redacted (personal data; the values are in the local output only).
- **Failures seen while debugging:** `err.func.wired.empty-vehicle-link` ("No vehicle in garage") from using the empty first account. HTTP 403 "Locale not found" from the hyphen locale `nl-BE`.
