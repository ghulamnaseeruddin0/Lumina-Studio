# Deployment and Credentials Guide

## 1. Supported deployment layout

| Component | Recommended host | Why |
|---|---|---|
| Static browser app | Vercel **or** Cloudflare Pages | Vite builds static files from `web/`; quick CDN delivery |
| Python API, agent worker and FFmpeg renderer | Railway | Persistent Python process, Docker, HTTP/SSE, long-running work and volume support |
| Application records | Railway PostgreSQL (or reachable PostgreSQL) | Production application schema |
| Identity, OAuth sign-in, profile/preferences | Supabase | Auth/JWT and existing profile integration |
| Generated videos/media/backups | Railway persistent volume mounted `/data` | Large files and job state are not stored in PostgreSQL |

This repository does not make the Python/FFmpeg job worker into a Vercel Function or Cloudflare Worker. Do not deploy the renderer as a short-lived serverless request. Hosting limits, prices, included credits, bandwidth and free tiers change; check current official pricing and terms before selecting a production plan. No provider is promised free forever.

## 2. Configure Supabase

1. Create a Supabase project and run the profile/settings migration in `supabase/migrations/` using the Supabase SQL Editor.
2. Enable email/password. If desired, enable Google and GitHub and supply credentials from those providers.
3. Configure each OAuth provider's callback URL using the URL Supabase displays.
4. Set Supabase **Site URL** and allowed redirect URLs for the exact frontend domain and local development URL.
5. Copy the project URL and publishable/anon key to the frontend build variables.
6. Create the intended owner account. Obtain its Auth UUID from the trusted Supabase dashboard; configure it only on the backend as `LUMINA_SUPER_ADMIN_USER_ID`.
7. Keep service-role key and database credentials backend-only. Enable owner MFA in Supabase; it is not configured by a frontend code flag.

JWT signing: asymmetric Supabase tokens use the Supabase JWKS endpoint; configure `SUPABASE_JWT_SECRET` only when your project uses HS256 signing.

## 3. Railway backend deployment

1. Create a Railway project and deploy this repository using its included `Dockerfile` and `railway.json`.
2. Provision PostgreSQL and attach a persistent volume mounted at `/data`.
3. Set the required environment variables in the backend service (table below). Set `REQUIRE_POSTGRES=true` and `REQUIRE_SUPABASE_AUTH=true` for a production deployment.
4. Set the API's exact frontend origin in `LUMINA_ALLOWED_ORIGINS`; avoid wildcards.
5. Set Railway's HTTPS public domain in `LUMINA_PUBLIC_URL` and the selected frontend origin in `LUMINA_FRONTEND_URL`.
6. Configure CPU/memory, volume size, backup policy, health check `/api/health`, service restart and log retention based on expected render workload.
7. Deploy, verify the HTTPS health endpoint, owner login, database backend, one test video and restore plan.

The Railway service must have FFmpeg installed (provided by the Dockerfile), write access to `/data`, outbound HTTPS to configured providers, and private network access to PostgreSQL. Consider worker resource limits: Long videos consume CPU, disk, network bandwidth and time.

### Migration from SQLite

Stop the source app and back up SQLite plus media directories. Provision a fresh empty target PostgreSQL database and set `LUMINA_DATABASE_URL`; run:

```powershell
python scripts/migrate_sqlite_to_postgres.py "C:\path\to\data\lumina.db"
```

The importer refuses non-empty target application tables/media destinations and records the source hash. Review and verify counts and file paths before moving traffic. Do not run this against production data without a separate backup and approved migration window.

## 4. Vercel frontend deployment

1. Create a Vercel account and import the GitHub repository.
2. Set **Root Directory** to `web`.
3. Choose Vite or configure **Build Command** `npm run build`, **Output Directory** `dist`, and install command `npm install`.
4. Define only the frontend build variables below.
5. Add the Vercel domain to Supabase allowed URLs and backend `LUMINA_ALLOWED_ORIGINS`.
6. Deploy; visit the generated HTTPS domain, sign in, and verify the backend API.
7. Add a custom domain and configure DNS in Vercel if desired. Redeploy after build variable changes because Vite embeds them at build time.

`web/vercel.json` supplies static security headers. Vercel hosts the frontend only; it does not host this persistent FFmpeg worker.

