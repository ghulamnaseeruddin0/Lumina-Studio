"""The application core: jobs, the worker thread, approvals, backups. The web server is a thin layer on top."""
import copy
import base64
import html
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import zipfile
from pathlib import Path

from . import security
from .agents import agent_catalog, build_registry
from .agents.common import word_count
from .agents.director import Paused, StepFailed
from .agents.planning import clean_narration, import_analytics_csv, get_profile, DEFAULT_PROFILE
from .agents.visuals import swap_asset
from .admin import AdminService
from .config import Config
from .db import DB
from .job import Bus, Cancelled, Job, TERMINAL
from .llm import LLM
from .publishing import PROVIDERS, PublishingService
from .quota import Quota
from .sources import Stock, StockError, http_json
from .timeline import build_timeline
from .tts import Narration


class App:
    def __init__(self, cfg=None, start_worker=True):
        self.cfg = cfg or Config()
        security.register_secrets(self.cfg.all_secret_values())
        self.auth = security.Auth(self.cfg.data)
        if os.environ.get("REQUIRE_POSTGRES", "").lower() in ("1", "true", "yes") and not (
                os.environ.get("LUMINA_DATABASE_URL") or os.environ.get("DATABASE_URL")):
            raise RuntimeError("REQUIRE_POSTGRES is enabled but no PostgreSQL connection URL is configured.")
        self.db = DB(self.cfg.db_path)
        self.publishing = PublishingService(self.db, self.cfg.data)
        if os.environ.get("REQUIRE_SUPABASE_AUTH", "").lower() in ("1", "true", "yes") and not self.auth_is_configured():
            raise RuntimeError("REQUIRE_SUPABASE_AUTH is enabled but Supabase authentication is not configured.")
        self.bus = Bus()
        self.llm = LLM(self.cfg, self.db)
        self.quota = Quota(self.db, self.cfg)
        self.stock = Stock(self.cfg, self.db, self.quota)
        self.narration = Narration(self.cfg)
        self.registry = build_registry()
        self.director = self.registry["director"]
        self.admin = AdminService(self)
        self.limiter = security.RateLimiter()
        self.queue = queue.Queue()
        self.notification_queue = queue.Queue()
        self.publish_queue = queue.Queue()
        self.active = {}
        self._notification_thread = None
        self._publishing_thread = None
        self._notification_lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        if start_worker:
            self.start()

    @staticmethod
    def auth_is_configured():
        return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_ANON_KEY"))

    # ------------------------------------------------------------- lifecycle
    def start(self):
        interrupted_batches = self.db.q("SELECT DISTINCT batch_id FROM publish_targets WHERE status='uploading'")
        if interrupted_batches:
            now = time.time()
            self.db.x("UPDATE publish_targets SET status='failed',error=?,progress=0,updated=? WHERE status='uploading'",
                      ("The service restarted during this upload. Verify the platform before retrying.", now))
            for batch in interrupted_batches:
                pending = self.db.one("SELECT COUNT(*) AS n FROM publish_targets WHERE batch_id=? AND status='queued'",
                                      (batch["batch_id"],))["n"]
                status = "queued" if pending else "completed"
                self.db.x("UPDATE publish_batches SET status=?,updated=?,completed=? WHERE id=?",
                          (status, now, None if pending else now, batch["batch_id"]))
                if not pending:
                    self._queue_publish_summary(batch["batch_id"])
        for r in self.db.q("SELECT id FROM jobs WHERE status IN ('queued','running')"):
            self.db.x("UPDATE jobs SET status='queued' WHERE id=?", (r["id"],))
            self.queue.put(r["id"])
        self._thread = threading.Thread(target=self._loop, daemon=True, name="lumina-worker")
        self._thread.start()
        self._notification_thread = threading.Thread(target=self._notification_loop, daemon=True,
                                                     name="lumina-notifications")
        self._notification_thread.start()
        self._publishing_thread = threading.Thread(target=self._publishing_loop, daemon=True,
                                                   name="lumina-publisher")
        self._publishing_thread.start()
        for row in self.db.q("SELECT id,next_attempt FROM notification_outbox WHERE status IN ('queued','retry')"):
            delay = max(0, row["next_attempt"] - time.time())
            if delay:
                timer = threading.Timer(delay, self.notification_queue.put, (row["id"],))
                timer.daemon = True
                timer.start()
            else:
                self.notification_queue.put(row["id"])
        for row in self.db.q("SELECT DISTINCT batch_id FROM publish_targets WHERE status IN ('queued','uploading')"):
            self.publish_queue.put(row["batch_id"])
        self.auto_backup()

    def stop(self):
        self._stop.set()
        self.queue.put(None)
        self.notification_queue.put(None)
        self.publish_queue.put(None)

    def _loop(self):
        while not self._stop.is_set():
            jid = self.queue.get()
            if jid is None:
                break
            try:
                self._run(jid)
            except Exception:
                self._log_error(jid)

    def _notification_loop(self):
        while not self._stop.is_set():
            item_id = self.notification_queue.get()
            if item_id is None:
                break
            try:
                self._deliver_notification(item_id)
            except Exception:
                self._log_error("notification")

    def _publishing_loop(self):
        while not self._stop.is_set():
            batch_id = self.publish_queue.get()
            if batch_id is None:
                break
            try:
                self._run_publish_batch(batch_id)
            except Exception:
                self._log_error("publishing")
                now = time.time()
                try:
                    self.db.x("UPDATE publish_targets SET status='failed',error=?,progress=0,updated=? "
                              "WHERE batch_id=? AND status IN ('queued','uploading')",
                              ("An internal publishing error stopped this batch. Check logs and verify remote posts before retrying.",
                               now, batch_id))
                    self.db.x("UPDATE publish_batches SET status='completed',updated=?,completed=? WHERE id=?",
                              (now, now, batch_id))
                    self._queue_publish_summary(batch_id)
                except Exception:
                    self._log_error("publishing-recovery")

    def _log_error(self, jid):
        try:
            d = self.cfg.data / "logs"
            d.mkdir(exist_ok=True)
            with open(d / "errors.log", "a", encoding="utf-8") as fh:
                fh.write(f"\n[{time.strftime('%F %T')}] job {jid}\n" + security.redact(traceback.format_exc()))
        except OSError:
            pass

    def _run(self, jid):
        row = self.db.one("SELECT status FROM jobs WHERE id=?", (jid,))
        if not row or row["status"] in TERMINAL:
            return
        job = Job(jid, self)
        self.active[jid] = job
        try:
            self.director.run(job)
        except Cancelled:
            job.emit("job_cancelled", "Job cancelled.")
            job.set_status("cancelled")
        except Paused:
            pass
        except StepFailed as e:
            job.emit("job_failed", security.redact(str(e)))
            job.set_status("failed", error=security.redact(str(e)))
        except Exception as e:
            self._log_error(jid)
            msg = security.redact(f"{type(e).__name__}: {e}")
            job.emit("job_failed", msg)
            job.set_status("failed", error=msg)
        finally:
            self.active.pop(jid, None)
            self.bus.notify()
            status = self.db.one("SELECT status FROM jobs WHERE id=?", (jid,))
            if status and status["status"] in ("done", "needs_attention"):
                self._queue_ready_notification(jid)

    # ------------------------------------------------------------------ jobs
    def new_id(self):
        return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)

    def create_job(self, params, parent=None, state=None, user_id="local", workspace_id=None, account_email=""):
        jid = self.new_id()
        now = time.time()
        self.db.x("INSERT INTO jobs(id,topic,format,status,params,created,updated,parent,user_id,workspace_id,account_email) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (jid, params["topic"], params["format"], "queued", json.dumps(params), now, now, parent,
                   user_id, workspace_id, account_email))
        (self.cfg.jobs_dir / jid).mkdir(parents=True, exist_ok=True)
        if state is not None:
            (self.cfg.jobs_dir / jid / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.db.audit(user_id, "create_job", f"{jid} {params['topic'][:80]}", user_id)
        self.queue.put(jid)
        self.bus.notify()
        return jid

    def submit(self, raw, user_id="local", workspace_id=None, account_email=""):
        params = security.validate_job_params(raw, self.cfg.get("limits"))
        return self.create_job(params, user_id=user_id, workspace_id=workspace_id, account_email=account_email)

    def get_job(self, jid, user_id="local"):
        row = self.db.one(
            "SELECT j.* FROM jobs j WHERE j.id=? AND (j.user_id=? OR j.workspace_id IN "
            "(SELECT workspace_id FROM workspace_members WHERE user_id=? AND role IN ('owner','editor')) "
            "OR (j.workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id=? AND role='reviewer') "
            "AND j.status IN ('done','needs_attention')))", (jid, user_id, user_id, user_id))
        if not row:
            return None
        row["params"] = json.loads(row["params"] or "{}")
        try:
            state = json.loads((self.cfg.jobs_dir / jid / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        row["steps"] = self.db.q("SELECT step, status, summary FROM steps WHERE job_id=?", (jid,))
        row["result"] = {k: state.get(k) for k in ("publish", "seo", "qa", "deliverables", "compliance", "timeline") if k in state}
        if state.get("timeline"):
            row["result"]["timeline"] = {"total": state["timeline"]["total"]}
        return row

    def can_edit_job(self, jid, user_id):
        row = self.db.one("SELECT user_id,workspace_id FROM jobs WHERE id=?", (jid,))
        if not row:
            return False
        if row["user_id"] == user_id:
            return True
        if not row.get("workspace_id"):
            return False
        return bool(self.db.one(
            "SELECT 1 x FROM workspace_members WHERE workspace_id=? AND user_id=? AND role IN ('owner','editor')",
            (row["workspace_id"], user_id),
        ))

    def save_version(self, jid, user_id="local", label="Checkpoint"):
        row = self.db.one("SELECT status FROM jobs WHERE id=?", (jid,))
        if not row or not self.can_edit_job(jid, user_id):
            raise ValueError("No such job.")
        if row["status"] in ("queued", "running", "awaiting_review"):
            raise ValueError("Save a version after this job reaches a stable state.")
        try:
            payload = json.loads((self.cfg.jobs_dir / jid / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError("The project state is not available to snapshot.") from e
        if not isinstance(payload, dict):
            raise ValueError("The project state is invalid.")
        label = security.CONTROL_RE.sub("", str(label or "Checkpoint")).strip()[:80] or "Checkpoint"
        version_id = secrets.token_urlsafe(18)
        self.db.x("INSERT INTO project_versions(id,job_id,user_id,label,payload,created) VALUES(?,?,?,?,?,?)",
                  (version_id, jid, user_id, label, json.dumps(payload, ensure_ascii=False), time.time()))
        self.db.audit(user_id, "save_project_version", f"{jid} {label}", user_id)
        return {"id": version_id, "job_id": jid, "label": label}

    def list_versions(self, jid, user_id="local"):
        if not self.can_edit_job(jid, user_id):
            raise ValueError("No such job.")
        return self.db.q("SELECT id, label, created FROM project_versions WHERE job_id=? ORDER BY created DESC", (jid,))

    def restore_version(self, version_id, user_id="local"):
        row = self.db.one(
            "SELECT v.job_id, v.payload, j.status FROM project_versions v "
            "JOIN jobs j ON j.id=v.job_id WHERE v.id=?",
            (version_id,),
        )
        if not row or not self.can_edit_job(row["job_id"], user_id):
            raise ValueError("No such project version.")
        if row["status"] in ("queued", "running", "awaiting_review"):
            raise ValueError("Stop the active job before restoring a project version.")
        try:
            state = json.loads(row["payload"])
        except (TypeError, ValueError) as e:
            raise ValueError("The saved project version is invalid.") from e
        if not isinstance(state, dict):
            raise ValueError("The saved project version is invalid.")
        directory = self.cfg.jobs_dir / row["job_id"]
        tmp = directory / "state.json.tmp"
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(directory / "state.json")
        self.db.audit(user_id, "restore_project_version", row["job_id"], user_id)
        return {"job_id": row["job_id"]}

    def create_workspace(self, user_id, name):
        name = security.CONTROL_RE.sub("", str(name or "")).strip()[:100]
        if not name:
            raise ValueError("Workspace name is required.")
        workspace_id = secrets.token_urlsafe(16)
        now = time.time()
        self.db.x("INSERT INTO workspaces(id,name,owner_id,created) VALUES(?,?,?,?)", (workspace_id, name, user_id, now))
        self.db.x("INSERT INTO workspace_members(workspace_id,user_id,role,created) VALUES(?,?,?,?)",
                  (workspace_id, user_id, "owner", now))
        self.db.audit(user_id, "create_workspace", workspace_id, user_id)
        return {"id": workspace_id, "name": name, "role": "owner"}

    def claim_local_jobs(self, user_id, job_ids):
        if not isinstance(job_ids, list) or not job_ids or len(job_ids) > 100:
            raise ValueError("Choose between one and 100 local jobs to import.")
        claimed = []
        for jid in dict.fromkeys(str(value) for value in job_ids):
            exists = self.db.one("SELECT 1 x FROM jobs WHERE id=? AND user_id='local'", (jid,))
            if not exists:
                raise ValueError("One or more selected jobs are unavailable for import.")
            self.db.x("UPDATE jobs SET user_id=? WHERE id=? AND user_id='local'", (user_id, jid))
            self.db.x("UPDATE history SET user_id=? WHERE job_id=? AND user_id='local'", (user_id, jid))
            self.db.x("UPDATE used_assets SET user_id=? WHERE job_id=? AND user_id='local'", (user_id, jid))
            claimed.append(jid)
        self.db.audit(user_id, "claim_local_jobs", ",".join(claimed), user_id)
        return {"claimed": claimed}

    def list_workspaces(self, user_id):
        return self.db.q(
            "SELECT w.id,w.name,w.owner_id,m.role FROM workspaces w "
            "JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=? ORDER BY w.created",
            (user_id,),
        )

    def workspace_roster(self, workspace_id, user_id):
        member = self.db.one("SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?",
                             (workspace_id, user_id))
        if not member:
            raise ValueError("Workspace not found.")
        members = self.db.q("SELECT user_id,role,created FROM workspace_members WHERE workspace_id=? ORDER BY created",
                            (workspace_id,))
        invitations = []
        if member["role"] == "owner":
            invitations = self.db.q("SELECT id,email,role,expires FROM invitations "
                                   "WHERE workspace_id=? AND accepted IS NULL ORDER BY expires", (workspace_id,))
        return {"members": members, "invitations": invitations, "can_manage": member["role"] == "owner"}

    def remove_workspace_member(self, workspace_id, actor_id, member_id):
        actor = self.db.one("SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?",
                            (workspace_id, actor_id))
        target = self.db.one("SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?",
                             (workspace_id, member_id))
        if not actor or actor["role"] != "owner":
            raise ValueError("Only a workspace owner can manage members.")
        if not target or target["role"] == "owner":
            raise ValueError("Workspace owner cannot be removed.")
        self.db.x("DELETE FROM workspace_members WHERE workspace_id=? AND user_id=?", (workspace_id, member_id))
        self.db.audit(actor_id, "remove_workspace_member", f"{workspace_id} {member_id}", actor_id)
        return {"removed": True}

    def revoke_workspace_invitation(self, invitation_id, actor_id):
        row = self.db.one("SELECT i.workspace_id,i.email FROM invitations i JOIN workspace_members m "
                          "ON m.workspace_id=i.workspace_id WHERE i.id=? AND m.user_id=? AND m.role='owner' "
                          "AND i.accepted IS NULL", (invitation_id, actor_id))
        if not row:
            raise ValueError("Pending invitation not found.")
        self.db.x("DELETE FROM invitations WHERE id=? AND accepted IS NULL", (invitation_id,))
        self.db.audit(actor_id, "revoke_workspace_invitation", f"{row['workspace_id']} {row['email']}", actor_id)
        return {"revoked": True}

    def invite_workspace_member(self, workspace_id, actor_id, email, role="editor"):
        if role not in ("editor", "reviewer"):
            raise ValueError("Role must be editor or reviewer.")
        workspace = self.db.one("SELECT owner_id FROM workspaces WHERE id=?", (workspace_id,))
        member = self.db.one("SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?", (workspace_id, actor_id))
        if not workspace or (workspace["owner_id"] != actor_id and (not member or member["role"] != "owner")):
            raise ValueError("Only a workspace owner can invite members.")
        email = str(email or "").strip().lower()[:254]
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a valid email address.")
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        invitation_id = secrets.token_urlsafe(16)
        self.db.x(
            "INSERT INTO invitations(id,workspace_id,email,role,token_hash,expires) VALUES(?,?,?,?,?,?)",
            (invitation_id, workspace_id, email, role, token_hash, time.time() + 7 * 86400),
        )
        self.db.audit(actor_id, "invite_workspace_member", f"{workspace_id} {email} {role}", actor_id)
        return {"id": invitation_id, "email": email, "role": role, "token": token,
                "expires_in": 7 * 86400}

    def accept_workspace_invitation(self, token, user_id, email):
        token_hash = hashlib.sha256(str(token or "").encode()).hexdigest()
        invitation = self.db.one("SELECT * FROM invitations WHERE token_hash=?", (token_hash,))
        if not invitation or invitation["accepted"] is not None or invitation["expires"] < time.time():
            raise ValueError("Invitation is invalid, expired, or already used.")
        if invitation["email"].casefold() != str(email or "").casefold():
            raise ValueError("Sign in with the email address that received this invitation.")
        now = time.time()
        self.db.x("INSERT INTO workspace_members(workspace_id,user_id,role,created) VALUES(?,?,?,?) "
                  "ON CONFLICT(workspace_id,user_id) DO NOTHING",
                  (invitation["workspace_id"], user_id, invitation["role"], now))
        self.db.x("UPDATE invitations SET accepted=? WHERE id=?", (now, invitation["id"]))
        self.db.audit(user_id, "accept_workspace_invitation", invitation["workspace_id"], user_id)
        return {"workspace_id": invitation["workspace_id"], "role": invitation["role"]}

    def list_calendar(self, user_id):
        return self.db.q("SELECT id,job_id,title,scheduled,status,metadata FROM content_calendar "
                         "WHERE user_id=? OR workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id=?) "
                         "ORDER BY scheduled IS NULL,scheduled DESC LIMIT 200", (user_id, user_id))

    def update_calendar(self, user_id, item_id, title, scheduled):
        row = self.db.one("SELECT id,job_id,workspace_id,user_id FROM content_calendar WHERE id=?", (item_id,))
        can_edit = bool(row and row["user_id"] == user_id)
        if row and row.get("job_id"):
            can_edit = can_edit or self.can_edit_job(row["job_id"], user_id)
        elif row and row.get("workspace_id"):
            can_edit = can_edit or bool(self.db.one(
                "SELECT 1 x FROM workspace_members WHERE workspace_id=? AND user_id=? AND role IN ('owner','editor')",
                (row["workspace_id"], user_id),
            ))
        if not can_edit:
            raise ValueError("No such calendar item.")
        title = security.CONTROL_RE.sub("", str(title or "")).strip()[:160]
        if not title:
            raise ValueError("Calendar title is required.")
        try:
            scheduled = float(scheduled) if scheduled is not None else None
        except (ValueError, TypeError) as e:
            raise ValueError("Scheduled time must be a Unix timestamp or empty.") from e
        self.db.x("UPDATE content_calendar SET title=?,scheduled=?,status=? WHERE id=?",
                  (title, scheduled, "scheduled" if scheduled is not None else "draft", item_id))
        self.db.audit(user_id, "update_calendar_item", item_id, user_id)
        return {"id": item_id, "title": title, "scheduled": scheduled}

    def list_experiments(self, user_id):
        return self.db.q("SELECT id,job_id,kind,variants,metrics,created FROM experiments "
                         "WHERE user_id=? ORDER BY created DESC LIMIT 100", (user_id,))

    def update_experiment(self, experiment_id, user_id, metrics):
        row = self.db.one("SELECT metrics FROM experiments WHERE id=? AND user_id=?", (experiment_id, user_id))
        if not row:
            raise ValueError("Experiment not found.")
        if not isinstance(metrics, dict) or set(metrics) - {"views", "impressions", "clicks", "ctr"}:
            raise ValueError("Use only views, impressions, clicks, and ctr metrics.")
        try:
            cleaned = {key: float(value) for key, value in metrics.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError("Experiment metrics must be numeric.") from exc
        if any(not (0 <= value <= 1_000_000_000_000) for value in cleaned.values()):
            raise ValueError("Experiment metrics must be non-negative and within range.")
        if "ctr" in cleaned and cleaned["ctr"] > 100:
            raise ValueError("Click-through rate must be a percentage from 0 to 100.")
        try:
            current = json.loads(row["metrics"] or "{}")
        except (TypeError, ValueError):
            current = {}
        if not isinstance(current, dict):
            current = {}
        current.update(cleaned)
        self.db.x("UPDATE experiments SET metrics=? WHERE id=? AND user_id=?",
                  (json.dumps(current), experiment_id, user_id))
        self.db.audit(user_id, "update_experiment_metrics", experiment_id, user_id)
        return {"id": experiment_id, "metrics": current}

    def analytics_summary(self, user_id="local"):
        row = self.db.one("SELECT COUNT(*) AS videos,COALESCE(SUM(views),0) AS views,"
                          "COALESCE(AVG(ctr),0) AS average_ctr,"
                          "COALESCE(AVG(avg_view_seconds),0) AS average_view_seconds "
                          "FROM analytics WHERE user_id=?", (user_id,))
        return {key: round(float(value), 2) if key != "videos" else int(value)
                for key, value in row.items()}

    def preflight(self, raw):
        params = security.validate_job_params(raw, self.cfg.get("limits"))
        from . import media
        disk = shutil.disk_usage(self.cfg.data)
        ai_ready = self.llm.available()
        ffmpeg_ready = bool(media.ffmpeg_version())
        stock_ready = self.stock.has_main_source()
        checks = [
            {"name": "Job settings", "status": "ready", "detail": f"{params['format']} · "
             f"{params.get('seconds', params.get('minutes'))}"},
            {"name": "AI provider", "status": "ready" if ai_ready else "blocked",
             "detail": "A configured AI provider is available." if ai_ready else "Add an AI provider key in Settings."},
            {"name": "FFmpeg", "status": "ready" if ffmpeg_ready else "blocked",
             "detail": "FFmpeg is available." if ffmpeg_ready else "Install FFmpeg and restart the studio."},
            {"name": "Footage sources", "status": "ready" if stock_ready else "warning",
             "detail": "Stock footage API configured." if stock_ready else "Only Wikimedia image search is available; add Pexels or Pixabay for more footage."},
            {"name": "Free disk space", "status": "ready" if disk.free >= 512 * 1024 * 1024 else "blocked",
             "detail": f"{disk.free / (1024 ** 3):.1f} GB available."},
        ]
        return {"ready": all(item["status"] != "blocked" for item in checks), "checks": checks}

    def storage_summary(self, user_id="local"):
        jobs = self.db.q("SELECT id FROM jobs WHERE user_id=? OR workspace_id IN "
                         "(SELECT workspace_id FROM workspace_members WHERE user_id=?)", (user_id, user_id))
        library_rows = self.db.q("SELECT path FROM media_library WHERE user_id=? OR workspace_id IN "
                                 "(SELECT workspace_id FROM workspace_members WHERE user_id=?)", (user_id, user_id))
        jobs_root = self.cfg.jobs_dir.resolve()
        library_root = (self.cfg.data / "library").resolve()

        def file_size(path):
            try:
                resolved = path.resolve()
                if resolved in (jobs_root, library_root) or path.is_symlink():
                    return 0
                if not resolved.is_file():
                    return 0
                if jobs_root not in resolved.parents and library_root not in resolved.parents:
                    return 0
                return resolved.stat().st_size
            except (OSError, RuntimeError):
                return 0

        job_bytes = 0
        for row in jobs:
            directory = (self.cfg.jobs_dir / row["id"]).resolve()
            if directory.parent != jobs_root or not directory.is_dir():
                continue
            for path in directory.rglob("*"):
                job_bytes += file_size(path)
        media_bytes = sum(file_size(Path(row["path"])) for row in library_rows)
        usage = shutil.disk_usage(self.cfg.data)
        return {"jobs": len(jobs), "job_bytes": job_bytes, "media_items": len(library_rows),
                "media_bytes": media_bytes, "free_bytes": usage.free}

    def _enqueue_notification(self, user_id, job_id, kind, recipient, payload, idempotency_key):
        if not recipient:
            return None
        outbox_id = hashlib.sha256(idempotency_key.encode()).hexdigest()
        configured = bool(os.environ.get("RESEND_API_KEY") and os.environ.get("RESEND_FROM_EMAIL"))
        status = "queued" if configured else "blocked"
        now = time.time()
        self.db.x("INSERT INTO notification_outbox(id,user_id,job_id,kind,idempotency_key,recipient,payload,status,"
                  "next_attempt,created,error) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING",
                  (outbox_id, user_id, job_id, kind, idempotency_key, recipient, json.dumps(payload),
                   status, now, now, "" if configured else "Resend is not configured on the server."))
        row = self.db.one("SELECT status FROM notification_outbox WHERE id=?", (outbox_id,))
        if row and row["status"] in ("queued", "retry"):
            self.notification_queue.put(outbox_id)
        return outbox_id

    def _queue_ready_notification(self, job_id):
        row = self.db.one("SELECT id,user_id,topic,format,status,account_email FROM jobs WHERE id=?", (job_id,))
        if not row:
            return
        recipient = self.publishing.notification_email(row["user_id"], row.get("account_email", ""))
        if not recipient:
            return
        frontend = self.publishing.frontend_url
        link = f"{frontend}/?job={urllib.parse.quote(job_id)}" if frontend else ""
        state = {}
        try:
            state = json.loads((self.cfg.jobs_dir / job_id / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        files = state.get("deliverables", [])
        payload = {"topic": row["topic"], "format": row["format"], "status": row["status"],
                   "files": files, "link": link}
        self._enqueue_notification(row["user_id"], job_id, "ready_for_review", recipient, payload,
                                   f"job:{job_id}:ready-for-review")

    def _deliver_notification(self, outbox_id):
        with self._notification_lock:
            row = self.db.one("SELECT * FROM notification_outbox WHERE id=?", (outbox_id,))
            if not row or row["status"] == "sent" or row["status"] not in ("queued", "retry", "blocked"):
                return
            configured = bool(os.environ.get("RESEND_API_KEY") and os.environ.get("RESEND_FROM_EMAIL"))
            if not configured:
                self.db.x("UPDATE notification_outbox SET status='blocked',error=? WHERE id=?",
                          ("Resend is not configured on the server.", outbox_id))
                return
            if row["next_attempt"] > time.time():
                return
            try:
                payload = json.loads(row["payload"])
                if row["kind"] == "ready_for_review":
                    subject = f"Lumina video ready for review: {payload['topic']}"
                    files = "".join(f"<li>{html.escape(str(name))}</li>" for name in payload.get("files", []))
                    link = (f'<p><a href="{html.escape(payload["link"], quote=True)}">Open your Lumina project and review the complete package</a></p>'
                            if payload.get("link") else "<p>Sign in to Lumina and open this video from Videos.</p>")
                    body = (f"<h1>Your video package is ready</h1><p><strong>{html.escape(payload['topic'])}</strong>"
                            f" ({html.escape(payload['format'])}; {html.escape(payload['status'])})</p>{link}"
                            f"<p>Available materials:</p><ul>{files}</ul>"
                            "<p>Review the video and publishing details in Lumina before choosing Publish.</p>")
                else:
                    subject = f"Lumina publishing results: {payload.get('topic', 'video')}"
                    rows = "".join(
                        f"<li><strong>{html.escape(str(item.get('provider', 'Platform')).title())}</strong>: "
                        f"{html.escape(str(item.get('status', 'unknown')))}"
                        f"{' — ' + html.escape(str(item.get('error', ''))) if item.get('error') else ''}"
                        f"{' · ' + html.escape(str(item.get('remote_url', ''))) if item.get('remote_url') else ''}</li>"
                        for item in payload.get("targets", []))
                    link = (f'<p><a href="{html.escape(payload["link"], quote=True)}">Open publishing results in Lumina</a></p>'
                            if payload.get("link") else "")
                    body = f"<h1>Publishing attempt finished</h1><p>{html.escape(payload.get('topic', ''))}</p><ul>{rows}</ul>{link}"
                provider_id = self.publishing.send_resend(row["recipient"], subject, body, row["idempotency_key"])
                self.db.x("UPDATE notification_outbox SET status='sent',sent=?,provider_id=?,error=NULL WHERE id=?",
                          (time.time(), provider_id, outbox_id))
            except Exception as exc:
                attempts = int(row["attempts"]) + 1
                status = "failed" if attempts >= 8 else "retry"
                next_attempt = time.time() + min(3600, 30 * (2 ** min(attempts - 1, 7)))
                message = security.redact(str(exc))[:500]
                self.db.x("UPDATE notification_outbox SET status=?,attempts=?,next_attempt=?,error=? WHERE id=?",
                          (status, attempts, next_attempt, message, outbox_id))
                if status == "retry":
                    timer = threading.Timer(min(3600, 30 * (2 ** min(attempts - 1, 7))),
                                            self.notification_queue.put, (outbox_id,))
                    timer.daemon = True
                    timer.start()

    def publishing_notifications(self, user_id):
        return self.db.q("SELECT id,job_id,kind,status,attempts,next_attempt,error,created,sent FROM notification_outbox "
                         "WHERE user_id=? ORDER BY created DESC LIMIT 100", (user_id,))

    def retry_notification(self, user_id, outbox_id):
        row = self.db.one("SELECT status FROM notification_outbox WHERE id=? AND user_id=?", (outbox_id, user_id))
        if not row or row["status"] == "sent":
            raise ValueError("Retryable notification not found.")
        if not os.environ.get("RESEND_API_KEY") or not os.environ.get("RESEND_FROM_EMAIL"):
            raise ValueError("Configure Resend on the server before retrying email.")
        self.db.x("UPDATE notification_outbox SET status='queued',next_attempt=?,error=NULL WHERE id=? AND user_id=?",
                  (time.time(), outbox_id, user_id))
        self.notification_queue.put(outbox_id)
        return {"queued": True}

    def public_media_url(self, job_id, user_id, lifetime=7200):
        if not self.publishing.public_url or not self.publishing.public_url.startswith("https://"):
            raise ValueError("Set LUMINA_PUBLIC_URL to an HTTPS Railway URL before uploading to Meta platforms.")
        if not self.get_job(job_id, user_id) or not self.can_edit_job(job_id, user_id):
            raise ValueError("No such owned video.")
        path = self.deliverable_path(job_id, "final.mp4")
        if not path.is_file():
            raise ValueError("The completed video is unavailable.")
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.db.x("DELETE FROM public_media_tokens WHERE expires<?", (now,))
        self.db.x("INSERT INTO public_media_tokens(token_hash,user_id,job_id,expires,created) VALUES(?,?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), user_id, job_id, now + min(lifetime, 7200), now))
        return f"{self.publishing.public_url}/api/publishing/media/{urllib.parse.quote(job_id)}?ticket={urllib.parse.quote(token)}"

    def check_public_media_ticket(self, ticket, job_id):
        hashed = hashlib.sha256(str(ticket or "").encode()).hexdigest()
        row = self.db.one("SELECT job_id,expires FROM public_media_tokens WHERE token_hash=?", (hashed,))
        if not row or row["job_id"] != job_id or row["expires"] < time.time():
            return None
        try:
            return self.deliverable_path(job_id, "final.mp4")
        except (ValueError, PermissionError):
            return None

    def create_publish_batch(self, job_id, user_id, targets, metadata, account_email=""):
        job = self.get_job(job_id, user_id)
        if not job or not self.can_edit_job(job_id, user_id):
            raise ValueError("You do not have permission to publish this video.")
        if job["status"] not in ("done", "needs_attention"):
            raise ValueError("Wait until video production is complete before publishing.")
        video_path = self.deliverable_path(job_id, "final.mp4")
        if not video_path.is_file():
            raise ValueError("The completed video file is missing.")
        if not isinstance(targets, list) or not targets or len(targets) > 12:
            raise ValueError("Select one or more connected platform accounts.")
        if not isinstance(metadata, dict):
            raise ValueError("Publishing metadata must be an object.")
        title = security.CONTROL_RE.sub(" ", str(metadata.get("title") or job["params"]["topic"])).strip()[:100]
        description = security.CONTROL_RE.sub(" ", str(metadata.get("description") or ""))[:5000]
        privacy = str(metadata.get("privacy") or "public")
        if privacy not in ("public", "unlisted", "private"):
            raise ValueError("Privacy must be public, unlisted, or private.")
        connected = {row["id"]: row for row in self.db.q(
            "SELECT id,provider FROM platform_connections WHERE user_id=?", (user_id,))}
        selected = list(dict.fromkeys(str(target) for target in targets))
        if any(target not in connected for target in selected):
            raise ValueError("One or more selected publishing accounts are unavailable.")
        for target in selected:
            provider = connected[target]["provider"]
            if provider not in PROVIDERS:
                raise ValueError("A selected account uses an unsupported publishing provider.")
            if provider in ("instagram", "facebook"):
                if not self.publishing.public_url.startswith("https://"):
                    raise ValueError("Set LUMINA_PUBLIC_URL to an HTTPS Railway URL before uploading to Meta platforms.")
        batch_id = secrets.token_urlsafe(18)
        now = time.time()
        payload = {"title": title, "description": description, "privacy": privacy,
                   "format": job["format"]}
        self.db.x("INSERT INTO publish_batches(id,user_id,job_id,status,created,updated,metadata) VALUES(?,?,?,?,?,?,?)",
                  (batch_id, user_id, job_id, "queued", now, now, json.dumps(payload)))
        for target in selected:
            self.db.x("INSERT INTO publish_targets(id,batch_id,user_id,provider,connection_id,status,created,updated) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      (secrets.token_urlsafe(18), batch_id, user_id, connected[target]["provider"],
                       target, "queued", now, now))
        self.db.audit(user_id, "confirm_multi_platform_publish", f"{job_id} {','.join(selected)}", user_id)
        self.publish_queue.put(batch_id)
        return {"id": batch_id, "status": "queued"}

    def list_publish_batches(self, job_id, user_id):
        if not self.get_job(job_id, user_id):
            raise ValueError("No such job.")
        batches = self.db.q("SELECT id,status,created,updated,completed,metadata FROM publish_batches "
                            "WHERE job_id=? AND user_id=? ORDER BY created DESC LIMIT 20", (job_id, user_id))
        for batch in batches:
            batch["metadata"] = json.loads(batch["metadata"] or "{}")
            batch["targets"] = self.db.q(
                "SELECT id,provider,connection_id,status,remote_id,error,attempts,progress,created,updated "
                "FROM publish_targets WHERE batch_id=? AND user_id=? ORDER BY created",
                (batch["id"], user_id))
        return batches

    def retry_publish_batch(self, batch_id, user_id):
        batch = self.db.one("SELECT id,status FROM publish_batches WHERE id=? AND user_id=?", (batch_id, user_id))
        if not batch or batch["status"] != "completed":
            raise ValueError("Only completed batches with failed destinations can be retried.")
        failed = self.db.q("SELECT id FROM publish_targets WHERE batch_id=? AND user_id=? AND status='failed'",
                           (batch_id, user_id))
        if not failed:
            raise ValueError("This publish batch has no failed destinations.")
        now = time.time()
        for row in failed:
            self.db.x("UPDATE publish_targets SET status='queued',error=NULL,progress=0,updated=? WHERE id=?",
                      (now, row["id"]))
        payload = self.db.one("SELECT metadata FROM publish_batches WHERE id=?", (batch_id,))
        metadata = json.loads(payload["metadata"] or "{}")
        metadata["retry_generation"] = int(metadata.get("retry_generation", 0)) + 1
        self.db.x("UPDATE publish_batches SET status='queued',updated=?,completed=NULL,metadata=? WHERE id=?",
                  (now, json.dumps(metadata), batch_id))
        self.publish_queue.put(batch_id)
        self.db.audit(user_id, "retry_failed_publish_targets", batch_id, user_id)
        return {"queued": len(failed)}

    def _run_publish_batch(self, batch_id):
        batch = self.db.one("SELECT * FROM publish_batches WHERE id=?", (batch_id,))
        if not batch or batch["status"] == "completed":
            return
        job = self.get_job(batch["job_id"], batch["user_id"])
        if not job or job["status"] not in ("done", "needs_attention"):
            now = time.time()
            self.db.x("UPDATE publish_targets SET status='failed',error=?,updated=? WHERE batch_id=? "
                      "AND status IN ('queued','uploading')",
                      ("The completed source video is no longer available.", now, batch_id))
            self.db.x("UPDATE publish_batches SET status='completed',updated=?,completed=? WHERE id=?",
                      (now, now, batch_id))
            self._queue_publish_summary(batch_id)
            return
        self.db.x("UPDATE publish_batches SET status='uploading',updated=? WHERE id=?",
                  (time.time(), batch_id))
        try:
            metadata = json.loads(batch["metadata"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        targets = self.db.q("SELECT * FROM publish_targets WHERE batch_id=? AND user_id=? "
                            "AND status IN ('queued','uploading') ORDER BY created",
                            (batch_id, batch["user_id"]))
        for target in targets:
            now = time.time()
            self.db.x("UPDATE publish_targets SET status='uploading',attempts=attempts+1,updated=?,progress=0.1 WHERE id=?",
                      (now, target["id"]))
            try:
                video_path = self.deliverable_path(batch["job_id"], "final.mp4")
                if not video_path.is_file():
                    raise ValueError("The completed video file is missing.")
                media_url = ""
                if target["provider"] in ("instagram", "facebook", "tiktok"):
                    media_url = self.public_media_url(batch["job_id"], batch["user_id"])
                result = self.publishing.publish_video(target, job, video_path, metadata, media_url)
                self.db.x("UPDATE publish_targets SET status='published',remote_id=?,remote_url=?,"
                          "error=NULL,progress=1,updated=? WHERE id=?",
                          (result.get("remote_id"), result.get("remote_url"), time.time(), target["id"]))
            except Exception as exc:
                error = security.redact(str(exc))[:500] or "Publishing failed."
                self.db.x("UPDATE publish_targets SET status='failed',error=?,progress=0,updated=? WHERE id=?",
                          (error, time.time(), target["id"]))
        remaining = self.db.one("SELECT COUNT(*) AS n FROM publish_targets WHERE batch_id=? "
                                "AND status IN ('queued','uploading')", (batch_id,))["n"]
        if remaining:
            self.publish_queue.put(batch_id)
            return
        now = time.time()
        self.db.x("UPDATE publish_batches SET status='completed',updated=?,completed=? WHERE id=?",
                  (now, now, batch_id))
        self._queue_publish_summary(batch_id)

    def _queue_publish_summary(self, batch_id):
        batch = self.db.one("SELECT b.*,j.topic,j.account_email FROM publish_batches b "
                            "JOIN jobs j ON j.id=b.job_id WHERE b.id=?", (batch_id,))
        if not batch:
            return
        targets = self.db.q("SELECT provider,status,error,remote_id,remote_url FROM publish_targets "
                            "WHERE batch_id=? AND user_id=? ORDER BY created",
                            (batch_id, batch["user_id"]))
        try:
            metadata = json.loads(batch["metadata"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        generation = int(metadata.get("retry_generation", 0))
        recipient = self.publishing.notification_email(batch["user_id"], batch["account_email"])
        frontend = self.publishing.frontend_url
        link = f"{frontend}/?job={urllib.parse.quote(batch['job_id'])}" if frontend else ""
        payload = {"topic": batch["topic"], "targets": targets, "link": link}
        self._enqueue_notification(batch["user_id"], batch["job_id"], "publish_results", recipient, payload,
                                   f"publish:{batch_id}:results:{generation}")

    def list_templates(self, user_id):
        rows = self.db.q("SELECT id,name,params,created,updated FROM project_templates "
                         "WHERE user_id=? ORDER BY updated DESC LIMIT 100", (user_id,))
        for row in rows:
            row["params"] = json.loads(row["params"])
        return rows

    def save_template(self, user_id, name, raw_params):
        name = security.CONTROL_RE.sub("", str(name or "")).strip()[:80]
        if not name or not isinstance(raw_params, dict):
            raise ValueError("A template name and options are required.")
        values = {**raw_params, "topic": "Reusable video template"}
        params = security.validate_job_params(values, self.cfg.get("limits"))
        params.pop("topic", None)
        existing = self.db.one("SELECT id FROM project_templates WHERE user_id=? AND name=?", (user_id, name))
        template_id = existing["id"] if existing else secrets.token_urlsafe(16)
        now = time.time()
        self.db.x("INSERT INTO project_templates(id,user_id,name,params,created,updated) VALUES(?,?,?,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET params=excluded.params,updated=excluded.updated",
                  (template_id, user_id, name, json.dumps(params), now, now))
        self.db.audit(user_id, "save_project_template", name, user_id)
        return {"id": template_id, "name": name, "params": params}

    def delete_template(self, template_id, user_id):
        row = self.db.one("SELECT name FROM project_templates WHERE id=? AND user_id=?", (template_id, user_id))
        if not row:
            raise ValueError("Template not found.")
        self.db.x("DELETE FROM project_templates WHERE id=? AND user_id=?", (template_id, user_id))
        self.db.audit(user_id, "delete_project_template", row["name"], user_id)
        return {"deleted": True}

    def store_media(self, user_id, filename, content, media_type, license_name="", attribution="", workspace_id=None):
        filename = Path(str(filename or "upload")).name
        filename = security.CONTROL_RE.sub("", filename).strip()[:180]
        if not filename or filename in (".", ".."):
            raise ValueError("A valid filename is required.")
        if media_type not in ("video", "image", "audio"):
            raise ValueError("Only video, image, and audio media can be stored.")
        if not isinstance(content, bytes) or not content:
            raise ValueError("The uploaded file is empty.")
        max_bytes = int(self.cfg.get("server.max_upload_mb", 5)) * 1024 * 1024
        if len(content) > max_bytes:
            raise ValueError(f"Media exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")
        if workspace_id:
            member = self.db.one("SELECT 1 x FROM workspace_members WHERE workspace_id=? AND user_id=?",
                                 (workspace_id, user_id))
            if not member:
                raise ValueError("You are not a member of this workspace.")
        media_id = secrets.token_urlsafe(16)
        digest = hashlib.sha256(user_id.encode()).hexdigest()[:24]
        directory = self.cfg.data / "library" / digest
        directory.mkdir(parents=True, exist_ok=True)
        path = security.safe_join(directory, f"{media_id}-{filename}")
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(path)
        self.db.x("INSERT INTO media_library(id,user_id,workspace_id,filename,path,media_type,license,attribution,metadata,created) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (media_id, user_id, workspace_id, filename, str(path), media_type, str(license_name or "")[:160],
                   str(attribution or "")[:500], "{}", time.time()))
        self.db.audit(user_id, "upload_library_media", f"{media_id} {filename}", user_id)
        return {"id": media_id, "filename": filename, "media_type": media_type, "bytes": len(content)}

    def list_media(self, user_id):
        return self.db.q("SELECT id,filename,media_type,license,attribution,created FROM media_library "
                         "WHERE user_id=? OR workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id=?) "
                         "ORDER BY created DESC LIMIT 200", (user_id, user_id))

    def media_path(self, media_id, user_id):
        row = self.db.one("SELECT path FROM media_library WHERE id=? AND (user_id=? OR workspace_id IN "
                          "(SELECT workspace_id FROM workspace_members WHERE user_id=?))", (media_id, user_id, user_id))
        if not row:
            raise ValueError("Media not found.")
        path = Path(row["path"]).resolve()
        root = (self.cfg.data / "library").resolve()
        if root not in path.parents or not path.is_file():
            raise PermissionError("Media path is unavailable.")
        return path

    def create_media_ticket(self, media_id, user_id, lifetime=3600):
        self.media_path(media_id, user_id)
        payload = json.dumps({"media": media_id, "user": user_id, "expires": time.time() + lifetime},
                             separators=(",", ":")).encode()
        token = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        key = os.environ.get("LUMINA_FILE_SIGNING_KEY") or self.auth.token
        signature = hmac.new(key.encode(), token.encode(), hashlib.sha256).hexdigest()
        return f"{token}.{signature}"

    def check_media_ticket(self, ticket, media_id):
        try:
            encoded, signature = ticket.split(".", 1)
            key = os.environ.get("LUMINA_FILE_SIGNING_KEY") or self.auth.token
            expected = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return None
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if payload.get("media") != media_id or float(payload.get("expires", 0)) < time.time():
                return None
            return payload.get("user")
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def delete_media(self, media_id, user_id):
        row = self.db.one("SELECT path,filename FROM media_library WHERE id=? AND user_id=?", (media_id, user_id))
        if not row:
            raise ValueError("Media not found.")
        path = self.media_path(media_id, user_id)
        path.unlink()
        self.db.x("DELETE FROM media_library WHERE id=? AND user_id=?", (media_id, user_id))
        self.db.audit(user_id, "delete_library_media", row["filename"], user_id)
        return {"deleted": True}

    def create_preview(self, jid, user_id="local"):
        job = self.get_job(jid, user_id)
        if not job:
            raise ValueError("No such job.")
        if job["status"] in ("queued", "running", "awaiting_review"):
            raise ValueError("Preview is available after the production job is stable.")
        directory = self.cfg.jobs_dir / jid
        candidates = [directory / "final.mp4"]
        preview = directory / "preview.mp4"
        if preview.is_file():
            return {"file": "preview.mp4"}
        if not candidates[0].is_file():
            raise ValueError("The completed video is not available for preview.")
        from . import media
        media.ffmpeg(["-i", str(candidates[0]), "-vf", "scale='min(640,iw)':-2", "-c:v", "libx264",
                      "-preset", "veryfast", "-crf", "32", "-c:a", "aac", "-b:a", "64k",
                      "-t", "30", "-movflags", "+faststart", str(preview)], timeout=240)
        self.db.audit(user_id, "create_preview", jid, user_id)
        return {"file": "preview.mp4"}

    def update_timeline(self, jid, user_id, timeline):
        job = self.get_job(jid, user_id)
        if not job:
            raise ValueError("No such job.")
        if job["status"] != "awaiting_review":
            raise ValueError("Edit scene order at the footage review step before rendering.")
        gate = self.db.one("SELECT data FROM events WHERE job_id=? AND type='gate' ORDER BY id DESC LIMIT 1", (jid,))
        try:
            gate_data = json.loads(gate["data"]) if gate else {}
        except (TypeError, ValueError):
            gate_data = {}
        if gate_data.get("gate") != "scenes":
            raise ValueError("Timeline order can only be changed during footage review.")
        if not isinstance(timeline, dict) or not isinstance(timeline.get("scenes"), list):
            raise ValueError("Timeline must contain a scenes list.")
        if len(timeline["scenes"]) > 500:
            raise ValueError("Timeline contains too many scenes.")
        state_path = self.cfg.jobs_dir / jid / "state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError("Project state is unavailable.") from e
        allowed = {scene.get("id") for scene in state.get("scenes", [])}
        seen = set()
        cleaned = []
        for scene in timeline["scenes"]:
            if not isinstance(scene, dict) or scene.get("id") not in allowed or scene["id"] in seen:
                raise ValueError("Timeline includes an unknown or duplicate scene.")
            seen.add(scene["id"])
            change = {"id": scene["id"]}
            if "voice" in scene:
                voice = str(scene["voice"] or "").strip()
                if len(voice) > 60 or (voice and not re.fullmatch(r"[A-Za-z0-9-]+", voice)):
                    raise ValueError("Voice overrides may contain only letters, numbers, and hyphens.")
                change["voice_override"] = voice
            if "rate" in scene:
                try:
                    value = scene["rate"] or 0
                    if isinstance(value, bool):
                        raise ValueError
                    numeric_rate = float(value)
                    if not numeric_rate.is_integer():
                        raise ValueError
                    rate = int(numeric_rate)
                except (TypeError, ValueError, OverflowError) as e:
                    raise ValueError("Scene voice rate must be a whole percentage.") from e
                if not -20 <= rate <= 20:
                    raise ValueError("Scene voice rate must be between -20% and +20%.")
                change["rate_override"] = rate
            cleaned.append(change)
        if seen != allowed:
            raise ValueError("Timeline must include each existing scene exactly once.")
        by_id = {scene["id"]: scene for scene in state["scenes"]}
        updated_scenes = []
        for change in cleaned:
            scene = by_id[change["id"]]
            if "voice_override" in change:
                if change["voice_override"]:
                    scene["voice_override"] = change["voice_override"]
                else:
                    scene.pop("voice_override", None)
            if "rate_override" in change:
                if change["rate_override"]:
                    scene["rate_override"] = change["rate_override"]
                else:
                    scene.pop("rate_override", None)
            updated_scenes.append(scene)
        state["scenes"] = updated_scenes
        tmp = state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(state_path)
        self.db.audit(user_id, "edit_timeline", jid, user_id)
        return {"scenes": len(cleaned)}

    def schedule_job(self, jid, user_id, scheduled):
        job = self.get_job(jid, user_id)
        if not job:
            raise ValueError("No such job.")
        if not self.can_edit_job(jid, user_id):
            raise ValueError("You do not have permission to schedule this project.")
        try:
            scheduled = float(scheduled)
        except (ValueError, TypeError) as e:
            raise ValueError("Scheduled time must be a Unix timestamp.") from e
        if scheduled < time.time() + 60:
            raise ValueError("Choose a schedule time at least one minute in the future.")
        title = job["topic"][:160]
        item_id = hashlib.sha256(f"{user_id}:{jid}:schedule".encode()).hexdigest()[:24]
        self.db.x("INSERT INTO content_calendar(id,user_id,workspace_id,job_id,title,scheduled,status,metadata) "
                  "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET scheduled=excluded.scheduled,status='scheduled'",
                  (item_id, user_id, job.get("workspace_id"), jid, title, scheduled, "scheduled", "{}"))
        self.db.audit(user_id, "schedule_job", jid, user_id)
        return {"id": item_id, "scheduled": scheduled}

    def create_review_link(self, jid, user_id="local", lifetime=7 * 86400):
        if not self.get_job(jid, user_id):
            raise ValueError("No such job.")
        if not self.can_edit_job(jid, user_id):
            raise ValueError("You do not have permission to share this project.")
        raw_token = secrets.token_urlsafe(32)
        link_id = secrets.token_urlsafe(16)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires = time.time() + max(300, min(int(lifetime), 30 * 86400))
        self.db.x("INSERT INTO review_links(id,job_id,token_hash,role,expires) VALUES(?,?,?,?,?)",
                  (link_id, jid, token_hash, "reviewer", expires))
        self.db.audit(user_id, "create_review_link", jid, user_id)
        return {"id": link_id, "token": raw_token, "expires": expires}

    def review_link_job(self, token):
        token_hash = hashlib.sha256(str(token or "").encode()).hexdigest()
        row = self.db.one(
            "SELECT j.id,j.topic,j.format,j.status,j.progress,j.created,j.current_agent,j.parent "
            "FROM review_links r JOIN jobs j ON j.id=r.job_id "
            "WHERE r.token_hash=? AND r.revoked IS NULL AND r.expires>?",
            (token_hash, time.time()),
        )
        return row

    def review_link_details(self, token):
        token_hash = hashlib.sha256(str(token or "").encode()).hexdigest()
        row = self.db.one("SELECT id,job_id FROM review_links WHERE token_hash=? AND revoked IS NULL AND expires>?",
                          (token_hash, time.time()))
        if not row:
            return None
        job = self.review_link_job(token)
        if not job:
            return None
        try:
            state = json.loads((self.cfg.jobs_dir / row["job_id"] / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        comments = self.db.q("SELECT id,body,created FROM review_comments WHERE link_id=? ORDER BY created", (row["id"],))
        return {"job": job, "files": state.get("deliverables", []), "comments": comments}

    def add_review_comment(self, token, body):
        detail = self.review_link_details(token)
        if not detail:
            raise ValueError("Review link not found or expired.")
        body = security.CONTROL_RE.sub(" ", str(body or "")).strip()
        if not body or len(body) > 2000:
            raise ValueError("Comment must contain between 1 and 2000 characters.")
        token_hash = hashlib.sha256(str(token).encode()).hexdigest()
        link = self.db.one("SELECT id FROM review_links WHERE token_hash=?", (token_hash,))
        comment_id = secrets.token_urlsafe(16)
        self.db.x("INSERT INTO review_comments(id,job_id,link_id,body,created) VALUES(?,?,?,?,?)",
                  (comment_id, detail["job"]["id"], link["id"], body, time.time()))
        owner = self.db.one("SELECT user_id FROM jobs WHERE id=?", (detail["job"]["id"],))
        self.db.audit("reviewer", "comment_on_review", detail["job"]["id"], owner["user_id"] if owner else "local")
        return {"id": comment_id, "body": body}

    def revoke_review_link(self, jid, link_id, user_id="local"):
        owned = self.db.one(
            "SELECT r.id FROM review_links r JOIN jobs j ON j.id=r.job_id "
            "WHERE r.id=? AND r.job_id=? AND r.revoked IS NULL",
            (link_id, jid),
        )
        if not owned or not self.can_edit_job(jid, user_id):
            raise ValueError("Review link not found or already revoked.")
        self.db.x("UPDATE review_links SET revoked=? WHERE id=?", (time.time(), link_id))
        self.db.audit(user_id, "revoke_review_link", jid, user_id)
        return {"revoked": True}

    def list_jobs(self, limit=30, user_id="local"):
        return self.db.q(
            "SELECT j.id,j.topic,j.format,j.status,j.progress,j.created,j.current_agent,j.parent,j.workspace_id "
            "FROM jobs j WHERE j.user_id=? OR j.workspace_id IN "
            "(SELECT workspace_id FROM workspace_members WHERE user_id=? AND role IN ('owner','editor')) "
            "OR (j.workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id=? AND role='reviewer') "
            "AND j.status IN ('done','needs_attention')) "
            "ORDER BY j.created DESC LIMIT ?", (user_id, user_id, user_id, limit))

    def events_since(self, jid, after=0, limit=300):
        rows = self.db.q("SELECT id, ts, agent, type, message, data FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT ?", (jid, after, limit))
        for r in rows:
            r["data"] = json.loads(r["data"]) if r["data"] else None
        return rows

    def cancel(self, jid):
        job = self.active.get(jid)
        if job:
            job.cancel_flag.set()
            return True
        row = self.db.one("SELECT status FROM jobs WHERE id=?", (jid,))
        if row and row["status"] not in TERMINAL:
            self.db.x("INSERT INTO events(job_id,ts,type,message) VALUES(?,?,?,?)", (jid, time.time(), "job_cancelled", "Job cancelled."))
            self.db.x("UPDATE jobs SET status='cancelled', updated=? WHERE id=?", (time.time(), jid))
            self.bus.notify()
            return True
        return False

    def retry(self, jid):
        row = self.db.one("SELECT status FROM jobs WHERE id=?", (jid,))
        if not row or row["status"] not in ("failed", "cancelled", "needs_attention"):
            raise ValueError("Only failed, cancelled or finished-with-problems jobs can be retried.")
        self.db.x("DELETE FROM steps WHERE job_id=? AND status IN ('failed','running')", (jid,))
        if row["status"] == "needs_attention":
            self.db.x("DELETE FROM steps WHERE job_id=? AND step IN ('qa','editor','publish')", (jid,))
        self.db.x("UPDATE jobs SET status='queued', error=NULL, updated=? WHERE id=?", (time.time(), jid))
        self.queue.put(jid)
        self.bus.notify()

    def approve(self, jid, gate, edits):
        row = self.db.one("SELECT status FROM jobs WHERE id=?", (jid,))
        if not row or row["status"] != "awaiting_review":
            raise ValueError("This job is not waiting for review.")
        if gate not in ("script", "scenes"):
            raise ValueError("Unknown review step.")
        job = Job(jid, self)
        edits = edits or {}
        if gate == "script" and edits.get("script"):
            self._apply_script_edit(job, edits["script"])
        if gate == "scenes":
            self._apply_scene_edits(job, edits)
        job.save_state()
        self.director.mark(job, f"gate:{gate}", "done", "approved")
        job.emit("log", f"Review approved ({gate}). Continuing.", agent="director")
        self.db.x("UPDATE jobs SET status='queued', updated=? WHERE id=?", (time.time(), jid))
        self.queue.put(jid)
        self.bus.notify()

    def _apply_script_edit(self, job, new):
        secs = new.get("sections")
        if not isinstance(secs, list) or not (2 <= len(secs) <= 40):
            raise ValueError("The script needs between 2 and 40 sections.")
        clean = []
        for s in secs:
            nar = clean_narration(str(s.get("narration", ""))[:6000])
            if nar:
                clean.append({"heading": clean_narration(str(s.get("heading", "")))[:120], "narration": nar})
        if len(clean) < 2:
            raise ValueError("The script needs at least two non-empty sections.")
        words = sum(word_count(s["narration"]) for s in clean)
        cap = int(job.max_seconds * self.cfg.get("voice.words_per_second", 2.5) * 0.95)
        if words > cap:
            raise ValueError(f"The script is {words} words, but the length limit allows about {cap}. Please shorten it.")
        job.state["script"] = {**job.state["script"], "title": clean_narration(str(new.get("title", job.state["script"]["title"])))[:100],
                               "sections": clean}
        self.db.audit(job.user_id, "edit_script", job.id, job.user_id)

    def _apply_scene_edits(self, job, edits):
        for sw in edits.get("swaps", [])[:200]:
            swap_asset(job, str(sw.get("scene_id")), int(sw.get("alt")))
        remove = {str(x) for x in edits.get("remove", [])}
        if remove:
            scenes = job.state["scenes"]
            keep = [s for k, s in enumerate(scenes) if s["id"] not in remove or k in (0, len(scenes) - 1)]
            job.state["scenes"] = keep

    # ------------------------------------------------------------ repurposing
    def create_repurpose_job(self, parent, scene_ids, k):
        pstate = parent.state
        by_id = {s["id"]: s for s in pstate["scenes"]}
        subset = []
        for i, sid in enumerate(scene_ids):
            sc = copy.deepcopy(by_id[sid])
            sc["id"] = f"sc_{i + 1:04d}"
            sc["section"] = 0
            subset.append(sc)
        voice_total = sum(s["voice"]["duration"] + 0.4 for s in subset)
        title = f"{pstate['script']['title']} (clip {k})"
        state = {key: copy.deepcopy(pstate[key]) for key in ("style", "brand", "trends", "analytics", "research", "compliance", "mood") if key in pstate}
        state["scenes"] = subset
        state["script"] = {"title": title, "language": pstate["script"].get("language", "en"),
                           "sections": [{"heading": "", "narration": " ".join(s["text"] for s in subset)}]}
        params = {"topic": f"{parent.params['topic']} - highlight {k}", "format": "short", "seconds": int(min(voice_total, 90)),
                  "language": parent.params.get("language", "en"), "translate": [], "review": {"script": False, "scenes": False},
                  "repurpose": 0, "quality": parent.params.get("quality", ""), "music": parent.params.get("music", True),
                  "niche": parent.params.get("niche", ""), "voice": parent.params.get("voice", ""), "burn_captions": True}
        return self.create_job(params, parent=parent.id, state=state, user_id=parent.user_id)

    # --------------------------------------------------------------- helpers
    def ideas(self, niche):
        niche = security.sanitize_topic(niche)
        sugg = []
        try:
            import urllib.parse
            for q in (niche, niche + " how", niche + " why"):
                self.quota.acquire("autocomplete")
                d = http_json(f"{self.cfg.get('stock.autocomplete_url')}?" + urllib.parse.urlencode({"client": "firefox", "ds": "yt", "q": q}))
                sugg += [s for s in (d[1] if isinstance(d, list) and len(d) > 1 else []) if isinstance(s, str)]
        except Exception:
            pass
        sugg = list(dict.fromkeys(sugg))[:25]
        out = self.llm.ask_json("[agent:ideas] You are a YouTube strategist who finds under-served video topics.",
                                f"Niche: {niche}\nWhat people search: {', '.join(sugg) or '(unavailable)'}\n"
                                "Suggest 10 specific video topics a new channel could make with real stock footage (avoid topics "
                                "that need screen recordings or real people). Return JSON {\"ideas\": [{\"topic\": str, \"why\": str}]}")
        return [i for i in out.get("ideas", []) if isinstance(i, dict) and i.get("topic")][:10]

    def brand_get(self, user_id="local"):
        return get_profile(self.db, user_id)

    def brand_set(self, data, user_id="local"):
        prof = dict(DEFAULT_PROFILE)
        prof["channel_name"] = str(data.get("channel_name", ""))[:80]
        prof["niche"] = str(data.get("niche", ""))[:120]
        prof["tone"] = str(data.get("tone", prof["tone"]))[:200]
        prof["audience"] = str(data.get("audience", prof["audience"]))[:120]
        accent = str(data.get("accent", "FFD23F")).lstrip("#")
        prof["accent"] = accent if len(accent) == 6 and all(c in "0123456789abcdefABCDEF" for c in accent) else "FFD23F"
        prof["cta"] = str(data.get("cta", prof["cta"]))[:160]
        prof["banned_phrases"] = [str(x)[:60] for x in (data.get("banned_phrases") or [])][:30]
        self.db.kv_set("brand_profile" if user_id == "local" else f"brand_profile:{user_id}", prof)
        self.db.audit(user_id, "update_brand", prof["channel_name"], user_id)
        return prof

    def analytics_import(self, text, user_id="local"):
        n = import_analytics_csv(self.db, text, user_id)
        self.db.audit(user_id, "import_analytics", f"{n} rows", user_id)
        return n

    def health(self):
        from . import media
        return {"ok": True, "ffmpeg": bool(media.ffmpeg_version()), "llm": self.llm.available(),
                "stock": self.stock.sources(), "voice_engines": self.narration.providers()}

    def settings_view(self):
        return {"keys": self.cfg.secret_status(), "voice": self.cfg.get("voice"), "render": self.cfg.get("render"),
                "audio": self.cfg.get("audio"), "limits": self.cfg.get("limits"), "quota": self.quota.status(),
                "llm_usage": self.llm.usage_today(), "voice_engines": self.narration.providers(),
                "music_files": len([p for p in (self.cfg.assets / "music").rglob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg", ".flac")]),
                "logo": (self.cfg.assets / "brand" / "logo.png").exists()}

    def set_keys(self, keys, user_id="local"):
        for name, val in (keys or {}).items():
            self.cfg.set_secret(name, str(val))
            self.db.audit(user_id, "set_key", name, user_id)
        security.register_secrets(self.cfg.all_secret_values())
        self.llm.reset_chain()
        self.llm.dead.clear()

    def update_settings(self, data, user_id="local"):
        allowed = {"render.quality": ("draft", "standard", "high"), "voice.rate": None, "voice.voice": None,
                   "render.burn_captions_long": (True, False), "render.burn_captions_short": (True, False),
                   "audio.music": (True, False), "audio.music_volume": None}
        for k, v in (data or {}).items():
            if k not in allowed:
                continue
            if allowed[k] and v not in allowed[k]:
                continue
            if k == "audio.music_volume":
                v = max(0.0, min(float(v), 0.6))
            elif k.startswith("voice."):
                v = str(v)[:60]
            self.cfg.set(k, v)
        self.cfg.save()
        self.db.audit(user_id, "update_settings", ", ".join(sorted(k for k in (data or {}) if k in allowed)), user_id)

    def audit_events(self, user_id="local"):
        return self.db.q("SELECT id,ts,actor,action,detail FROM audit WHERE user_id=? ORDER BY id DESC LIMIT 5000",
                         (user_id,))

    def backup(self):
        d = self.cfg.data / "backups"
        d.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if self.db.is_postgres:
            tmp = d / f"_db_{stamp}.dump"
            try:
                with tmp.open("wb") as output:
                    subprocess.run(
                        ["pg_dump", "--no-owner", "--no-acl", "--format=custom", "--dbname", self.db.url],
                        stdout=output, stderr=subprocess.PIPE, check=True,
                    )
            except FileNotFoundError as e:
                tmp.unlink(missing_ok=True)
                raise RuntimeError("PostgreSQL backup requires pg_dump (install postgresql-client).") from e
            except subprocess.CalledProcessError as e:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"PostgreSQL backup failed with exit code {e.returncode}.") from e
        else:
            tmp = d / f"_db_{stamp}.sqlite"
            src = sqlite3.connect(str(self.cfg.db_path))
            dst = sqlite3.connect(str(tmp))
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
        out = d / f"lumina-{stamp}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "lumina.dump" if self.db.is_postgres else "lumina.db")
            sp = self.cfg.data / "settings.json"
            if sp.exists():
                z.write(sp, "settings.json")
        tmp.unlink(missing_ok=True)
        for old in sorted(d.glob("lumina-*.zip"))[:-7]:
            old.unlink(missing_ok=True)
        return out

    def auto_backup(self):
        d = self.cfg.data / "backups"
        recent = [p for p in d.glob("lumina-*.zip") if time.time() - p.stat().st_mtime < 86400]
        if not recent:
            try:
                self.backup()
            except Exception as e:
                self._log_error("backup")
                print(f"Warning: automatic backup failed: {e}", file=sys.stderr)

    def deliverable_path(self, jid, rel):
        base = self.cfg.jobs_dir / jid
        full = security.safe_join(base, rel)
        parts = Path(rel).parts
        if not parts or parts[0] in ("parts", "media", "audio") or rel in ("state.json",) or full.suffix in (".json",) and rel != "publish.json":
            raise PermissionError("Not a downloadable file.")
        return full
