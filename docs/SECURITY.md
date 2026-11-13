# Security

## What is built in

| Risk | Protection |
|---|---|
| Strangers using your studio | Hosted requests use server-verified Supabase JWTs; local-token compatibility uses a long random token and constant-time comparison. API requests are rate-limited; local token attempts have lockout protection |
| Websites calling your studio | CORS allow-list (`server.allowed_origins`); other origins get 403 |
| Keys leaking | Keys are stored only on the server (`data/secrets.json`, owner-only permissions) or in environment variables; never returned by the API; removed from logs, events and generated text |
| Keys leaking from a GitHub upload | `.gitignore` excludes `.env` and `data/` |
| Burning your free quotas | Per-hour job limit, per-API sliding-window limits, search cache |
| Malicious web content ("prompt injection") | Fetched text is stripped of tags and instruction-like lines and labelled as untrusted data in prompts |
| Server-side request forgery | Downloads are only allowed from an allow-list of stock hosts, over public addresses; redirects are validated |
| File access | Downloads are restricted to the job's deliverable files; `..` traversal is blocked; internal folders (state, audio, parts) are not downloadable |
| Odd input | Topics are cleaned and length-limited (no links); all job parameters are validated and capped (90 s / 30 min) |
| Browser attacks | Content-Security-Policy, `nosniff`, no framing; the interface never inserts server text as HTML |
| Data loss | Automatic daily backups; progress saved after every step |
| Vulnerable dependencies | Only two dependencies; GitHub Actions runs `pip-audit`; Dependabot proposes updates |
| Audit trail | The `audit` table records job creation, key and token changes, settings changes |
| Single-owner administration | Supabase-verified identity is matched against the backend-only `LUMINA_SUPER_ADMIN_USER_ID`; there is no public admin registration route |
| Admin authorization | Every `/api/admin/` route checks server-side role permissions; account status is checked on authenticated requests |
| Admin secrets | Supabase Auth Admin service key remains backend-only; the Admin UI reports configuration presence, not secret values |
| Admin audit/session data | Sensitive admin changes are audited; session identifiers and (when salted) IP addresses are stored as hashes |
| Publishing OAuth | Callback state is random, hashed, expiring, single-use, and bound to the initiating user |
| Connected-account credentials | Provider tokens are encrypted with the Railway-only `LUMINA_OAUTH_ENCRYPTION_KEY`; they are not returned to the browser |
| Temporary provider video access | Meta/TikTok receive short-lived HTTPS links scoped to one completed video, not an account or filesystem path |
| Publishing confirmation | Uploads are queued only after an authorized user explicitly confirms selected destinations |
| Email retries | Resend messages use an idempotency key and a persistent outbox; provider error bodies are not exposed |

## What you must still do

- Keep `.env`, `data/` and your token private. Never post screenshots of the terminal link (it contains the token).
- Leave the server on `127.0.0.1` unless you put HTTPS in front of it (tunnel or reverse proxy).
- Rotate the token (*Settings > Create a new token*) if you shared the link.
- Use free API keys created only for this project, so a leak is easy to fix.
- Check licences of music you add.
- Keep `LUMINA_OAUTH_ENCRYPTION_KEY`, OAuth client secrets, Resend credentials, and provider tokens private and stable. Back up the encryption key securely; rotating it requires users to reconnect accounts.
- Register only the exact HTTPS Railway OAuth callbacks. Review each platform's permissions, app-review rules, privacy settings, and account eligibility before enabling users to publish.
- Keep Python, FFmpeg and `pip install -U -r requirements.txt` up to date.
- For a hosted multi-user setup, configure the owner UUID, Supabase service-role
  key, and PostgreSQL only in the backend secret manager. Review
  [security and admin permissions](./SECURITY-AND-ADMIN-PERMISSIONS.md).

## Reporting a problem

If you find a security issue in your own copy, stop the studio, rotate your keys, and fix or report it privately before sharing details.