## 5. Cloudflare Pages frontend deployment

1. Create a Cloudflare account and select **Workers & Pages > Create > Pages** (static Pages project).
2. Connect the GitHub repository.
3. Set root directory `web`, build command `npm run build`, output directory `dist`.
4. Set the same three frontend build variables as for Vercel.
5. Add the Pages/custom domain origin to Supabase redirect allow-list and Railway `LUMINA_ALLOWED_ORIGINS`.
6. Deploy and verify sign-in, API calls and video streaming.
7. Configure a custom domain/DNS in Pages and redeploy after environment changes.

`web/wrangler.toml` identifies the static output. Do not configure this project as a Worker renderer; static Pages does not replace the Railway API.

## 6. Environment variables and credentials

Values below are names/purposes only. Obtain secrets from the named provider and place them in the **Railway backend variable manager** unless marked frontend. `.env.example` contains safe empty placeholders. Never paste actual credentials into docs, source control, browser code, screenshots, or issue reports.

### Frontend build-time variables

| Variable | Purpose / source | Required |
|---|---|---|
| `VITE_SUPABASE_URL` | Supabase project URL, Supabase project settings | Yes for Supabase login |
| `VITE_SUPABASE_ANON_KEY` | Supabase publishable/anon key; intended for browser use | Yes for Supabase login |
| `VITE_API_URL` | HTTPS Railway API origin, no trailing slash | Yes when frontend and API are separate |

These are public browser configuration; do not put any private credential under a `VITE_` name.

### Backend runtime, owner and database

| Variable | Purpose / where to obtain | Required |
|---|---|---|
| `LUMINA_SUPER_ADMIN_USER_ID` | Existing owner UUID from Supabase Auth dashboard; binds exactly one owner | Required for Admin owner |
| `SUPABASE_URL` | Supabase project URL; must match frontend project | Required for Auth |
| `SUPABASE_ANON_KEY` | Supabase publishable/anon key for backend Auth configuration | Required for Auth |
| `SUPABASE_JWT_SECRET` | Supabase JWT secret, only for HS256 tokens | Conditional |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase server-side Auth Admin operations (invite/list/ban/delete); Supabase project settings | Required for Admin Auth user operations |
| `LUMINA_DATABASE_URL` | PostgreSQL connection URL from database provider | Required for hosted Postgres; takes precedence |
| `DATABASE_URL` | Alternative PostgreSQL URL provided by Railway | Alternative |
| `REQUIRE_POSTGRES` | Set `true` to fail startup without a DB URL | Recommended in production |
| `REQUIRE_SUPABASE_AUTH` | Set `true` to fail startup without Supabase Auth config | Recommended in production |
| `LUMINA_TOKEN` | Random local bearer-token compatibility secret; generate privately | Strongly recommended; not a user account |
| `LUMINA_FILE_SIGNING_KEY` | Signing key for time-bounded file tickets; generate privately | Optional but recommended stable key |
| `LUMINA_AUTH_AUDIT_SALT` | Salt for hashing session source-IP values in audit sessions | Optional; keep stable/private |
| `LUMINA_DATA` | Data directory; use `/data` on Railway persistent volume | Required in Railway layout |
| `LUMINA_ASSETS` | Optional asset directory override | Optional |
| `LUMINA_ALLOWED_ORIGINS` | Comma-separated exact browser origins | Required for separate frontend/API |
| `PORT` | Platform-provided HTTP port | Railway supplies it |
| `FFMPEG_PATH`, `FFPROBE_PATH` | Explicit executable paths if not on `PATH` | Optional; image installs FFmpeg |

The owner UUID must be configured at each backend start. First configured startup binds the singleton row; a conflicting later UUID intentionally fails startup. Removing the variable leaves the owner unrecognized rather than promoting someone else.

In Supabase multi-user mode, shared API-key/output setting changes are permission
checked, shared-token rotation is owner-only, and backup creation is limited to
the admin backup grant. Ordinary user profile and publishing preferences remain
separate account-scoped workflows.

### AI and stock provider credentials

