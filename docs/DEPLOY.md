# Deploy Lumina Studio

This project now also supports Vercel for the static web client. For complete
step-by-step Vercel/Cloudflare/Railway setup, credential sourcing and cost
caveats, see the [deployment and credentials guide](./03-Deployment-and-Credentials-Guide.md)
and [production checklist](./DEPLOYMENT-CHECKLIST.md). Billing/payments are
deferred and are not implemented.

This guide walks through Cloudflare Pages as the static frontend example; the
same Vite app can be hosted on Vercel using the separate deployment guide. The
Python/FFmpeg studio runs as a persistent Railway service. Supabase provides
authentication, while PostgreSQL stores account-scoped application records.

> **Storage model:** hosted records use PostgreSQL. Project state, generated
> videos, uploaded library files, caches, and backups remain on Railway's
> persistent `/data` volume. Local/offline development uses SQLite.

## 1. Configure Supabase

1. Create a Supabase project and run
   [`backend/supabase/migrations/202611110001_initial_profile_settings.sql`](../backend/supabase/migrations/202611110001_initial_profile_settings.sql)
   in the SQL Editor.
2. In Authentication, enable email/password and the Google and GitHub providers.
   Add each provider's credentials and the Supabase callback URL shown by the
   provider setup page.
3. Set the site's URL and allowed redirect URLs to the selected frontend URL
   (and your local development URL, if needed).
4. Copy the project URL and publishable/anon key for the frontend environment.

## 2. Deploy the API and renderer to Railway

1. Create a Railway project from this repository and set its **Root Directory**
   to `backend`. Railway then uses `backend/Dockerfile` and `backend/railway.json`.
2. Attach a persistent volume mounted at `/data`.
3. Provision a PostgreSQL database and set these service variables:

   | Variable | Value |
   |---|---|
   | `PORT` | Supplied by Railway; the launcher reads this automatically |
   | `LUMINA_DATA` | `/data` |
   | `LUMINA_TOKEN` | A long, random secret for local-token compatibility |
   | `LUMINA_ALLOWED_ORIGINS` | Exact frontend origin(s), comma-separated |
   | `SUPABASE_URL` | Supabase project URL |
   | `SUPABASE_ANON_KEY` | Supabase publishable/anon key |
   | `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` | Public Supabase config embedded into the frontend during the Docker build |
   | `VITE_API_URL` | Optional API origin for the bundled frontend; leave empty to use this Railway service |
   | `SUPABASE_JWT_SECRET` | Only if the project's access tokens use HS256 |
   | `LUMINA_DATABASE_URL` | Private PostgreSQL connection URL (or use Railway's `DATABASE_URL`) |
   | `REQUIRE_POSTGRES` | `true` to fail startup unless PostgreSQL is configured |
   | `REQUIRE_SUPABASE_AUTH` | `true` to fail startup unless Supabase Auth is configured |
   | `LUMINA_PUBLIC_URL` | HTTPS Railway service URL; used for OAuth callbacks and short-lived video links |
   | `LUMINA_FRONTEND_URL` | HTTPS frontend URL for OAuth completion redirects |
   | `LUMINA_OAUTH_ENCRYPTION_KEY` | Fernet key used to encrypt connected platform tokens |
   | `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` | Google OAuth client with YouTube upload enabled |
   | `META_APP_ID` / `META_APP_SECRET` | Meta app with the approved Pages and Instagram permissions |
   | `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` | TikTok app with Content Posting API access |
   | `RESEND_API_KEY` / `RESEND_FROM_EMAIL` | Resend API key and verified sender address |

   Set any AI and stock-provider keys in Lumina's Settings or the service
   environment. Do not put private service secrets in Cloudflare's frontend
   variables. Supabase asymmetric signing keys are verified using Supabase's
   JWKS endpoint; HS256 requires `SUPABASE_JWT_SECRET`.
4. Deploy and confirm that Railway's `/api/health` health check succeeds. Keep
   the Railway service URL; it must be HTTPS.
5. On first startup, Lumina initializes its PostgreSQL application schema.
   Keep the Railway volume attached because render state and media are stored
   there, not in PostgreSQL.

The Dockerfile builds the browser UI from `backend/web/` and serves it from the
Railway service. Railway exposes configured variables to Docker builds when
they are declared as `ARG`; the `VITE_` values are public browser configuration,
not secrets. The separately hosted `frontend/` project uses the same Vite
variables.

### Configure publishing providers and email

Set `LUMINA_PUBLIC_URL` to the exact HTTPS Railway service origin and
`LUMINA_FRONTEND_URL` to the selected frontend origin. Register these callback
URLs with each provider:

- Google: `https://<railway-host>/api/publishing/callback/youtube`
- Meta: `https://<railway-host>/api/publishing/callback/meta`
- TikTok: `https://<railway-host>/api/publishing/callback/tiktok`

Generate a Fernet key once with
`python -c "from lumina.publishing import fernet_key; print(fernet_key())"` and
store it as `LUMINA_OAUTH_ENCRYPTION_KEY` in Railway. Keep it stable and backed
up securely: changing it makes existing connected-account tokens unreadable.
Do not use the generated key in frontend variables.

Enable the official provider APIs and request their required permissions.
Provider app review, account eligibility, quotas, video constraints, and (for
TikTok URL-pull publishing) verification of the Railway video URL domain are
external requirements. Meta publishing requires a Facebook Page; Instagram
publishing requires an eligible professional Instagram account linked to a
Page. Instagram publishing uses the Reels API. Configure a verified Resend
sender domain before setting `RESEND_FROM_EMAIL`. Lumina only publishes after
the user reviews the video and confirms the selected accounts.

## 3. Deploy the web console to Cloudflare Pages

Create a Pages project from the same repository with:

- **Root directory:** `frontend`
- **Build command:** `npm run build`
- **Build output directory:** `dist`

Set these build-time environment variables:

| Variable | Value |
|---|---|
| `VITE_SUPABASE_URL` | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Supabase publishable/anon key |
| `VITE_API_URL` | HTTPS Railway service URL, without a trailing slash |

Deploy again after setting the variables. The Supabase publishable/anon key is
intended for browser use; Row Level Security policies protect user profile and
preference records. Never expose a Supabase service-role key in the browser.

## Local frontend development

Copy `frontend/.env.example` to `frontend/.env.local`, set the three `VITE_` variables,
then run:

```powershell
cd frontend
npm install
npm run dev
```

Add the local frontend origin to `LUMINA_ALLOWED_ORIGINS` on the API. OAuth
providers also need the local URL in Supabase's allowed redirect URLs.
