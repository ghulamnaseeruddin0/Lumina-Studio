# Production Deployment Checklist

## Before deployment

- [ ] Choose owner identity; create its Supabase Auth account and enable owner MFA.
- [ ] Run the checked-in Supabase migration(s) and verify Row Level Security policies.
- [ ] Set backend secrets from `.env.example` in Railway; never add secrets to Git or frontend variables.
- [ ] Set the exact owner UUID as `LUMINA_SUPER_ADMIN_USER_ID`.
- [ ] Provision PostgreSQL and a persistent Railway volume mounted at `/data`.
- [ ] Configure `REQUIRE_POSTGRES=true` and `REQUIRE_SUPABASE_AUTH=true`.
- [ ] Set exact frontend origins in `LUMINA_ALLOWED_ORIGINS`.
- [ ] Configure Supabase site URL/allowed redirects and OAuth providers.
- [ ] Configure provider OAuth apps, app review/scopes and verified Resend sender only if using those integrations.
- [ ] Confirm `LUMINA_OAUTH_ENCRYPTION_KEY`, file signing key and local compatibility token are strong and backed up privately.
- [ ] Review provider costs, usage limits, data retention and privacy obligations.

## Build and rollout

- [ ] From `backend/`, run `python -m unittest discover -s tests -v`.
- [ ] From `backend/`, run Python compilation; from `frontend/`, run `npm run build`.
- [ ] For an all-Railway setup, set the service Root Directory to `backend`; the Dockerfile builds and bundles the browser UI. Set public `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` build variables and leave `VITE_API_URL` empty for the same-service API.
- [ ] Alternatively, deploy `frontend/` to Vercel or Cloudflare Pages with `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, and `VITE_API_URL`.
- [ ] Deploy the Dockerfile-backed Python API/renderer to Railway with Root Directory `backend`, attach persistent storage and configure health check `/api/health`.
- [ ] Confirm `https://<api>/api/health` returns success and the admin panel shows the intended owner and PostgreSQL backend.
- [ ] Test one regular account and verify admin APIs deny it; test owner controls in a staging environment.
- [ ] Verify OAuth callbacks, video URLs, notifications and one small test render before enabling users.
- [ ] Verify backups contain the expected database and file state; keep a copy outside Railway.

## After rollout

- [ ] Check Railway logs, database connectivity, disk headroom, provider limits and failed notification outbox entries.
- [ ] Verify owner can access the Admin Control Center; do not publicly expose service-role credentials.
- [ ] Document rollback procedure and restore test evidence.
- [ ] Rebuild the frontend after any `VITE_*` variable change; these are embedded at build time.
- [ ] Reassess provider prices and plan limits regularly; free tiers and terms can change.

Billing and payment setup is intentionally out of scope and deferred. No payment provider, subscription flow, payment records, or revenue dashboard is implemented.
