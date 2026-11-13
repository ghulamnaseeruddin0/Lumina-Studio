"""Thread-safe local SQLite or production PostgreSQL storage."""
import json
import os
import sqlite3
import time
from contextlib import closing

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, topic TEXT, format TEXT, status TEXT, params TEXT,
  created REAL, updated REAL, error TEXT, progress REAL DEFAULT 0, current_agent TEXT, parent TEXT,
  user_id TEXT NOT NULL DEFAULT 'local', workspace_id TEXT, account_email TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, ts REAL, agent TEXT, type TEXT, message TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS ev_job ON events(job_id, id);
CREATE TABLE IF NOT EXISTS steps(
  job_id TEXT, step TEXT, status TEXT, started REAL, finished REAL, summary TEXT, PRIMARY KEY(job_id, step));
CREATE TABLE IF NOT EXISTS assets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, scene_id TEXT, source TEXT, asset_id TEXT, kind TEXT,
  page_url TEXT, author TEXT, author_url TEXT, license TEXT, title TEXT, local_path TEXT);
CREATE TABLE IF NOT EXISTS api_calls(provider TEXT, ts REAL);
CREATE INDEX IF NOT EXISTS api_p ON api_calls(provider, ts);
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, ts REAL, payload TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, topic TEXT, title TEXT, ts REAL, fingerprint TEXT,
  user_id TEXT NOT NULL DEFAULT 'local');
CREATE TABLE IF NOT EXISTS used_assets(source TEXT, asset_id TEXT, job_id TEXT, ts REAL, user_id TEXT NOT NULL DEFAULT 'local');
CREATE INDEX IF NOT EXISTS used_a ON used_assets(source, asset_id);
CREATE TABLE IF NOT EXISTS analytics(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, title TEXT, views REAL, impressions REAL, ctr REAL,
  avg_view_seconds REAL, raw TEXT, user_id TEXT NOT NULL DEFAULT 'local');
CREATE TABLE IF NOT EXISTS llm_usage(day TEXT, provider TEXT, count INTEGER, tokens INTEGER, PRIMARY KEY(day, provider));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, actor TEXT, action TEXT, detail TEXT,
  user_id TEXT NOT NULL DEFAULT 'local');
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_members(
  workspace_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL, created REAL NOT NULL,
  PRIMARY KEY(workspace_id, user_id));
