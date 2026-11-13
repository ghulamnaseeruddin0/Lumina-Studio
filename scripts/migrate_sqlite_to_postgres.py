"""Import a stopped Lumina SQLite database into an empty PostgreSQL database."""
import hashlib
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TABLES = (
    "jobs", "events", "steps", "assets", "api_calls", "cache", "kv", "history",
    "used_assets", "analytics", "llm_usage", "audit", "workspaces", "workspace_members",
    "invitations", "project_versions", "media_library", "review_links", "review_comments",
    "content_calendar", "experiments", "project_templates", "oauth_states", "platform_connections",
    "publish_batches", "publish_targets", "notification_outbox", "publishing_preferences",
    "public_media_tokens",
    "super_admin_identity", "admin_user_controls", "admin_agent_settings", "admin_feature_flags",
    "admin_auth_sessions",
)
IDENTITY_TABLES = ("events", "assets", "history", "analytics", "audit")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/migrate_sqlite_to_postgres.py <path-to-stopped-lumina.db>")
    source = Path(sys.argv[1]).expanduser().resolve()
    database_url = os.environ.get("LUMINA_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not source.is_file():
        raise SystemExit(f"SQLite database not found: {source}")
    if not database_url:
        raise SystemExit("Set LUMINA_DATABASE_URL to the target PostgreSQL connection URL.")

    from lumina.db import DB

    # Initialize/validate the destination schema before starting the import transaction.
    target_data = Path(os.environ.get("LUMINA_DATA") or ROOT / "data").resolve()
    DB(target_data / "lumina.db")
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise SystemExit("PostgreSQL migration requires psycopg; install requirements.txt first.") from exc

    source_hash = digest(source)
    source_uri = source.as_uri() + "?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as src:
        src.row_factory = sqlite3.Row
        existing_tables = {r["name"] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with psycopg.connect(database_url, row_factory=dict_row) as dst:
            with dst.cursor() as cur:
                imported = cur.execute(
                    "SELECT source_hash FROM migration_imports WHERE source_path=%s", (str(source),)
                ).fetchone()
                if imported:
                    if imported["source_hash"] == source_hash:
                        print(f"Already imported: {source}")
                        return
                    raise SystemExit("This SQLite path was imported before but has changed. Use a fresh target or reconcile changes manually.")
                for table in TABLES:
                    if table not in existing_tables:
                        continue
                    count = cur.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
                    if count:
                        raise SystemExit(f"Target table {table} is not empty. Migrate only into a fresh application database.")

                storage_dirs = ("jobs", "library")
                if "media_library" in existing_tables:
                    media_count = src.execute("SELECT COUNT(*) FROM media_library").fetchone()[0]
                    library = source.parent / "library"
                    if media_count and not library.is_dir():
                        raise SystemExit(f"SQLite has {media_count} media records but {library} is missing.")
                for directory in storage_dirs:
                    old = source.parent / directory
                    new = target_data / directory
                    if not old.is_dir() or old.resolve() == new.resolve():
                        continue
                    if new.exists() and any(new.iterdir()):
                        raise SystemExit(f"Destination {new} is not empty; refusing to overwrite media or job files.")
                for directory in storage_dirs:
                    old = source.parent / directory
                    new = target_data / directory
                    if old.is_dir() and old.resolve() != new.resolve():
                        shutil.copytree(old, new, dirs_exist_ok=True)

                for table in TABLES:
                    if table not in existing_tables:
                        continue
                    source_columns = [r["name"] for r in src.execute(f'PRAGMA table_info("{table}")')]
                    dest_columns = {r["column_name"] for r in cur.execute(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s",
                        (table,),
                    ).fetchall()}
                    columns = [name for name in source_columns if name in dest_columns]
                    if not columns:
                        continue
                    names = ", ".join(f'"{name}"' for name in columns)
                    placeholders = ", ".join(["%s"] * len(columns))
                    sql = f'INSERT INTO "{table}" ({names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
                    source_rows = src.execute(f'SELECT {names} FROM "{table}"')
                    rows = source_rows.fetchmany(500)
                    copied = 0
                    while rows:
                        batch = [list(row) for row in rows]
                        if table == "media_library":
                            path_index = columns.index("path")
                            old_library = (source.parent / "library").resolve()
                            new_library = (target_data / "library").resolve()
                            for values in batch:
                                old_path = Path(values[path_index]).resolve()
                                try:
                                    relative = old_path.relative_to(old_library)
                                except ValueError as exc:
                                    raise SystemExit("Media library contains a path outside the source data folder.") from exc
                                values[path_index] = str(new_library / relative)
                        cur.executemany(sql, [tuple(row) for row in batch])
                        copied += len(rows)
                        rows = source_rows.fetchmany(500)
                    print(f"{table}: imported {copied} row(s)")
                    actual = cur.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
                    if actual != copied:
                        raise RuntimeError(f"Imported row count mismatch for {table}: expected {copied}, found {actual}")

                for table in IDENTITY_TABLES:
                    cur.execute(
                        f"SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
                        f"EXISTS(SELECT 1 FROM {table}))",
                        (table,),
                    )
                cur.execute(
                    "INSERT INTO migration_imports(source_hash, source_path, imported) VALUES(%s, %s, %s)",
                    (source_hash, str(source), time.time()),
                )
            dst.commit()
    print("Import committed. Verify job counts and media files before switching production traffic.")


if __name__ == "__main__":
    main()
