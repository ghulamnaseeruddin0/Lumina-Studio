"""Server tests: auth, validation, path traversal, CORS, rate limits, live stream and video range requests."""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fakes  # noqa: E402
from lumina.app import App  # noqa: E402
from lumina import security  # noqa: E402
from lumina.server import make_server  # noqa: E402

TMP = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
ROOT = Path(TMP.name)
SRV, PORT, WORLD = fakes.start_world(ROOT / "media")


def call(base, method, path, body=None, token=None, headers=None, raw=False):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            return r.status, (data if raw else json.loads(data or b"{}")), dict(r.headers)
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data or b"{}"), dict(e.headers)
        except ValueError:
            return e.code, {}, dict(e.headers)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(dir=ROOT))
        cls.cfg = fakes.test_config(cls.tmp, PORT)
        cls.cfg.set("server.allowed_origins", ["https://my-console.vercel.app"])
        cls.app = App(cls.cfg)
        cls.srv = make_server(cls.app, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.token = cls.app.auth.token

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.app.stop()

    def test_01_auth_and_status(self):
        self.assertEqual(call(self.base, "GET", "/api/health")[0], 200)
        self.assertEqual(call(self.base, "GET", "/api/status")[0], 401)
        self.assertEqual(call(self.base, "GET", "/api/status", token="wrong")[0], 401)
        code, d, _ = call(self.base, "GET", "/api/status", token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual(len(d["agents"]), 36)
        self.assertTrue(d["ffmpeg"])
        # token in the query string also works (needed by <video> tags)
        self.assertEqual(call(self.base, "GET", "/api/status?token=" + self.token)[0], 200)

    def test_publishing_routes_require_auth_and_scope_preferences(self):
        self.app.limiter = security.RateLimiter()
        self.app.auth = security.Auth(self.cfg.data)
        self.assertEqual(call(self.base, "GET", "/api/publishing/capabilities")[0], 401)
        code, data, _ = call(self.base, "GET", "/api/publishing/capabilities", token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual({item["id"] for item in data["platforms"]},
                         {"youtube", "instagram", "facebook", "tiktok"})
        self.assertEqual(data["connections"], [])
        code, data, _ = call(self.base, "PUT", "/api/publishing/preferences",
                             {"notification_email": "creator@example.test"}, self.token)
        self.assertEqual(code, 200)
        self.assertEqual(data["notification_email"], "creator@example.test")
        code, data, _ = call(self.base, "PUT", "/api/publishing/preferences",
                             {"notification_email": "not-an-email"}, self.token)
        self.assertEqual(code, 400)
        code, data, _ = call(self.base, "GET", "/api/publishing/preferences", token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual(data["notification_email"], "creator@example.test")

    def test_temporary_publish_video_url_is_ticket_scoped_and_range_enabled(self):
        jid = self.app.new_id()
        now = time.time()
        self.app.db.x("INSERT INTO jobs(id,topic,format,status,params,created,updated,user_id) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      (jid, "Temporary media access test", "short", "done",
                       json.dumps({"topic": "Temporary media access test", "format": "short"}),
                       now, now, "local"))
        directory = self.cfg.jobs_dir / jid
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "final.mp4").write_bytes(b"0123456789")
        previous_url = self.app.publishing.public_url
        self.app.publishing.public_url = "https://studio.example"
        try:
            media_url = self.app.public_media_url(jid, "local")
        finally:
            self.app.publishing.public_url = previous_url
        path = urllib.parse.urlparse(media_url).path
        query = urllib.parse.urlparse(media_url).query
        code, data, headers = call(self.base, "GET", path + "?" + query,
                                   headers={"Range": "bytes=2-5"}, raw=True)
        self.assertEqual(code, 206)
        self.assertEqual(data, b"2345")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        code, _, _ = call(self.base, "GET", path + "?ticket=invalid", raw=True)
        self.assertEqual(code, 404)

    def test_02_validation(self):
        for bad in ({"topic": "ab"}, {"topic": "see https://evil.example/x"}, {"topic": "x" * 400}, {"topic": "valid topic here", "format": "movie"}):
            code, d, _ = call(self.base, "POST", "/api/jobs", bad, self.token)
            self.assertEqual(code, 400, bad)
            self.assertIn("error", d)
        code, _, _ = call(self.base, "POST", "/api/jobs", "not json", self.token)
        self.assertIn(code, (400,))

    def test_03_path_traversal_and_private_files(self):
        jid = self.app.create_job({"topic": "traversal check topic", "format": "short"})
        self.app.cancel(jid)
        d = self.cfg.jobs_dir / jid
        (d / "final.mp4").write_bytes(b"0123456789" * 100)
        (d / "parts").mkdir(exist_ok=True)
        (d / "parts" / "secret.txt").write_text("x")
        (self.cfg.data / "secrets.json").write_text("{}")
        for rel in ("../../secrets.json", "..%2F..%2Fsecrets.json", "parts/secret.txt", "state.json", "audio/master.wav"):
            code, _, _ = call(self.base, "GET", f"/api/file/{jid}/{rel}", token=self.token)
            self.assertIn(code, (403, 404, 400), rel)
        code, data, hdr = call(self.base, "GET", f"/api/file/{jid}/final.mp4", token=self.token, raw=True)
        self.assertEqual(code, 200)
        self.assertEqual(len(data), 1000)
        # range request (video seeking)
        req = urllib.request.Request(self.base + f"/api/file/{jid}/final.mp4?token={self.token}", headers={"Range": "bytes=10-19"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(r.read(), b"0123456789")

    def test_04_cors(self):
        code, _, _ = call(self.base, "GET", "/api/status", token=self.token, headers={"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        code, _, h = call(self.base, "GET", "/api/status", token=self.token, headers={"Origin": "https://my-console.vercel.app"})
        self.assertEqual(code, 200)
        self.assertEqual(h.get("Access-Control-Allow-Origin"), "https://my-console.vercel.app")
        code, _, h = call(self.base, "OPTIONS", "/api/jobs", headers={"Origin": "https://my-console.vercel.app"})
        self.assertEqual(code, 204)
        self.assertIn("Authorization", h.get("Access-Control-Allow-Headers", ""))

    def test_05_keys_never_returned(self):
        code, d, _ = call(self.base, "PUT", "/api/keys", {"keys": {"GROQ_API_KEY": "gsk_supersecretvalue1234567890abc"}}, self.token)
        self.assertEqual(code, 200)
        self.assertTrue(d["keys"]["GROQ_API_KEY"])
        self.assertNotIn("supersecretvalue", json.dumps(d))
        code, d, _ = call(self.base, "PUT", "/api/keys", {"keys": {"NOT_A_KEY": "x"}}, self.token)
        self.assertEqual(code, 400)
        code, d, _ = call(self.base, "PUT", "/api/keys", {"keys": {"GROQ_API_KEY": ""}}, self.token)
        self.assertFalse(d["keys"]["GROQ_API_KEY"])

    def test_06_brand_analytics_ideas_settings(self):
        code, d, _ = call(self.base, "PUT", "/api/brand", {"channel_name": "Deep Facts", "accent": "#12ab34", "banned_phrases": ["hey guys"]}, self.token)
        self.assertEqual(code, 200)
        self.assertEqual(d["accent"], "12ab34")
        code, d, _ = call(self.base, "PUT", "/api/brand", {"accent": "not-a-colour"}, self.token)
        self.assertEqual(d["accent"], "FFD23F")
        csv = "Video title,Views,Impressions click-through rate (%),Average view duration\nTop 5 ocean facts,1200,6.1,0:45\nOcean overview,300,3.2,0:30\n"
        code, d, _ = call(self.base, "POST", "/api/analytics", {"csv": csv}, self.token)
        self.assertEqual((code, d["imported"]), (200, 2))
        code, d, _ = call(self.base, "POST", "/api/analytics", {"csv": "a,b\n1,2"}, self.token)
        self.assertEqual(code, 400)
        code, d, _ = call(self.base, "POST", "/api/ideas", {"niche": "ocean science"}, self.token)
        self.assertEqual(code, 200)
        self.assertTrue(d["ideas"])
        code, d, _ = call(self.base, "PUT", "/api/settings", {"render.quality": "high", "render.evil": "x", "audio.music_volume": 9}, self.token)
        self.assertEqual(d["render"]["quality"], "high")
        self.assertLessEqual(d["audio"]["music_volume"], 0.6)
        call(self.base, "PUT", "/api/settings", {"render.quality": "draft"}, self.token)

    def test_07_static_and_headers(self):
        req = urllib.request.Request(self.base + "/")
        with urllib.request.urlopen(req) as r:
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        code, _, _ = call(self.base, "GET", "/../lumina/config.py")
        self.assertIn(code, (403, 404))

    def test_08_live_stream_and_completion(self):
        code, d, _ = call(self.base, "POST", "/api/jobs", {"topic": "streaming the deep sea story", "format": "short", "seconds": 30}, self.token)
        self.assertEqual(code, 201)
        jid = d["id"]
        events, t0 = [], time.time()
        req = urllib.request.Request(self.base + f"/api/jobs/{jid}/stream?token={self.token}")
        with urllib.request.urlopen(req, timeout=240) as r:
            ev = None
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("event:"):
                    ev = line[6:].strip()
                    if ev == "end":
                        break
                elif line.startswith("data:") and ev != "end":
                    events.append(json.loads(line[5:]))
                if time.time() - t0 > 240:
                    break
        types = {e["type"] for e in events}
        self.assertIn("agent_start", types)
        self.assertIn("job_done", types)
        agents = [e["agent"] for e in events if e["type"] == "agent_start"]
        self.assertGreaterEqual(len(set(agents)), 20)
        code, job, _ = call(self.base, "GET", f"/api/jobs/{jid}", token=self.token)
        self.assertEqual(job["status"], "done")
        self.assertIn("final.mp4", job["result"]["deliverables"])
        code, vid, h = call(self.base, "GET", f"/api/file/{jid}/final.mp4", token=self.token, raw=True)
        self.assertEqual(code, 200)
        self.assertGreater(len(vid), 10000)

    def test_09_rate_limits_last(self):
        self.cfg.set("server.job_creations_per_hour", 1)
        self.app.limiter._hits.clear()
        call(self.base, "POST", "/api/jobs", {"topic": "first job allowed", "format": "short"}, self.token)
        code, _, _ = call(self.base, "POST", "/api/jobs", {"topic": "second job blocked", "format": "short"}, self.token)
        self.assertEqual(code, 429)
        for _ in range(11):
            call(self.base, "GET", "/api/status", token="bad-token")
        code, _, _ = call(self.base, "GET", "/api/status", token=self.token)
        self.assertEqual(code, 429, "repeated bad tokens must lock the client out")

    def test_08_audit_export(self):
        self.app.db.audit("local", "formula_test", "=1+1", "local")
        code, data, headers = call(self.base, "GET", "/api/audit/export", token=self.token, raw=True)
        self.assertEqual(code, 200)
        self.assertIn("text/csv", headers.get("Content-Type", ""))
        self.assertIn(b"'=1+1", data)
        self.assertIn(b"export_audit", data)

    def test_08_templates_preflight_storage_analytics_and_experiments(self):
        code, template, _ = call(self.base, "POST", "/api/templates", {
            "name": "Short explainer",
            "params": {"format": "short", "seconds": 45, "language": "en", "music": False},
        }, self.token)
        self.assertEqual(code, 201)
        self.assertEqual(template["params"]["seconds"], 45)
        code, listed, _ = call(self.base, "GET", "/api/templates", token=self.token)
        self.assertTrue(any(row["id"] == template["id"] for row in listed["templates"]))
        code, result, _ = call(self.base, "POST", "/api/preflight", {"topic": "preflight example topic", "format": "short"}, self.token)
        self.assertEqual(code, 200)
        self.assertEqual(len(result["checks"]), 5)
        code, storage, _ = call(self.base, "GET", "/api/storage", token=self.token)
        self.assertEqual(code, 200)
        self.assertIn("free_bytes", storage)
        code, analytics, _ = call(self.base, "GET", "/api/analytics", token=self.token)
        self.assertEqual(code, 200)
        self.assertIn("average_ctr", analytics)
        self.app.db.x("INSERT INTO experiments(id,user_id,job_id,kind,variants,metrics,created) VALUES(?,?,?,?,?,?,?)",
                      ("local-exp", "local", "test-job", "thumbnail", "[]", "{}", time.time()))
        code, updated, _ = call(self.base, "PUT", "/api/experiments/local-exp",
                                {"metrics": {"views": 20, "ctr": 4.5}}, self.token)
        self.assertEqual(code, 200)
        self.assertEqual(updated["metrics"]["views"], 20)
        code, _, _ = call(self.base, "DELETE", f"/api/templates/{template['id']}", token=self.token)
        self.assertEqual(code, 200)

    def test_08_workspace_member_management_routes(self):
        code, workspace, _ = call(self.base, "POST", "/api/workspaces", {"name": "Route test"}, self.token)
        self.assertEqual(code, 201)
        invite = self.app.invite_workspace_member(workspace["id"], "local", "route-user@example.com", "editor")
        self.app.accept_workspace_invitation(invite["token"], "route-user", "route-user@example.com")
        code, roster, _ = call(self.base, "GET", f"/api/workspaces/{workspace['id']}/members", token=self.token)
        self.assertEqual(code, 200)
        self.assertEqual({member["user_id"] for member in roster["members"]}, {"local", "route-user"})
        self.assertTrue(roster["can_manage"])
        pending = self.app.invite_workspace_member(workspace["id"], "local", "pending-route@example.com", "reviewer")
        code, result, _ = call(self.base, "DELETE", f"/api/workspace-invitations/{pending['id']}", token=self.token)
        self.assertEqual((code, result["revoked"]), (200, True))
        code, result, _ = call(self.base, "DELETE",
                               f"/api/workspaces/{workspace['id']}/members/route-user", token=self.token)
        self.assertEqual((code, result["removed"]), (200, True))
        code, _, _ = call(self.base, "GET", f"/api/workspaces/{workspace['id']}/members", token=self.token)
        self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main()
