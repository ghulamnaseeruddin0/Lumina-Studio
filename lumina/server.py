"""HTTP server (standard library): JSON API, live event stream (SSE), file downloads, static web UI."""
import hashlib
import csv
import io
import json
import mimetypes
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .admin import AdminUnavailableError, ROLE_PERMISSIONS
from .agents import agent_catalog
from .config import ROOT
from .job import TERMINAL
from .security import redact

WEB = ROOT / "web"
MAX_BODY = 16 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "Lumina/" + __version__
    protocol_version = "HTTP/1.1"

    app = None  # set by make_server

    def log_message(self, fmt, *args):
        pass

    # ------------------------------------------------------------- plumbing
    def ip(self):
        return self.client_address[0]

    def origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return None
        allowed = list(self.app.cfg.get("server.allowed_origins", []))
        allowed.extend(x.strip() for x in os.environ.get("LUMINA_ALLOWED_ORIGINS", "").split(",") if x.strip())
        host = self.headers.get("Host", "")
        if origin in allowed or origin.endswith("//" + host):
            return origin
        return False

    def cors(self):
        o = self.origin_ok()
        if o:
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")
            if self.headers.get("Access-Control-Request-Private-Network"):
                self.send_header("Access-Control-Allow-Private-Network", "true")

    def security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.cors()
        self.security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.security_headers()
        self.end_headers()

    def error(self, code, msg):
        self.send_json({"error": redact(msg)}, code)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("Request too large.")
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise ValueError("Invalid JSON.")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def token(self, qs):
        h = self.headers.get("Authorization", "")
        if h.startswith("Bearer "):
            return h[7:].strip()
        return (qs.get("token") or [""])[0]

    # ---------------------------------------------------------------- routing
    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.route("GET")

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        path = urllib.parse.unquote(u.path)
        app = self.app
        try:
            if self.origin_ok() is False and path.startswith("/api/"):
                return self.error(403, "Origin not allowed. Add it to server.allowed_origins in settings.json.")
            if not app.limiter.allow("ip:" + self.ip(), app.cfg.get("server.rate_limit_per_min", 300)):
                return self.error(429, "Too many requests. Slow down.")
            if not path.startswith("/api/"):
                return self.static(path)
            if path == "/api/health":
                return self.send_json({"ok": True, "version": __version__})
            oauth_callback = re.fullmatch(r"/api/publishing/callback/(youtube|meta|tiktok)", path)
            if oauth_callback and method == "GET":
                frontend = app.publishing.frontend_url
                if not frontend:
                    return self.error(503, "Publishing callback is not configured.")
                outcome = "connected"
                try:
                    app.publishing.complete_oauth(oauth_callback.group(1), qs)
                except ValueError:
                    outcome = "error"
                parsed_frontend = urllib.parse.urlparse(frontend)
                if parsed_frontend.scheme not in ("https", "http") or not parsed_frontend.netloc:
                    return self.error(503, "Publishing callback frontend URL is invalid.")
                query = urllib.parse.parse_qsl(parsed_frontend.query, keep_blank_values=True)
                query.extend((("publishing", outcome), ("provider", oauth_callback.group(1))))
                location = urllib.parse.urlunparse((parsed_frontend.scheme, parsed_frontend.netloc,
                    parsed_frontend.path, parsed_frontend.params, urllib.parse.urlencode(query), ""))
                return self.redirect(location)
            public_media = re.fullmatch(r"/api/publishing/media/([\w\-]+)", path)
            if public_media and method in ("GET", "HEAD") and qs.get("ticket"):
                if not app.admin.feature_enabled("direct_publishing"):
                    return self.error(503, "Direct publishing is temporarily disabled.")
                media = app.check_public_media_ticket(qs["ticket"][0], public_media.group(1))
                if not media:
                    return self.error(404, "Temporary video URL expired or is invalid.")
                return self.file(media)
            supplied = self.token(qs)
            identity = None
            if path.startswith("/api/media/") and qs.get("ticket"):
                if not app.admin.feature_enabled("media_library"):
                    return self.error(503, "The media library is temporarily disabled.")
                media_id = path.rsplit("/", 1)[-1]
                user_id = app.check_media_ticket(qs["ticket"][0], media_id)
            elif path.startswith("/api/review/") and qs.get("token"):
                user_id = "review:" + qs["token"][0]
            elif path.startswith("/api/file/") and qs.get("ticket"):
                jid = path[len("/api/file/"):].split("/", 1)[0]
                user_id = app.auth.check_file_ticket(qs["ticket"][0], jid)
            elif app.auth.supabase_enabled:
                identity = app.auth.supabase_identity(supplied)
                user_id = identity["id"] if identity else None
            else:
                if app.auth.locked(self.ip()):
                    return self.error(429, "Too many failed sign-in attempts. Try again in a few minutes.")
                user_id = "local" if app.auth.check(supplied, self.ip()) else None
            if not user_id:
                return self.error(401, "Unauthorized. Sign in again and retry the request.")
            if identity:
                app.admin.record_session(user_id, identity, self.ip(), self.headers.get("User-Agent", ""))
            if user_id and not user_id.startswith("review:") and not app.admin.identity_allowed(user_id):
                return self.error(403, "This account is inactive or suspended. Contact the application owner.")
            if app.admin.maintenance_enabled() and not (
                    path.startswith("/api/admin/") or path == "/api/health"
                    or oauth_callback or public_media):
                return self.error(503, "The application is temporarily in maintenance mode.")
            return self.api(method, path, qs, user_id, identity.get("email", "") if identity else "")
        except (BrokenPipeError, ConnectionResetError):
            return
        except AdminUnavailableError as e:
            return self.error(503, str(e))
        except ValueError as e:
            return self.error(400, str(e))
        except PermissionError as e:
            return self.error(403, str(e))
        except Exception as e:  # never leak internals
            app._log_error("server")
            return self.error(500, "Internal error. See data/logs/errors.log.")

    def admin_api(self, method, path, qs, user_id):
        admin = self.app.admin
        if path == "/api/admin/overview" and method == "GET":
            return self.send_json(admin.overview(user_id))
        if path == "/api/admin/capabilities" and method == "GET":
            return self.send_json(admin.capabilities(user_id))
        if path == "/api/admin/users" and method == "GET":
            return self.send_json(admin.list_users(user_id, (qs.get("q") or [""])[0]))
        if path == "/api/admin/users" and method == "POST":
            return self.send_json(admin.invite_user(user_id, self.body().get("email")), 201)
        user_route = re.fullmatch(r"/api/admin/users/([^/]+)", path)
        if user_route and method == "GET":
            return self.send_json(admin.get_user(user_id, user_route.group(1)))
        if user_route and method == "PUT":
            return self.send_json(admin.update_user(user_id, user_route.group(1), self.body()))
        if user_route and method == "DELETE":
            return self.send_json(admin.delete_user(
                user_id, user_route.group(1), self.body().get("confirmation")))
        if path == "/api/admin/roles" and method == "GET":
            admin.require(user_id, "admin.users.read")
            return self.send_json({"roles": {
                name: list(permissions) for name, permissions in ROLE_PERMISSIONS.items()
            }, "super_admin_assignable": False})
        if path == "/api/admin/agents" and method == "GET":
            return self.send_json({"agents": admin.agents(user_id)})
        agent_route = re.fullmatch(r"/api/admin/agents/([\w\-]+)", path)
        if agent_route and method == "PUT":
            return self.send_json(admin.update_agent(user_id, agent_route.group(1), self.body().get("enabled")))
        if path == "/api/admin/features" and method == "GET":
            return self.send_json({"features": admin.feature_flags(user_id),
                                   "maintenance_mode": admin.maintenance_enabled()})
        feature_route = re.fullmatch(r"/api/admin/features/([\w\-]+)", path)
        if feature_route and method == "PUT":
            return self.send_json(admin.set_feature(
                user_id, feature_route.group(1), self.body().get("enabled")))
        if path == "/api/admin/maintenance" and method == "PUT":
            return self.send_json(admin.set_maintenance(user_id, self.body().get("enabled")))
        if path == "/api/admin/settings" and method == "GET":
            admin.require(user_id, "admin.settings.write")
            return self.send_json(self.app.settings_view())
        if path == "/api/admin/settings" and method == "PUT":
            admin.require(user_id, "admin.settings.write")
            self.app.update_settings(self.body(), user_id)
            return self.send_json(self.app.settings_view())
        if path == "/api/admin/integrations" and method == "GET":
            return self.send_json(admin.integrations(user_id))
        if path == "/api/admin/audit" and method == "GET":
            return self.send_json({"events": admin.audit_events(
                user_id, (qs.get("limit") or [500])[0])})
        if path == "/api/admin/sessions" and method == "GET":
            return self.send_json({"sessions": admin.sessions(user_id)})
        if path == "/api/admin/notifications" and method == "GET":
            return self.send_json({"notifications": admin.notifications(user_id)})
        notification_retry = re.fullmatch(r"/api/admin/notifications/([\w\-]+)/retry", path)
        if notification_retry and method == "POST":
            return self.send_json(admin.retry_notification(user_id, notification_retry.group(1)))
        if path == "/api/admin/backups" and method == "GET":
            return self.send_json({"backups": admin.backups(user_id)})
        if path == "/api/admin/backups" and method == "POST":
            return self.send_json(admin.create_backup(user_id), 201)
        if path == "/api/admin/errors" and method == "GET":
            return self.send_json(admin.error_log(user_id, (qs.get("limit") or [100])[0]))
        if path == "/api/admin/health" and method == "GET":
            return self.send_json(admin.health(user_id))
        return self.error(404, "Unknown administrator endpoint.")

    # -------------------------------------------------------------------- API
    def api(self, method, path, qs, user_id="local", user_email=""):
        app = self.app
        if path.startswith("/api/admin/"):
            return self.admin_api(method, path, qs, user_id)
        if method == "POST" and path == "/api/jobs" and not app.admin.feature_enabled("new_video_jobs"):
            return self.error(503, "New video jobs are temporarily disabled by the application owner.")
        if (path.startswith("/api/workspaces") or path.startswith("/api/workspace-invitations")) \
                and not app.admin.feature_enabled("workspace_collaboration"):
            return self.error(503, "Workspace collaboration is temporarily disabled.")
        if (path == "/api/media" or path.startswith("/api/media/") or path.startswith("/api/media-link/")) \
                and not app.admin.feature_enabled("media_library"):
            return self.error(503, "The media library is temporarily disabled.")
        if ((method == "POST" and (path.startswith("/api/publishing/connect/")
                                   or re.fullmatch(r"/api/jobs/[\w\-]+/publish", path)
                                   or re.fullmatch(r"/api/publishing/(?:batches|notifications)/[\w\-]+/retry", path)))
                or (method == "DELETE" and path.startswith("/api/publishing/connections/"))) \
                and not app.admin.feature_enabled("direct_publishing"):
            return self.error(503, "Direct publishing is temporarily disabled.")
        if app.auth.supabase_enabled:
            if (path == "/api/settings" and method == "PUT") or (path == "/api/keys" and method == "PUT"):
                app.admin.require(user_id, "admin.settings.write")
            if path == "/api/backup" and method == "POST":
                app.admin.require(user_id, "admin.backups.create")
            if path == "/api/token/rotate" and method == "POST" and not app.admin.is_owner(user_id):
                raise PermissionError("Only the Super Admin can rotate the shared studio token.")
        m = re.fullmatch(r"/api/jobs/([\w\-]+)(?:/([\w\-]+))?", path)
        review_public = re.fullmatch(r"/api/review/([\w\-]+)", path)
        if review_public and user_id.startswith("review:") and method == "GET":
            token = user_id.removeprefix("review:")
            detail = app.review_link_details(token)
            if not detail or detail["job"]["id"] != review_public.group(1):
                return self.error(404, "Review link not found or expired.")
            return self.send_json(detail)
        review_comments = re.fullmatch(r"/api/review/([\w\-]+)/comments", path)
        if review_comments and user_id.startswith("review:") and method == "POST":
            token = user_id.removeprefix("review:")
            detail = app.review_link_details(token)
            if not detail or detail["job"]["id"] != review_comments.group(1):
                return self.error(404, "Review link not found or expired.")
            if not app.limiter.allow("review-comment:" + hashlib.sha256(token.encode()).hexdigest(), 30, 3600):
                return self.error(429, "Comment limit reached for this review link.")
            return self.send_json(app.add_review_comment(token, self.body().get("body")), 201)
        review_media = re.fullmatch(r"/api/review/([\w\-]+)/file/(.+)", path)
        if review_media and user_id.startswith("review:") and method == "GET":
            token = user_id.removeprefix("review:")
            reviewed = app.review_link_job(token)
            if not reviewed or reviewed["id"] != review_media.group(1):
                return self.error(404, "Review link not found or expired.")
            return self.file(app.deliverable_path(reviewed["id"], review_media.group(2)))
        review_stream = re.fullmatch(r"/api/review/([\w\-]+)/stream", path)
        if review_stream and user_id.startswith("review:") and method == "GET":
            token = user_id.removeprefix("review:")
            reviewed = app.review_link_job(token)
            if not reviewed or reviewed["id"] != review_stream.group(1):
                return self.error(404, "Review link not found or expired.")
            return self.stream(reviewed["id"], int((qs.get("after") or ["0"])[0] or 0), review_token=token)
        version_restore = re.fullmatch(r"/api/versions/([\w\-]+)/restore", path)
        if version_restore and method == "POST":
            return self.send_json(app.restore_version(version_restore.group(1), user_id))
        if path == "/api/workspaces" and method == "GET":
            return self.send_json({"workspaces": app.list_workspaces(user_id)})
        if path == "/api/workspaces" and method == "POST":
            return self.send_json(app.create_workspace(user_id, self.body().get("name")), 201)
        workspace_roster = re.fullmatch(r"/api/workspaces/([\w\-]+)/members", path)
        if workspace_roster and method == "GET":
            return self.send_json(app.workspace_roster(workspace_roster.group(1), user_id))
        workspace_member = re.fullmatch(r"/api/workspaces/([\w\-]+)/members/([\w\-]+)", path)
        if workspace_member and method == "DELETE":
            workspace_id, member_id = workspace_member.groups()
            return self.send_json(app.remove_workspace_member(workspace_id, user_id, member_id))
        invitation_route = re.fullmatch(r"/api/workspace-invitations/([\w\-]+)", path)
        if invitation_route and method == "DELETE":
            return self.send_json(app.revoke_workspace_invitation(invitation_route.group(1), user_id))
        if path == "/api/workspace-invitations/accept" and method == "POST":
            body = self.body()
            return self.send_json(app.accept_workspace_invitation(body.get("token"), user_id, user_email))
        if path == "/api/local/import-jobs" and method == "POST":
            return self.send_json(app.claim_local_jobs(user_id, self.body().get("job_ids")))
        workspace_action = re.fullmatch(r"/api/workspaces/([\w\-]+)/(invite|accept)", path)
        if workspace_action and method == "POST":
            workspace_id, action = workspace_action.groups()
            body = self.body()
            if action == "invite":
                return self.send_json(app.invite_workspace_member(
                    workspace_id, user_id, body.get("email"), body.get("role", "editor")), 201)
            return self.send_json(app.accept_workspace_invitation(body.get("token"), user_id, user_email))
        if path == "/api/calendar" and method == "GET":
            return self.send_json({"items": app.list_calendar(user_id)})
        calendar_item = re.fullmatch(r"/api/calendar/([\w\-]+)", path)
        if calendar_item and method == "PUT":
            body = self.body()
            return self.send_json(app.update_calendar(user_id, calendar_item.group(1), body.get("title"), body.get("scheduled")))
        if path == "/api/experiments" and method == "GET":
            return self.send_json({"experiments": app.list_experiments(user_id)})
        experiment_action = re.fullmatch(r"/api/experiments/([\w\-]+)", path)
        if experiment_action and method == "PUT":
            body = self.body()
            return self.send_json(app.update_experiment(experiment_action.group(1), user_id, body.get("metrics")))
        if path == "/api/analytics" and method == "GET":
            return self.send_json(app.analytics_summary(user_id))
        if path == "/api/templates" and method == "GET":
            return self.send_json({"templates": app.list_templates(user_id)})
        if path == "/api/templates" and method == "POST":
            body = self.body()
            return self.send_json(app.save_template(user_id, body.get("name"), body.get("params")), 201)
        template_action = re.fullmatch(r"/api/templates/([\w\-]+)", path)
        if template_action and method == "DELETE":
            return self.send_json(app.delete_template(template_action.group(1), user_id))
        if path == "/api/media" and method == "GET":
            return self.send_json({"media": app.list_media(user_id)})
        if path == "/api/media" and method == "POST":
            if not self.headers.get("Content-Length"):
                return self.error(411, "Content-Length is required for media uploads.")
            if int(self.headers["Content-Length"]) > MAX_BODY:
                return self.error(413, "Media upload exceeds the request size limit.")
            body = self.body()
            maximum = int(app.cfg.get("server.max_upload_mb", 5)) * 1024 * 1024
            content = security.decode_upload_data(body.get("data"), maximum)
            saved = app.store_media(user_id, body.get("filename"), content, body.get("media_type"),
                                    body.get("license"), body.get("attribution"), body.get("workspace_id"))
            return self.send_json(saved, 201)
        if path.startswith("/api/media-link/") and method == "GET":
            media_id = path[len("/api/media-link/"):]
            ticket = app.create_media_ticket(media_id, user_id)
            return self.send_json({"url": f"/api/media/{urllib.parse.quote(media_id)}?ticket={urllib.parse.quote(ticket)}"})
        media_file = re.fullmatch(r"/api/media/([\w\-]+)", path)
        if media_file and method == "GET":
            media_id = media_file.group(1)
            file_path = app.media_path(media_id, user_id)
            return self.file(file_path)
        if media_file and method == "DELETE":
            return self.send_json(app.delete_media(media_file.group(1), user_id))
        if path == "/api/status":
            return self.send_json({**app.health(), "agents": agent_catalog(app.registry), "version": __version__})
        if path == "/api/preflight" and method == "POST":
            return self.send_json(app.preflight(self.body()))
        if path == "/api/storage" and method == "GET":
            return self.send_json(app.storage_summary(user_id))
        if path == "/api/publishing/capabilities" and method == "GET":
            return self.send_json(app.publishing.capabilities(user_id))
        oauth_start = re.fullmatch(r"/api/publishing/connect/(youtube|meta|tiktok)", path)
        if oauth_start and method == "POST":
            return self.send_json(app.publishing.start_oauth(oauth_start.group(1), user_id))
        connection_route = re.fullmatch(r"/api/publishing/connections/([\w\-]+)", path)
        if connection_route and method == "DELETE":
            return self.send_json(app.publishing.disconnect(user_id, connection_route.group(1)))
        if path == "/api/publishing/preferences" and method == "GET":
            return self.send_json({"notification_email": app.publishing.notification_email(user_id, user_email),
                                   "resend_configured": bool(os.environ.get("RESEND_API_KEY") and
                                                             os.environ.get("RESEND_FROM_EMAIL"))})
        if path == "/api/publishing/preferences" and method == "PUT":
            return self.send_json(app.publishing.set_notification_email(user_id, self.body().get("notification_email")))
        if path == "/api/publishing/notifications" and method == "GET":
            return self.send_json({"notifications": app.publishing_notifications(user_id)})
        notification_retry = re.fullmatch(r"/api/publishing/notifications/([\w\-]+)/retry", path)
        if notification_retry and method == "POST":
            return self.send_json(app.retry_notification(user_id, notification_retry.group(1)))
        batch_retry = re.fullmatch(r"/api/publishing/batches/([\w\-]+)/retry", path)
        if batch_retry and method == "POST":
            return self.send_json(app.retry_publish_batch(batch_retry.group(1), user_id))
        if m and m.group(2) == "publish":
            jid = m.group(1)
            if method == "GET":
                return self.send_json({"batches": app.list_publish_batches(jid, user_id)})
            if method == "POST":
                body = self.body()
                return self.send_json(app.create_publish_batch(jid, user_id, body.get("targets"),
                                                                body.get("metadata", {}), user_email), 201)
        if path == "/api/jobs" and method == "GET":
            return self.send_json({"jobs": app.list_jobs(user_id=user_id)})
        if path == "/api/jobs" and method == "POST":
            if not app.limiter.allow("job:" + self.ip(), app.cfg.get("server.job_creations_per_hour", 12), 3600):
                return self.error(429, "Job limit reached for this hour (protects your free API quotas).")
            if not app.llm.available():
                return self.error(400, "Add a free AI key in Settings first.")
            body = self.body()
            workspace_id = body.pop("workspace_id", None)
            if workspace_id:
                membership = app.db.one("SELECT 1 x FROM workspace_members WHERE workspace_id=? AND user_id=? "
                                        "AND role IN ('owner','editor')",
                                        (workspace_id, user_id))
                if not membership:
                    return self.error(403, "You are not a member of this workspace.")
            return self.send_json({"id": app.submit(body, user_id=user_id, workspace_id=workspace_id,
                                                     account_email=user_email)}, 201)
        if m:
            jid, action = m.group(1), m.group(2)
            if not app.get_job(jid, user_id):
                return self.error(404, "No such job.")
            if action is None and method == "GET":
                return self.send_json(app.get_job(jid, user_id))
            if action == "events" and method == "GET":
                after = int((qs.get("after") or ["0"])[0] or 0)
                return self.send_json({"events": app.events_since(jid, after)})
            if action == "stream" and method == "GET":
                return self.stream(jid, int((qs.get("after") or ["0"])[0] or 0))
            if action == "cancel" and method == "POST":
                return self.send_json({"ok": app.cancel(jid)})
            if action == "retry" and method == "POST":
                app.retry(jid)
                return self.send_json({"ok": True})
            if action == "approve" and method == "POST":
                b = self.body()
                app.approve(jid, str(b.get("gate", "")), b.get("edits"))
                return self.send_json({"ok": True})
            if action == "files" and method == "GET":
                return self.send_json({"files": (app.get_job(jid, user_id)["result"].get("deliverables") or [])})
            if action == "versions" and method == "GET":
                return self.send_json({"versions": app.list_versions(jid, user_id)})
            if action == "versions" and method == "POST":
                b = self.body()
                return self.send_json(app.save_version(jid, user_id, b.get("label", "Checkpoint")), 201)
            if action == "review-link" and method == "POST":
                return self.send_json(app.create_review_link(jid, user_id), 201)
            if action == "review-link" and method == "DELETE":
                return self.send_json(app.revoke_review_link(jid, str(qs.get("id", [""])[0]), user_id))
            if action == "preview" and method == "POST":
                return self.send_json(app.create_preview(jid, user_id))
            if action == "timeline" and method == "PUT":
                body = self.body()
                return self.send_json(app.update_timeline(jid, user_id, body.get("timeline")))
            if action == "timeline" and method == "GET":
                if not app.get_job(jid, user_id):
                    return self.error(404, "No such job.")
                try:
                    state = json.loads((app.cfg.jobs_dir / jid / "state.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    state = {}
                return self.send_json({"scenes": [
                    {"id": scene["id"], "heading": scene.get("heading", ""), "text": scene.get("text", ""),
                     "voice_override": scene.get("voice_override", ""), "rate_override": scene.get("rate_override", 0)}
                    for scene in state.get("scenes", [])
                ]})
            if action == "schedule" and method == "POST":
                body = self.body()
                return self.send_json(app.schedule_job(jid, user_id, body.get("scheduled")))
            if action == "repurpose" and method == "POST":
                return self.error(400, "Choose 'Create Shorts from this video' before creating a long video (repurpose option).")
            if action is None and method == "DELETE":
                return self.error(400, "Deleting jobs is done from the data folder (see docs/DATABASE.md).")
        if path == "/api/settings" and method == "GET":
            return self.send_json(app.settings_view())
        if path == "/api/settings" and method == "PUT":
            app.update_settings(self.body(), user_id)
            return self.send_json(app.settings_view())
        if path == "/api/keys" and method == "PUT":
            app.set_keys(self.body().get("keys"), user_id)
            return self.send_json(app.settings_view())
        if path == "/api/brand" and method == "GET":
            return self.send_json(app.brand_get(user_id))
        if path == "/api/brand" and method == "PUT":
            return self.send_json(app.brand_set(self.body(), user_id))
        if path == "/api/ideas" and method == "POST":
            if not app.limiter.allow("ideas:" + self.ip(), 20, 3600):
                return self.error(429, "Idea limit reached for this hour.")
            return self.send_json({"ideas": app.ideas(self.body().get("niche", ""))})
        if path == "/api/analytics" and method == "POST":
            text = self.body().get("csv", "")
            if len(text) > app.cfg.get("server.max_upload_mb", 5) * 1024 * 1024:
                raise ValueError("That file is too large.")
            return self.send_json({"imported": app.analytics_import(text, user_id)})
        if path == "/api/backup" and method == "POST":
            return self.send_json({"file": app.backup().name})
        if path == "/api/audit/export" and method == "GET":
            app.db.audit(user_id, "export_audit", "", user_id)
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(("id", "timestamp", "actor", "action", "detail"))
            for row in app.audit_events(user_id):
                values = [row["id"], row["ts"], row["actor"], row["action"], row["detail"]]
                writer.writerow([
                    "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value
                    for value in values
                ])
            data = output.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="lumina-audit.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.cors()
            self.security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        if path == "/api/token/rotate" and method == "POST":
            tok = app.auth.rotate()
            app.db.audit(user_id, "rotate_token", user_id=user_id)
            return self.send_json({"token": tok})
        if path.startswith("/api/file/") and method == "GET":
            rest = path[len("/api/file/"):]
            jid, _, rel = rest.partition("/")
            if not app.get_job(jid, user_id):
                return self.error(404, "No such job.")
            return self.file(app.deliverable_path(jid, rel))
        if path.startswith("/api/file-link/") and method == "GET":
            rest = path[len("/api/file-link/"):]
            jid, _, rel = rest.partition("/")
            if not app.get_job(jid, user_id):
                return self.error(404, "No such job.")
            app.deliverable_path(jid, rel)
            ticket = app.auth.file_ticket(user_id, jid)
            url = f"/api/file/{urllib.parse.quote(jid)}/{urllib.parse.quote(rel, safe='/')}?ticket={urllib.parse.quote(ticket)}"
            return self.send_json({"url": url})
        return self.error(404, "Unknown endpoint.")

    # --------------------------------------------------------------- streaming
    def stream(self, jid, after, review_token=None):
        app = self.app
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.cors()
        self.security_headers()
        self.end_headers()
        last = after
        idle = 0
        try:
            while True:
                if review_token and not app.review_link_job(review_token):
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                events = app.events_since(jid, last, 200)
                for ev in events:
                    last = ev["id"]
                    self.wfile.write(f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                if events:
                    self.wfile.flush()
                    idle = 0
                    continue
                status = (app.db.one("SELECT status FROM jobs WHERE id=?", (jid,)) or {}).get("status")
                if status in TERMINAL and not (time.sleep(0.2) or app.events_since(jid, last, 1)):
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                app.bus.wait(10)
                idle += 1
                if idle % 2 == 0:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    # ------------------------------------------------------------------ files
    def file(self, p: Path):
        app = self.app
        if not p.is_file():
            return self.error(404, "File not found.")
        size = p.stat().st_size
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        code = 200
        if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            elif m.group(2):
                start = max(size - int(m.group(2)), 0)
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-store")
        if (app.cfg.data / "library").resolve() in p.resolve().parents:
            self.send_header("Content-Disposition", f'attachment; filename="{p.name}"')
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if not ctype.startswith(("video/", "image/")):
            self.send_header("Content-Disposition", f'attachment; filename="{p.name}"')
        self.cors()
        self.security_headers()
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(p, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(1 << 16, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = (WEB / rel).resolve()
        if WEB.resolve() not in full.parents and full != WEB.resolve():
            return self.error(403, "Forbidden.")
        if not full.is_file():
            return self.error(404, "Not found.")
        data = full.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(full.name)[0] or "text/plain") + ("; charset=utf-8" if full.suffix in (".html", ".js", ".css") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob: https:; media-src 'self' blob: https: http:; "
                         "connect-src 'self' http: https:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
        self.security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


def make_server(app, host=None, port=None):
    handler = type("BoundHandler", (Handler,), {"app": app})
    srv = ThreadingHTTPServer((host or app.cfg.get("server.host"), port if port is not None else app.cfg.get("server.port")), handler)
    srv.daemon_threads = True
    return srv
