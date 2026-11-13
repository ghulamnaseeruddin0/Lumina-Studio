"""Single-owner bootstrap, server-side administration, and operational controls."""
import hashlib
import hmac
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from pathlib import Path

from . import security

ROLE_PERMISSIONS = {
    "member": (),
    "viewer": ("admin.dashboard.read", "admin.users.read", "admin.agents.read", "admin.analytics.read",
               "admin.audit.read", "admin.health.read", "admin.logs.read", "admin.notifications.read"),
    "support": ("admin.dashboard.read", "admin.users.read", "admin.audit.read", "admin.health.read",
                "admin.logs.read", "admin.sessions.read", "admin.accounts.status"),
    "operator": ("admin.dashboard.read", "admin.users.read", "admin.agents.read", "admin.analytics.read",
                 "admin.audit.read", "admin.health.read", "admin.logs.read", "admin.notifications.read",
                 "admin.sessions.read", "admin.agents.write", "admin.settings.write", "admin.features.write",
                 "admin.maintenance.write", "admin.notifications.retry", "admin.backups.create",
                 "admin.accounts.status"),
}
USER_STATUSES = ("active", "inactive", "suspended")
FEATURE_DEFAULTS = {
    "new_video_jobs": True,
    "direct_publishing": True,
    "workspace_collaboration": True,
    "media_library": True,
}
SENSITIVE_ENV_NAMES = (
    "LUMINA_SUPER_ADMIN_USER_ID", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY",
    "SUPABASE_JWT_SECRET", "LUMINA_DATABASE_URL", "DATABASE_URL", "LUMINA_TOKEN",
    "LUMINA_FILE_SIGNING_KEY", "LUMINA_OAUTH_ENCRYPTION_KEY", "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET", "META_APP_ID", "META_APP_SECRET", "META_GRAPH_VERSION",
    "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "RESEND_API_KEY", "RESEND_FROM_EMAIL",
    "LUMINA_PUBLIC_URL", "LUMINA_FRONTEND_URL", "LUMINA_ALLOWED_ORIGINS", "LUMINA_AUTH_AUDIT_SALT",
    "LUMINA_DATA", "LUMINA_ASSETS", "FFMPEG_PATH", "FFPROBE_PATH", "PORT",
    "REQUIRE_POSTGRES", "REQUIRE_SUPABASE_AUTH", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
    "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY",
    "PEXELS_API_KEY", "PIXABAY_API_KEY",
)