| Variable | Purpose / source | Required |
|---|---|---|
| `GEMINI_API_KEY` | Gemini API key from Google AI Studio | At least one AI provider recommended |
| `OPENROUTER_API_KEY` | OpenRouter key from OpenRouter account | Optional alternative/model fallback |
| `GROQ_API_KEY` | Groq API key | Optional fallback |
| `CEREBRAS_API_KEY` | Cerebras API key | Optional fallback |
| `CLOUDFLARE_API_TOKEN` | Cloudflare AI token with needed access | Optional |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID used in configured AI endpoint | Conditional on Cloudflare AI |
| `PEXELS_API_KEY` | Pexels stock API key | Optional stock source |
| `PIXABAY_API_KEY` | Pixabay stock API key | Optional stock source |

AI/stock keys can also be added through app Settings where supported; on Railway, prefer backend environment variables/secret manager. Provider model availability, rate limits and charges depend on each provider plan.

### Publishing OAuth, external video URLs and email

| Variable | Purpose / source | Required |
|---|---|---|
| `LUMINA_PUBLIC_URL` | Public HTTPS Railway origin for callbacks and short-lived video links | Required for publishing |
| `LUMINA_FRONTEND_URL` | Frontend origin used after OAuth callback/invitation | Recommended |
| `LUMINA_OAUTH_ENCRYPTION_KEY` | Fernet encryption key for stored connected-account tokens; generate via `python -c "from lumina.publishing import fernet_key; print(fernet_key())"` | Required for connected publishing |
| `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` | Google OAuth client with YouTube upload permissions | Conditional |
| `META_APP_ID`, `META_APP_SECRET` | Meta developer app; Facebook Page and eligible Instagram professional workflows | Conditional |
| `META_GRAPH_VERSION` | Optional API version override; app default otherwise | Optional |
| `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` | TikTok developer app and Content Posting access | Conditional |
| `RESEND_API_KEY` | Resend API key | Optional email |
| `RESEND_FROM_EMAIL` | Verified sender address/domain in Resend | Optional email |

Register exact callbacks: `/api/publishing/callback/youtube`, `/api/publishing/callback/meta`, `/api/publishing/callback/tiktok` on the Railway origin. Provider review, scopes, account eligibility, URL verification and quotas remain external. Keep the encryption key stable and backed up; losing/changing it requires account reconnection.

### Deployment credentials

GitHub/Vercel/Railway/Cloudflare account tokens are used in each platform's own connection flow. Do not copy deployment tokens into Lumina's app settings. Configure DNS records only in the relevant domain host and verify TLS before enabling authentication callbacks.

## 7. Cost and operational caveats

- Static hosting plans may include quotas, build minutes, bandwidth or commercial-use conditions. Review current Vercel/Cloudflare terms.
- Railway charges depend on current plan, compute, volume, egress and database usage. Rendering can be CPU/storage intensive.
- Supabase free/paid limits and database/auth behavior can change.
- AI, stock, OAuth and email usage is controlled by providers. A nominally free API may have throttles, review requirements or paid overages.
- Keep backups and generated media storage costs in mind. PostgreSQL backups do not automatically include `/data/jobs` or library files.

## 8. Go-live and troubleshooting

Use [the deployment checklist](./DEPLOYMENT-CHECKLIST.md). Common issues:

- **CORS 403:** use the exact frontend origin in `LUMINA_ALLOWED_ORIGINS`, including scheme and no path.
- **JWT rejected:** verify URL/project key consistency, system time, issuer and whether HS256 needs its JWT secret.
- **No admin owner:** verify the Auth UUID and `LUMINA_SUPER_ADMIN_USER_ID` on the backend, then restart; inspect startup errors for a conflicting singleton.
- **Auth user actions return 503:** configure backend-only `SUPABASE_SERVICE_ROLE_KEY`; never put it in Vite variables.
- **Publishing callback fails:** verify Railway HTTPS URL, provider callback, app permissions, encryption key and destination account eligibility.
- **No media access from Meta/TikTok:** ensure public HTTPS API origin, valid expiring links, and provider URL-domain verification if required.
- **Build uses old endpoint:** update build-time `VITE_API_URL` and trigger a new static deployment.
- **No backup:** install `postgresql-client` for Postgres backup command (`pg_dump`); check persistent storage permissions and free space.

No deployment is claimed complete by this manual. Configure real provider accounts and verify with a staging deployment before production.