CREATE TABLE IF NOT EXISTS invitations(
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, expires REAL NOT NULL, accepted REAL);
CREATE TABLE IF NOT EXISTS project_versions(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, user_id TEXT NOT NULL, label TEXT NOT NULL,
  payload TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS media_library(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, workspace_id TEXT, filename TEXT NOT NULL, path TEXT NOT NULL,
  media_type TEXT NOT NULL, license TEXT NOT NULL DEFAULT '', attribution TEXT NOT NULL DEFAULT '',
  metadata TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS review_links(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, role TEXT NOT NULL,
  expires REAL NOT NULL, revoked REAL);
CREATE TABLE IF NOT EXISTS review_comments(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, link_id TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS content_calendar(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, workspace_id TEXT, job_id TEXT, title TEXT NOT NULL,
  scheduled REAL, status TEXT NOT NULL DEFAULT 'planned', metadata TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS experiments(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, job_id TEXT NOT NULL, kind TEXT NOT NULL,
  variants TEXT NOT NULL, metrics TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS project_templates(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, params TEXT NOT NULL,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_states(
  state_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, provider TEXT NOT NULL,
  verifier TEXT NOT NULL DEFAULT '', expires REAL NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS platform_connections(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, provider TEXT NOT NULL, account_id TEXT NOT NULL,
  account_name TEXT NOT NULL DEFAULT '', encrypted_tokens TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT '',
  expires REAL, metadata TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL, updated REAL NOT NULL,
  UNIQUE(user_id, provider, account_id));
CREATE TABLE IF NOT EXISTS publish_batches(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, job_id TEXT NOT NULL, status TEXT NOT NULL,
  created REAL NOT NULL, updated REAL NOT NULL, completed REAL, metadata TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS publish_targets(
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, user_id TEXT NOT NULL, provider TEXT NOT NULL,
  connection_id TEXT NOT NULL, status TEXT NOT NULL, remote_id TEXT, remote_url TEXT, encrypted_session TEXT,
  error TEXT, attempts INTEGER NOT NULL DEFAULT 0, progress DOUBLE PRECISION NOT NULL DEFAULT 0,
  created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(batch_id, connection_id));
CREATE TABLE IF NOT EXISTS notification_outbox(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, job_id TEXT NOT NULL, kind TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, recipient TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL,
  provider_id TEXT, error TEXT, created REAL NOT NULL, sent REAL);
CREATE TABLE IF NOT EXISTS publishing_preferences(
  user_id TEXT PRIMARY KEY, notification_email TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS public_media_tokens(
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, job_id TEXT NOT NULL,
  expires REAL NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS super_admin_identity(
  singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1), user_id TEXT NOT NULL UNIQUE, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS admin_user_controls(
  user_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active', role TEXT NOT NULL DEFAULT 'member',
  permissions TEXT NOT NULL DEFAULT '[]', updated_by TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS admin_agent_settings(
  agent_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, settings TEXT NOT NULL DEFAULT '{}',
  updated_by TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS admin_feature_flags(
  flag TEXT PRIMARY KEY, enabled INTEGER NOT NULL, updated_by TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS admin_auth_sessions(
  session_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
  ip_hash TEXT NOT NULL DEFAULT '', user_agent TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS admin_auth_user ON admin_auth_sessions(user_id, last_seen);
CREATE TABLE IF NOT EXISTS migration_imports(source_hash TEXT PRIMARY KEY, source_path TEXT NOT NULL, imported REAL NOT NULL);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, topic TEXT, format TEXT, status TEXT, params TEXT,
  created DOUBLE PRECISION, updated DOUBLE PRECISION, error TEXT, progress DOUBLE PRECISION DEFAULT 0,
  current_agent TEXT, parent TEXT, user_id TEXT NOT NULL DEFAULT 'local', workspace_id TEXT,
  account_email TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS events(
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, job_id TEXT, ts DOUBLE PRECISION,
  agent TEXT, type TEXT, message TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS ev_job ON events(job_id, id);
CREATE TABLE IF NOT EXISTS steps(
  job_id TEXT, step TEXT, status TEXT, started DOUBLE PRECISION, finished DOUBLE PRECISION,
  summary TEXT, PRIMARY KEY(job_id, step));
CREATE TABLE IF NOT EXISTS assets(
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, job_id TEXT, scene_id TEXT, source TEXT,
  asset_id TEXT, kind TEXT, page_url TEXT, author TEXT, author_url TEXT, license TEXT, title TEXT, local_path TEXT);
CREATE TABLE IF NOT EXISTS api_calls(provider TEXT, ts DOUBLE PRECISION);
CREATE INDEX IF NOT EXISTS api_p ON api_calls(provider, ts);
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, ts DOUBLE PRECISION, payload TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS history(
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, job_id TEXT, topic TEXT, title TEXT,
  ts DOUBLE PRECISION, fingerprint TEXT, user_id TEXT NOT NULL DEFAULT 'local');
CREATE TABLE IF NOT EXISTS used_assets(source TEXT, asset_id TEXT, job_id TEXT, ts DOUBLE PRECISION,
  user_id TEXT NOT NULL DEFAULT 'local');
CREATE INDEX IF NOT EXISTS used_a ON used_assets(source, asset_id);
CREATE TABLE IF NOT EXISTS analytics(
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, ts DOUBLE PRECISION, title TEXT, views DOUBLE PRECISION,
  impressions DOUBLE PRECISION, ctr DOUBLE PRECISION, avg_view_seconds DOUBLE PRECISION,
  raw TEXT, user_id TEXT NOT NULL DEFAULT 'local');
CREATE TABLE IF NOT EXISTS llm_usage(day TEXT, provider TEXT, count INTEGER, tokens INTEGER, PRIMARY KEY(day, provider));
CREATE TABLE IF NOT EXISTS audit(
  id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, ts DOUBLE PRECISION, actor TEXT, action TEXT, detail TEXT,
  user_id TEXT NOT NULL DEFAULT 'local');
CREATE TABLE IF NOT EXISTS workspaces(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id TEXT NOT NULL, created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_members(
  workspace_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL,
  created DOUBLE PRECISION NOT NULL, PRIMARY KEY(workspace_id, user_id));
CREATE TABLE IF NOT EXISTS invitations(
  id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE, expires DOUBLE PRECISION NOT NULL, accepted DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS project_versions(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, user_id TEXT NOT NULL, label TEXT NOT NULL,
  payload TEXT NOT NULL, created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS media_library(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, workspace_id TEXT, filename TEXT NOT NULL,
  path TEXT NOT NULL, media_type TEXT NOT NULL, license TEXT NOT NULL DEFAULT '',
  attribution TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}', created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS review_links(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, role TEXT NOT NULL,
  expires DOUBLE PRECISION NOT NULL, revoked DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS review_comments(
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, link_id TEXT NOT NULL, body TEXT NOT NULL,
  created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS content_calendar(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, workspace_id TEXT, job_id TEXT, title TEXT NOT NULL,
  scheduled DOUBLE PRECISION, status TEXT NOT NULL DEFAULT 'planned', metadata TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS experiments(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, job_id TEXT NOT NULL, kind TEXT NOT NULL,
  variants TEXT NOT NULL, metrics TEXT NOT NULL DEFAULT '{}', created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS project_templates(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, params TEXT NOT NULL,
  created DOUBLE PRECISION NOT NULL, updated DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS oauth_states(
  state_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, provider TEXT NOT NULL,
  verifier TEXT NOT NULL DEFAULT '', expires DOUBLE PRECISION NOT NULL, created DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS oauth_state_expiry ON oauth_states(expires);
CREATE TABLE IF NOT EXISTS platform_connections(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, provider TEXT NOT NULL, account_id TEXT NOT NULL,
  account_name TEXT NOT NULL DEFAULT '', encrypted_tokens TEXT NOT NULL, scopes TEXT NOT NULL DEFAULT '',
  expires DOUBLE PRECISION, metadata TEXT NOT NULL DEFAULT '{}', created DOUBLE PRECISION NOT NULL,
  updated DOUBLE PRECISION NOT NULL, UNIQUE(user_id, provider, account_id));
CREATE TABLE IF NOT EXISTS publish_batches(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, job_id TEXT NOT NULL, status TEXT NOT NULL,
  created DOUBLE PRECISION NOT NULL, updated DOUBLE PRECISION NOT NULL, completed DOUBLE PRECISION,
  metadata TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS publish_targets(
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, user_id TEXT NOT NULL, provider TEXT NOT NULL,
  connection_id TEXT NOT NULL, status TEXT NOT NULL, remote_id TEXT, remote_url TEXT, encrypted_session TEXT,
  error TEXT, attempts INTEGER NOT NULL DEFAULT 0, progress DOUBLE PRECISION NOT NULL DEFAULT 0,
  created DOUBLE PRECISION NOT NULL, updated DOUBLE PRECISION NOT NULL, UNIQUE(batch_id, connection_id));
CREATE INDEX IF NOT EXISTS publish_target_batch ON publish_targets(batch_id, created);
CREATE TABLE IF NOT EXISTS notification_outbox(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, job_id TEXT NOT NULL, kind TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE, recipient TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt DOUBLE PRECISION NOT NULL,
  provider_id TEXT, error TEXT, created DOUBLE PRECISION NOT NULL, sent DOUBLE PRECISION);
CREATE INDEX IF NOT EXISTS notification_due ON notification_outbox(status, next_attempt);
CREATE TABLE IF NOT EXISTS publishing_preferences(
  user_id TEXT PRIMARY KEY, notification_email TEXT NOT NULL DEFAULT '', updated DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS public_media_tokens(
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, job_id TEXT NOT NULL,
  expires DOUBLE PRECISION NOT NULL, created DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS public_media_expiry ON public_media_tokens(expires);
CREATE TABLE IF NOT EXISTS super_admin_identity(
  singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1), user_id TEXT NOT NULL UNIQUE,
  created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS admin_user_controls(
  user_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active', role TEXT NOT NULL DEFAULT 'member',
  permissions TEXT NOT NULL DEFAULT '[]', updated_by TEXT NOT NULL, updated DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS admin_agent_settings(
  agent_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, settings TEXT NOT NULL DEFAULT '{}',
  updated_by TEXT NOT NULL, updated DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS admin_feature_flags(
  flag TEXT PRIMARY KEY, enabled INTEGER NOT NULL, updated_by TEXT NOT NULL, updated DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS admin_auth_sessions(
  session_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, first_seen DOUBLE PRECISION NOT NULL,
  last_seen DOUBLE PRECISION NOT NULL, ip_hash TEXT NOT NULL DEFAULT '', user_agent TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS admin_auth_user ON admin_auth_sessions(user_id, last_seen);
CREATE TABLE IF NOT EXISTS migration_imports(
  source_hash TEXT PRIMARY KEY, source_path TEXT NOT NULL, imported DOUBLE PRECISION NOT NULL);
"""


class DB:
    def __init__(self, path):
        self.path = str(path)
        self.url = os.environ.get("LUMINA_DATABASE_URL") or os.environ.get("DATABASE_URL")
        self.is_postgres = bool(self.url)
        if self.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("PostgreSQL mode requires psycopg. Install the project requirements.") from exc
            self._psycopg = psycopg
            self._dict_row = dict_row
            with self._connect() as c:
                for statement in POSTGRES_SCHEMA.split(";"):
                    if statement.strip():
                        c.execute(statement)
                c.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'local'")
                c.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS workspace_id TEXT")
                c.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS account_email TEXT NOT NULL DEFAULT ''")
                c.execute("ALTER TABLE publish_targets ADD COLUMN IF NOT EXISTS remote_url TEXT")
                c.execute("ALTER TABLE analytics ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'local'")
                c.execute("ALTER TABLE history ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'local'")
                c.execute("ALTER TABLE used_assets ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'local'")
                c.execute("ALTER TABLE audit ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'local'")
                c.commit()
            return
        with closing(self._connect()) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            job_columns = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
            if "user_id" not in job_columns:
                c.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'")
            if "workspace_id" not in job_columns:
                c.execute("ALTER TABLE jobs ADD COLUMN workspace_id TEXT")
            if "account_email" not in job_columns:
                c.execute("ALTER TABLE jobs ADD COLUMN account_email TEXT NOT NULL DEFAULT ''")
            publish_target_columns = {r["name"] for r in c.execute("PRAGMA table_info(publish_targets)")}
            if "remote_url" not in publish_target_columns:
                c.execute("ALTER TABLE publish_targets ADD COLUMN remote_url TEXT")
            analytics_columns = {r["name"] for r in c.execute("PRAGMA table_info(analytics)")}
            if "user_id" not in analytics_columns:
                c.execute("ALTER TABLE analytics ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'")
            history_columns = {r["name"] for r in c.execute("PRAGMA table_info(history)")}
            if "user_id" not in history_columns:
                c.execute("ALTER TABLE history ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'")
            used_asset_columns = {r["name"] for r in c.execute("PRAGMA table_info(used_assets)")}
            if "user_id" not in used_asset_columns:
                c.execute("ALTER TABLE used_assets ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'")
            audit_columns = {r["name"] for r in c.execute("PRAGMA table_info(audit)")}
            if "user_id" not in audit_columns:
                c.execute("ALTER TABLE audit ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'")
            c.commit()

    def _connect(self):
        if self.is_postgres:
            return self._psycopg.connect(self.url, row_factory=self._dict_row)
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def q(self, sql, params=()):
        if self.is_postgres:
            with self._connect() as c:
                rows = c.execute(self._postgres_sql(sql), params).fetchall()
                return [dict(r) for r in rows]
        with closing(self._connect()) as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def x(self, sql, params=()):
        if self.is_postgres:
            with self._connect() as c:
                cur = c.execute(self._postgres_sql(sql), params)
                c.commit()
                return cur.rowcount
        with closing(self._connect()) as c:
            cur = c.execute(sql, params)
            c.commit()
            return cur.lastrowid

    @staticmethod
    def _postgres_sql(sql):
        return sql.replace("?", "%s")

    def kv_get(self, key, default=None):
        r = self.one("SELECT value FROM kv WHERE key=?", (key,))
        if not r:
            return default
        try:
            return json.loads(r["value"])
        except ValueError:
            return default

    def kv_set(self, key, value):
        self.x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value)))

    def audit(self, actor, action, detail="", user_id="local"):
        self.x("INSERT INTO audit(ts,actor,action,detail,user_id) VALUES(?,?,?,?,?)",
               (time.time(), actor, action, detail[:500], user_id))