ADMIN_CAPABILITIES = (
    ("global_dashboard", "Global application dashboard", "admin.dashboard.read", "available"),
    ("user_management", "Search and inspect users", "admin.users.read", "available"),
    ("user_invitation", "Invite/create user accounts", "admin.users.manage", "available"),
    ("user_deletion", "Delete an Auth user with typed confirmation", "admin.users.manage", "available"),
    ("account_activation", "Activate accounts", "admin.accounts.status", "available"),
    ("account_deactivation", "Deactivate accounts", "admin.accounts.status", "available"),
    ("account_suspension", "Suspend and restore accounts", "admin.accounts.status", "available"),
    ("role_management", "Assign constrained non-owner roles", "admin.roles.manage", "available"),
    ("permission_management", "Set permissions within role grants", "admin.roles.manage", "available"),
    ("owner_access_control", "Enforce the single owner identity", "admin.dashboard.read", "available"),
    ("agent_management", "Inspect registered agents", "admin.agents.read", "available"),
    ("agent_activation", "Toggle optional agents safely", "admin.agents.write", "available"),
    ("agent_configuration", "Edit live per-agent parameters", "admin.agents.write", "not_implemented"),
    ("agent_performance", "Inspect persisted agent step timings", "admin.analytics.read", "available"),
    ("api_usage", "Inspect external API call counters", "admin.analytics.read", "available"),
    ("ai_token_usage", "Inspect persisted AI token counters", "admin.analytics.read", "available"),
    ("application_analytics", "Inspect global jobs and usage totals", "admin.analytics.read", "available"),
    ("subscriptions", "Subscription management", "admin.billing.read", "deferred_no_billing"),
    ("revenue", "Revenue monitoring", "admin.billing.read", "deferred_no_billing"),
    ("payments", "Payment monitoring", "admin.billing.read", "deferred_no_billing"),
    ("database_monitoring", "Inspect database type and connectivity", "admin.health.read", "available"),
    ("application_settings", "Read/update allow-listed runtime settings", "admin.settings.write", "available"),
    ("integration_management", "Inspect configured integration status", "admin.settings.write", "available"),
    ("environment_configuration", "See configured-variable presence only", "admin.settings.write", "available"),
    ("activity_history", "Inspect global audit history", "admin.audit.read", "available"),
    ("security_monitoring", "Inspect identity, account, and admin events", "admin.audit.read", "available"),
    ("authentication_monitoring", "Inspect recently verified sessions", "admin.sessions.read", "available"),
    ("notification_management", "Inspect publishing email outbox", "admin.notifications.read", "available"),
    ("maintenance_mode", "Enable/disable maintenance access guard", "admin.maintenance.write", "available"),
    ("backup_management", "Create/list application database backups", "admin.backups.create", "available"),
    ("recovery_operations", "Database restore operation", "admin.backups.restore", "manual_only"),
    ("error_monitoring", "Read bounded, redacted application error log", "admin.logs.read", "available"),
    ("feature_management", "Toggle supported server-enforced features", "admin.features.write", "available"),
    ("system_health", "Inspect app dependencies and storage", "admin.health.read", "available"),
    ("global_application_control", "Manage maintenance and protected features", "admin.maintenance.write", "available"),
)


class AdminError(ValueError):
    """A safe, user-presentable administrative operation error."""


class AdminUnavailableError(AdminError):
    """A required external administrative integration is not configured or available."""


