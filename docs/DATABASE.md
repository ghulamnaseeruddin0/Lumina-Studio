# Database guide

Lumina supports SQLite for local/offline use and PostgreSQL for a hosted, multi-user deployment. If no database URL is configured, the local SQLite database is:

```
data/lumina.db
```

It is created automatically. For Railway, set `LUMINA_DATABASE_URL` (or `DATABASE_URL`) to a PostgreSQL connection URL. The production database schema is initialized by the application; generated video and project files remain on the persistent `/data` volume.

To move an existing SQLite installation, stop the studio, back up `data/`, provision an empty PostgreSQL database, and run:

```powershell
$env:LUMINA_DATABASE_URL = "postgresql://..."
python scripts/migrate_sqlite_to_postgres.py data\lumina.db
```

The importer is repeat-safe for an unchanged source file, refuses a non-empty application database or media destination, and copies the `jobs/` and `library/` files alongside metadata. Keep the SQLite backup and verify storage and row counts before switching over.

## What it stores

| Table | Purpose |
|---|---|
| `jobs` | Every video you created: topic, format, status, progress |
| `events` | The live activity log of every agent (what you see on screen) |
| `steps` | Which steps of each video are finished (this is how Retry resumes without repeating work) |
| `assets` | The licence ledger: every clip and photo used, its author, licence and link |
| `used_assets` | Clips already used in finished videos (so new videos avoid repeats) |
| `history` | Your past videos (brand memory) |
| `analytics` | Rows imported from YouTube Studio CSV exports |
| `cache` | Cached stock searches (24 hours) |
| `api_calls` | Counters that keep you inside each free API limit |
| `llm_usage` | How many AI requests you used today |
| `kv` | Settings such as your channel profile |
| `audit` | Account-scoped security log; export from Settings |
| `workspaces` / `workspace_members` | Personal and team workspace membership |
| `media_library` | Uploaded and reusable media metadata, licence, and attribution |
| `project_versions` | Saved project snapshots |
| `content_calendar` / `experiments` | Planned publishing and tracked variants |
| `project_templates` | Reusable, account-scoped video settings |
| `super_admin_identity` | One singleton owner UUID binding; cannot be assigned through public registration |
| `admin_user_controls` | Account status and constrained non-owner admin role/permissions |
| `admin_agent_settings` / `admin_feature_flags` | Optional-agent enablement and server-enforced feature/maintenance controls |
| `admin_auth_sessions` | Hashed verified-session identifiers and sign-in activity timestamps |
| `platform_connections`, `publish_batches`, `publish_targets`, `notification_outbox` | Encrypted publishing connections, upload state and email delivery |

Admin/session tables are sensitive operational records and must not be edited by
hand while the service is running. See
[security and admin permissions](./SECURITY-AND-ADMIN-PERMISSIONS.md).

Other files live next to it in the `data/` folder:

```
data/jobs/<id>/final.mp4        finished videos and their files
data/jobs/<id>/state.json       the project's saved progress
data/cache/assets/              downloaded stock clips (reused across videos)
data/secrets.json               keys you saved in the app (private; never share)
data/auth.json                  your access token
data/settings.json              output preferences
data/backups/                   automatic backups
data/logs/errors.log            error log
```

## Viewing a local SQLite database in VS Code

1. Install the recommended extension **SQLite Viewer** (`qwtel.sqlite-viewer`). VS Code suggests it when you open the folder.
2. In the Explorer click `data/lumina.db`. You see every table and can browse rows.

Or use SQL in a terminal (needs the free `sqlite3` tool):

```
sqlite3 data/lumina.db "select id, topic, status from jobs order by created desc limit 10;"
sqlite3 data/lumina.db "select source, author, license from assets where job_id='YOUR_JOB_ID';"
```

Please do not edit rows by hand while the studio is running.

## Backups

- **Automatic:** a backup is saved at startup if the last one is older than 24 hours. The newest 7 are kept in `data/backups/`.
- **Manual:** *Settings > Back up my data now*, or run `python run.py backup`.
- A backup is a `.zip` containing a consistent copy of the database and your settings. It does **not** include your keys or videos (keys are private; videos are big).

**Restore:** stop the studio, unzip the backup, and replace `data/lumina.db` with the `lumina.db` from the zip.

## Moving to another computer

Copy the whole `data/` folder (or just `lumina.db` and `settings.json` plus any videos you want). Re-enter your keys on the new computer.

## Starting fresh

Stop the studio and delete `data/lumina.db` (and optionally `data/jobs/`). A new empty database is created on the next start.

## Hosted PostgreSQL

The backend uses parameterized SQL and scopes jobs, history, asset-use, analytics, media metadata, templates, experiments, and audit records by account or workspace. Supabase Auth manages identities; PostgreSQL stores hosted app records. Do not expose a database URL or Supabase service-role key to the browser. Restrict PostgreSQL network access to the Railway service and keep a separate database backup policy.
