# Lumina Studio Project Architecture

## Product and runtime

Lumina is a browser-based video production application. The repository has a static Vite/JavaScript client in `frontend/`, a Python standard-library HTTP API and job service in `backend/lumina/`, AI/provider adapters, a Director plus 35 specialist agents, and an FFmpeg renderer. A video job runs in a persistent Python process; this is not a serverless rendering design.

The frontend source is deployed from `frontend/`. `backend/web/` is a synchronized copy required by the existing API's static-file path. Local Python commands and tests run from `backend/`.

```text
Browser (Vite static files)
  ├─ Supabase Auth (email/password, Google, GitHub)
  └─ Bearer Supabase access token ── HTTPS ──> Python API (Railway)
                                               ├─ JWT validation / account controls
                                               ├─ PostgreSQL records (or local SQLite)
                                               ├─ Director -> specialist agents
                                               ├─ AI, stock, OAuth, Resend integrations
                                               └─ FFmpeg / persistent files (/data)
```

The Supabase publishable/anon key is intended for browser use. The API validates the access token itself. The service-role key, database URL, provider secrets, and OAuth encryption key are backend-only. Do not define those as `VITE_*` variables.

## Request and job lifecycle

1. The browser obtains an access token from Supabase Auth and sends it as `Authorization: Bearer ...`.
2. `backend/lumina/server.py` verifies identity, applies CORS/rate limits, account status, maintenance and supported feature gates, then dispatches the route.
3. Job routes validate input and scope jobs to the authenticated user/workspace.
4. `backend/lumina/app.py` persists the job and queues work to a background worker.
5. `backend/lumina/agents/director.py` runs ordered agent steps, emits events for the UI, saves state and step checkpoints, pauses at review gates, and resumes completed work after retry.
6. `backend/lumina/render.py` invokes FFmpeg; job results and media remain on persistent storage, not in PostgreSQL.
7. Publishing is a separate user-confirmed workflow. OAuth tokens are encrypted; upload state and Resend notifications are recorded in PostgreSQL/SQLite.

The API uses JSON for ordinary requests and server-sent events (SSE) for live job progress. Large uploads and downloads have bounded request sizes and ticket-scoped file access. Review links are separate, expiring read-only/comment workflows.

## Data stores

| Store | Contents | Production location |
|---|---|---|
| PostgreSQL | Users' app records, jobs, events, steps, usage, audit, workspaces, media metadata, publishing, admin controls | Railway database/private connection |
| SQLite | Local/offline version of the application tables | `backend/data/lumina.db` |
| Persistent filesystem | Generated videos, render intermediates, uploaded media, caches, logs, backups, settings | Railway volume mounted at `/data` |
| Supabase Auth | User identities and login sessions | Supabase project |
| Supabase profile/preferences tables | Profile and browser preference records protected by RLS | Supabase Postgres |

Schema initialization is performed by `backend/lumina/db.py`. The SQLite-to-PostgreSQL import utility only imports into a fresh application database and refuses non-empty destinations. Back up files and database together; PostgreSQL alone is not a backup of generated video files.

## Main source map

| Path | Responsibility |
|---|---|
| `backend/run.py` | CLI, local server startup, diagnostics and backup commands |
| `backend/lumina/server.py` | HTTP routing, auth boundary, JSON, CORS, SSE and file responses |
| `backend/lumina/app.py` | Application lifecycle, jobs, queue, app-level workflows |
| `backend/lumina/admin.py` | Single-owner binding, admin permissions and operational views/actions |
| `backend/lumina/db.py` | SQLite/PostgreSQL DDL and parameterized DB helpers |
| `backend/lumina/security.py` | Input checks, JWT validation, tokens, file tickets and redaction |
| `backend/lumina/agents/` | Director, agent registry and specialist implementations |
| `backend/lumina/publishing.py` | Platform OAuth/publishing, encrypted tokens and Resend delivery |
| `backend/lumina/render.py`, `backend/lumina/media.py` | FFmpeg rendering and media operations |
| `frontend/app.js`, `frontend/index.html`, `frontend/style.css` | Browser app, auth UX, product and admin interfaces |
| `backend/supabase/migrations/` | Supabase profile/preferences schema and policies |
| `backend/scripts/migrate_sqlite_to_postgres.py` | Explicit SQLite metadata/files import tool |

## Deployment shape

The supported split is a static frontend on Vercel or Cloudflare Pages and the persistent API/renderer on Railway. The Railway Dockerfile builds the synchronized frontend source in `backend/web/` and the Python API serves the Vite output from `/`. Railway's Python container needs FFmpeg, a persistent volume, network access to the private PostgreSQL database and HTTPS ingress. Cloudflare Workers or Vercel Functions are not replacements for the long-running Python/FFmpeg worker in this repository.

`frontend/vercel.json` contains static frontend security headers. `frontend/wrangler.toml` identifies the Pages build output. Railway's root directory is `backend/`, where `railway.json` uses the Dockerfile and checks `/api/health`. Hosting and provider prices/limits change; consult current provider plans before production use.

## Current boundaries

- Billing, subscriptions, payments, and revenue are not implemented and are deferred.
- Database restore is an operator-led, manual recovery procedure; the admin UI does not perform an in-place restore.
- Admin environment controls show whether allow-listed variables exist, never their values. Secret rotation belongs in the backend host's secret manager.
- Agent live parameter tuning is not implemented. Optional agents may be disabled; critical agents cannot.
- Local bearer-token mode is for local development/compatibility, not a substitute for Supabase user accounts and owner bootstrap in a multi-user hosted deployment.