class AdminService:
    def __init__(self, app):
        self.app = app
        self.db = app.db
        self.owner_id = ""
        configured_id = os.environ.get("LUMINA_SUPER_ADMIN_USER_ID", "").strip()
        if configured_id:
            try:
                configured_id = str(uuid.UUID(configured_id))
            except ValueError as exc:
                raise RuntimeError("LUMINA_SUPER_ADMIN_USER_ID must be the owner's Supabase Auth UUID.") from exc
            if not app.auth_is_configured():
                raise RuntimeError("Configure Supabase Auth before setting LUMINA_SUPER_ADMIN_USER_ID.")
            existing = self.db.one("SELECT user_id FROM super_admin_identity WHERE singleton_id=1")
            if existing and existing["user_id"] != configured_id:
                raise RuntimeError("Configured super-admin UUID conflicts with the existing singleton owner. Refusing startup.")
            if not existing:
                self.db.x("INSERT INTO super_admin_identity(singleton_id,user_id,created) VALUES(1,?,?)",
                          (configured_id, time.time()))
                self.db.audit("system", "bootstrap_super_admin", "owner UUID bound", configured_id)
            self.owner_id = configured_id
        else:
            self.owner_id = ""

    def is_owner(self, user_id):
        return bool(self.owner_id and user_id == self.owner_id)

    def identity_status(self, user_id):
        row = self.db.one("SELECT status FROM admin_user_controls WHERE user_id=?", (user_id,))
        return (row or {}).get("status", "active")

    def identity_allowed(self, user_id):
        return self.identity_status(user_id) == "active"

    def permissions(self, user_id):
        if self.is_owner(user_id):
            return ["*"]
        row = self.db.one("SELECT status,role,permissions FROM admin_user_controls WHERE user_id=?", (user_id,))
        if not row or row["status"] != "active":
            return []
        try:
            selected = set(json.loads(row["permissions"] or "[]"))
        except (TypeError, ValueError):
            selected = set()
        return sorted(selected.intersection(ROLE_PERMISSIONS.get(row["role"], ())))

    def require(self, user_id, permission):
        grants = self.permissions(user_id)
        if "*" not in grants and permission not in grants:
            raise PermissionError("Administrator permission required.")

    def record_session(self, user_id, claims, remote_ip, user_agent):
        session = str(claims.get("session_id") or claims.get("sid") or claims.get("iat") or "")
        if not session:
            return
        digest = hashlib.sha256(f"{user_id}:{session}".encode()).hexdigest()
        salt = os.environ.get("LUMINA_AUTH_AUDIT_SALT") or os.environ.get("LUMINA_TOKEN", "")
        ip_digest = hmac.new(salt.encode(), str(remote_ip).encode(), hashlib.sha256).hexdigest() if salt else ""
        now = time.time()
        existing = self.db.one("SELECT last_seen FROM admin_auth_sessions WHERE session_hash=?", (digest,))
        if existing and now - existing["last_seen"] < 300:
            return
        self.db.x("INSERT INTO admin_auth_sessions(session_hash,user_id,first_seen,last_seen,ip_hash,user_agent) "
                  "VALUES(?,?,?,?,?,?) ON CONFLICT(session_hash) DO UPDATE SET last_seen=excluded.last_seen,"
                  "ip_hash=excluded.ip_hash,user_agent=excluded.user_agent",
                  (digest, user_id, now, now, ip_digest, str(user_agent or "")[:200]))

    def require_service_key(self):
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        base = os.environ.get("SUPABASE_URL", "").rstrip("/")
        if not key or not base:
            raise AdminUnavailableError(
                "Set SUPABASE_SERVICE_ROLE_KEY and SUPABASE_URL in the backend secret manager to manage Auth users.")
        return base, key

    def _auth_admin(self, method, path, payload=None):
        base, key = self.require_service_key()
        url = base + "/auth/v1/" + path.lstrip("/")
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            "apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read(2 * 1024 * 1024)
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise AdminUnavailableError(f"Supabase Auth administration failed with HTTP {exc.code}.") from exc
        except (OSError, TimeoutError) as exc:
            raise AdminUnavailableError(f"Supabase Auth administration is unavailable ({type(exc).__name__}).") from exc
        except json.JSONDecodeError as exc:
            raise AdminUnavailableError("Supabase Auth returned an invalid response.") from exc

    @staticmethod
    def _user_record(user, control=None):
        control = control or {}
        try:
            permissions = json.loads(control.get("permissions") or "[]") if control else []
        except (TypeError, ValueError):
            permissions = []
        if not isinstance(permissions, list):
            permissions = []
        return {
            "id": user.get("id", ""),
            "email": str(user.get("email") or ""),
            "created_at": user.get("created_at"),
            "last_sign_in_at": user.get("last_sign_in_at"),
            "email_confirmed": bool(user.get("email_confirmed_at") or user.get("confirmed_at")),
            "status": control.get("status", "active"),
            "role": control.get("role", "member"),
            "permissions": permissions,
            "last_active_at": user.get("last_sign_in_at"),
        }

    def list_users(self, actor_id, query=""):
        self.require(actor_id, "admin.users.read")
        query = str(query or "").strip().casefold()[:120]
        response = self._auth_admin("GET", "admin/users?page=1&per_page=1000")
        users = response.get("users", []) if isinstance(response, dict) else []
        controls = {row["user_id"]: row for row in self.db.q("SELECT user_id,status,role,permissions FROM admin_user_controls")}
        results = [self._user_record(user, controls.get(user.get("id")))
                   for user in users if isinstance(user, dict)]
        if query:
            results = [user for user in results if query in user["email"].casefold() or query in user["id"].casefold()]
        return {"users": results[:500], "total": len(results), "has_more": len(results) > 500}

    def get_user(self, actor_id, target_id):
        self.require(actor_id, "admin.users.read")
        self._validate_user_id(target_id)
        response = self._auth_admin("GET", "admin/users/" + urllib.parse.quote(target_id, safe=""))
        user = response.get("user", response) if isinstance(response, dict) else {}
        if not isinstance(user, dict) or not user.get("id"):
            raise AdminError("Auth user not found.")
        control = self.db.one("SELECT status,role,permissions FROM admin_user_controls WHERE user_id=?", (target_id,))
        return self._user_record(user, control)

    @staticmethod
    def _validate_user_id(user_id):
        try:
            return str(uuid.UUID(str(user_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise AdminError("User identifier must be a UUID.") from exc

    def invite_user(self, actor_id, email):
        self.require(actor_id, "admin.users.manage")
        email = str(email or "").strip().lower()[:254]
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise AdminError("Enter a valid email address.")
        redirect = os.environ.get("LUMINA_FRONTEND_URL", "")
        path = "invite"
        if redirect:
            path += "?" + urllib.parse.urlencode({"redirect_to": redirect})
        response = self._auth_admin("POST", path, {"email": email})
        user = response.get("user", response) if isinstance(response, dict) else {}
        user_id = self._validate_user_id(user.get("id"))
        now = time.time()
        self.db.x("INSERT INTO admin_user_controls(user_id,status,role,permissions,updated_by,updated) "
                  "VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET status='active',updated_by=excluded.updated_by,"
                  "updated=excluded.updated",
                  (user_id, "active", "member", "[]", actor_id, now))
        self.db.audit(actor_id, "admin_invite_user", user_id, actor_id)
        return {"id": user_id, "email": email, "invited": True}

    def update_user(self, actor_id, target_id, data):
        self.require(actor_id, "admin.users.read")
        target_id = self._validate_user_id(target_id)
        if not isinstance(data, dict):
            raise AdminError("Expected an object with account controls.")
        is_owner = self.is_owner(actor_id)
        if not is_owner and (data.get("role") is not None or data.get("permissions") is not None):
            raise PermissionError("Only the Super Admin can change roles or permissions.")
        if not is_owner:
            self.require(actor_id, "admin.accounts.status")
            if data.get("status") is None:
                raise PermissionError("Account status permission is required.")
        if self.is_owner(target_id):
            raise AdminError("The exclusive Super Admin account cannot be modified here.")
        status = data.get("status")
        role = data.get("role")
        permissions = data.get("permissions")
        if status is not None and status not in USER_STATUSES:
            raise AdminError("Status must be active, inactive, or suspended.")
        if role is not None and role not in ROLE_PERMISSIONS:
            raise AdminError("Unknown role. The Super Admin role is exclusive and cannot be assigned.")
        if permissions is not None:
            if not isinstance(permissions, list) or any(item not in ROLE_PERMISSIONS.get(role, ()) for item in permissions):
                raise AdminError("Permissions must be selected from the chosen role's allowed grants.")
        current = self.db.one("SELECT status,role,permissions FROM admin_user_controls WHERE user_id=?", (target_id,))
        merged_status = status if status is not None else (current or {}).get("status", "active")
        merged_role = role if role is not None else (current or {}).get("role", "member")
        if permissions is None:
            try:
                merged_permissions = json.loads((current or {}).get("permissions") or "[]")
            except (TypeError, ValueError):
                merged_permissions = []
            if role is not None:
                merged_permissions = list(ROLE_PERMISSIONS[merged_role])
        else:
            merged_permissions = list(dict.fromkeys(permissions))
        if status is not None and merged_status != (current or {}).get("status", "active"):
            self._auth_admin("PUT", "admin/users/" + urllib.parse.quote(target_id, safe=""),
                             {"ban_duration": "none" if merged_status == "active" else "876000h"})
        now = time.time()
        self.db.x("INSERT INTO admin_user_controls(user_id,status,role,permissions,updated_by,updated) "
                  "VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET status=excluded.status,role=excluded.role,"
                  "permissions=excluded.permissions,updated_by=excluded.updated_by,updated=excluded.updated",
                  (target_id, merged_status, merged_role, json.dumps(merged_permissions), actor_id, now))
        self.db.audit(actor_id, "admin_update_user", f"{target_id} status={merged_status} role={merged_role}", actor_id)
        return {"id": target_id, "status": merged_status, "role": merged_role,
                "permissions": merged_permissions}

    def delete_user(self, actor_id, target_id, confirmation):
        self.require(actor_id, "admin.users.manage")
        target_id = self._validate_user_id(target_id)
        if not self.is_owner(actor_id):
            raise PermissionError("Only the Super Admin can delete accounts.")
        if self.is_owner(target_id) or target_id == actor_id:
            raise AdminError("The exclusive Super Admin account cannot be deleted here.")
        user = self.get_user(actor_id, target_id)
        if not hmac.compare_digest(str(confirmation or "").strip().casefold(), user["email"].casefold()):
            raise AdminError("Type the target account's exact email address to confirm deletion.")
        self._auth_admin("DELETE", "admin/users/" + urllib.parse.quote(target_id, safe=""))
        now = time.time()
        self.db.x("INSERT INTO admin_user_controls(user_id,status,role,permissions,updated_by,updated) "
                  "VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET status='inactive',updated_by=excluded.updated_by,"
                  "updated=excluded.updated",
                  (target_id, "inactive", "member", "[]", actor_id, now))
        self.db.audit(actor_id, "admin_delete_auth_user", target_id, actor_id)
        return {"deleted": True, "application_data_retained": True}

    def agent_status(self, agent_id):
        row = self.db.one("SELECT enabled,settings,updated_by,updated FROM admin_agent_settings WHERE agent_id=?",
                          (agent_id,))
        return row or {"enabled": 1, "settings": "{}", "updated_by": "", "updated": None}

    def agent_enabled(self, agent_id):
        return bool(self.agent_status(agent_id)["enabled"])

    def agents(self, actor_id):
        self.require(actor_id, "admin.agents.read")
        rows = self.db.q("SELECT step,status,started,finished,summary FROM steps WHERE started>? "
                         "ORDER BY started DESC LIMIT 10000", (time.time() - 30 * 86400,))
        stats = defaultdict(lambda: {"runs": 0, "failures": 0, "seconds": 0.0})
        for row in rows:
            agent_id = str(row["step"]).split(":", 1)[0]
            stat = stats[agent_id]
            stat["runs"] += 1
            stat["failures"] += row["status"] in ("failed", "skipped")
            if row["started"] and row["finished"]:
                stat["seconds"] += max(0, row["finished"] - row["started"])
        result = []
        for agent in self.app.registry.values():
            state = self.agent_status(agent.id)
            usage = stats[agent.id]
            result.append({**agent.meta(), "enabled": bool(state["enabled"]), "critical": bool(agent.critical),
                           "runtime_configuration_supported": False, "runs_30d": usage["runs"],
                           "failures_30d": usage["failures"],
                           "average_seconds_30d": round(usage["seconds"] / usage["runs"], 2) if usage["runs"] else 0})
        return sorted(result, key=lambda item: item["number"])

    def update_agent(self, actor_id, agent_id, enabled):
        self.require(actor_id, "admin.agents.write")
        agent = self.app.registry.get(agent_id)
        if not agent:
            raise AdminError("Agent not found.")
        if not isinstance(enabled, bool):
            raise AdminError("Agent enabled must be a boolean.")
        if not enabled and agent.critical:
            raise AdminError("This critical pipeline agent cannot be disabled because downstream production depends on it.")
        now = time.time()
        self.db.x("INSERT INTO admin_agent_settings(agent_id,enabled,settings,updated_by,updated) "
                  "VALUES(?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET enabled=excluded.enabled,"
                  "updated_by=excluded.updated_by,updated=excluded.updated",
                  (agent_id, int(enabled), "{}", actor_id, now))
        self.db.audit(actor_id, "admin_toggle_agent", f"{agent_id} enabled={enabled}", actor_id)
        return {"id": agent_id, "enabled": enabled}

    def feature_flags(self, actor_id):
        self.require(actor_id, "admin.settings.write")
        stored = {row["flag"]: bool(row["enabled"]) for row in self.db.q("SELECT flag,enabled FROM admin_feature_flags")}
        return {key: stored.get(key, default) for key, default in FEATURE_DEFAULTS.items()}

    def set_feature(self, actor_id, flag, enabled):
        self.require(actor_id, "admin.features.write")
        if flag not in FEATURE_DEFAULTS or not isinstance(enabled, bool):
            raise AdminError("Unknown feature flag or invalid enabled value.")
        now = time.time()
        self.db.x("INSERT INTO admin_feature_flags(flag,enabled,updated_by,updated) VALUES(?,?,?,?) "
                  "ON CONFLICT(flag) DO UPDATE SET enabled=excluded.enabled,updated_by=excluded.updated_by,"
                  "updated=excluded.updated",
                  (flag, int(enabled), actor_id, now))
        self.db.audit(actor_id, "admin_set_feature", f"{flag} enabled={enabled}", actor_id)
        return {"flag": flag, "enabled": enabled}

    def feature_enabled(self, flag):
        if flag not in FEATURE_DEFAULTS:
            return True
        row = self.db.one("SELECT enabled FROM admin_feature_flags WHERE flag=?", (flag,))
        return bool(row["enabled"]) if row else FEATURE_DEFAULTS[flag]

    def maintenance_enabled(self):
        row = self.db.one("SELECT enabled FROM admin_feature_flags WHERE flag='maintenance_mode'")
        return bool(row["enabled"]) if row else False

    def set_maintenance(self, actor_id, enabled):
        self.require(actor_id, "admin.maintenance.write")
        if not isinstance(enabled, bool):
            raise AdminError("Maintenance mode must be a boolean.")
        now = time.time()
        self.db.x("INSERT INTO admin_feature_flags(flag,enabled,updated_by,updated) VALUES('maintenance_mode',?,?,?) "
                  "ON CONFLICT(flag) DO UPDATE SET enabled=excluded.enabled,updated_by=excluded.updated_by,"
                  "updated=excluded.updated",
                  (int(enabled), actor_id, now))
        self.db.audit(actor_id, "admin_maintenance_mode", f"enabled={enabled}", actor_id)
        return {"enabled": enabled}

    def capabilities(self, actor_id):
        grants = set(self.permissions(actor_id))
        return {
            "owner": self.is_owner(actor_id),
            "capabilities": [{"id": key, "name": label, "status": status, "available": status == "available",
                              "permitted": "*" in grants or permission in grants}
                             for key, label, permission, status in ADMIN_CAPABILITIES],
        }

    def integrations(self, actor_id):
        self.require(actor_id, "admin.settings.write")
        statuses = self.app.cfg.secret_status()
        configured = {name: bool(os.environ.get(name)) for name in SENSITIVE_ENV_NAMES}
        configured.update({name: statuses.get(name, False) for name in statuses})
        return {"configured": configured,
                "providers": self.app.publishing.capabilities(actor_id)["platforms"],
                "billing": {"available": False, "reason": "No payment provider is integrated; billing is deferred."}}

    def sessions(self, actor_id):
        self.require(actor_id, "admin.sessions.read")
        return self.db.q("SELECT user_id,first_seen,last_seen,ip_hash,user_agent FROM admin_auth_sessions "
                         "ORDER BY last_seen DESC LIMIT 500")

    def audit_events(self, actor_id, limit=500):
        self.require(actor_id, "admin.audit.read")
        return self.db.q("SELECT id,ts,actor,action,detail,user_id FROM audit ORDER BY id DESC LIMIT ?",
                         (max(1, min(int(limit), 2000)),))

    def notifications(self, actor_id, limit=200):
        self.require(actor_id, "admin.notifications.read")
        return self.db.q("SELECT id,user_id,job_id,kind,status,attempts,next_attempt,error,created,sent "
                         "FROM notification_outbox ORDER BY created DESC LIMIT ?",
                         (max(1, min(int(limit), 500)),))

    def retry_notification(self, actor_id, notification_id):
        self.require(actor_id, "admin.notifications.retry")
        row = self.db.one("SELECT user_id,status FROM notification_outbox WHERE id=?", (notification_id,))
        if not row:
            raise AdminError("Notification not found.")
        return self.app.retry_notification(row["user_id"], notification_id)

    def backups(self, actor_id):
        self.require(actor_id, "admin.health.read")
        folder = self.app.cfg.data / "backups"
        files = []
        for path in sorted(folder.glob("lumina-*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                files.append({"name": path.name, "size_bytes": path.stat().st_size,
                              "created": path.stat().st_mtime})
            except OSError:
                continue
        return files[:50]

    def create_backup(self, actor_id):
        self.require(actor_id, "admin.backups.create")
        path = self.app.backup()
        self.db.audit(actor_id, "admin_create_backup", path.name, actor_id)
        return {"name": path.name, "size_bytes": path.stat().st_size}

    def error_log(self, actor_id, limit=100):
        self.require(actor_id, "admin.logs.read")
        path = self.app.cfg.data / "logs" / "errors.log"
        if not path.is_file():
            return {"lines": [], "available": False}
        try:
            with path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - 128 * 1024))
                content = stream.read().decode("utf-8", "replace")
        except OSError as exc:
            raise AdminError(f"Could not read the error log ({type(exc).__name__}).") from exc
        lines = [security.redact(line) for line in content.splitlines()]
        return {"lines": lines[-max(1, min(int(limit), 500)):], "available": True}

    def overview(self, actor_id):
        self.require(actor_id, "admin.dashboard.read")
        jobs = self.db.q("SELECT status,COUNT(*) AS total FROM jobs GROUP BY status")
        api_usage = self.db.q("SELECT provider,COUNT(*) AS calls FROM api_calls WHERE ts>? "
                              "GROUP BY provider ORDER BY calls DESC", (time.time() - 7 * 86400,))
        usage = self.db.one("SELECT COALESCE(SUM(count),0) AS requests,COALESCE(SUM(tokens),0) AS tokens "
                            "FROM llm_usage")
        active = self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')")
        errors = self.db.one("SELECT COUNT(*) AS n FROM audit WHERE action LIKE 'admin_%'")
        disk = shutil.disk_usage(self.app.cfg.data)
        database_bytes = None
        if not self.db.is_postgres:
            try:
                database_bytes = Path(self.db.path).stat().st_size
            except OSError:
                database_bytes = None
        user_total = None
        users_error = ""
        if os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
            try:
                remote = self._auth_admin("GET", "admin/users?page=1&per_page=1")
                user_total = remote.get("total") if isinstance(remote, dict) else None
            except AdminError as exc:
                users_error = str(exc)
        return {
            "owner_configured": bool(self.owner_id),
            "supabase_admin_configured": bool(os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
            "users_total": user_total,
            "users_error": users_error,
            "jobs_by_status": {row["status"]: int(row["total"]) for row in jobs},
            "active_jobs": int(active["n"]),
            "api_calls_7d": api_usage,
            "ai_requests": int(usage["requests"]),
            "ai_tokens": int(usage["tokens"]),
            "admin_actions": int(errors["n"]),
            "database": {"backend": "postgresql" if self.db.is_postgres else "sqlite",
                         "connected": self.database_ready(), "bytes": database_bytes},
            "storage": {"free_bytes": disk.free, "total_bytes": disk.total},
            "maintenance_mode": self.maintenance_enabled(),
        }

    def database_ready(self):
        try:
            self.db.one("SELECT 1 AS ok")
            return True
        except Exception:
            return False

    def health(self, actor_id):
        self.require(actor_id, "admin.health.read")
        return {**self.app.health(), "database": self.database_ready(),
                "database_backend": "postgresql" if self.db.is_postgres else "sqlite",
                "maintenance_mode": self.maintenance_enabled()}
