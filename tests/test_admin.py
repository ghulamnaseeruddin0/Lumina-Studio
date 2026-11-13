"""Tests for the single-owner administration service."""
import os
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lumina.admin import AdminError, AdminService  # noqa: E402
from lumina.app import App  # noqa: E402
from lumina.config import Config  # noqa: E402
from lumina.server import make_server  # noqa: E402


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.owner = str(uuid.uuid4())
        self.target = str(uuid.uuid4())
        self.environment = patch.dict(os.environ, {
            "SUPABASE_URL": "https://auth.example.test",
            "SUPABASE_ANON_KEY": "public-test-key",
            "LUMINA_SUPER_ADMIN_USER_ID": self.owner,
        })
        self.environment.start()
        self.cfg = Config(Path(self.tmp.name) / "data", assets_dir=Path(self.tmp.name) / "assets",
                          load_dotenv=False)
        self.app = App(self.cfg, start_worker=False)

    def tearDown(self):
        self.app.stop()
        self.environment.stop()
        self.tmp.cleanup()

    def test_owner_is_bootstrapped_as_a_singleton(self):
        stored = self.app.db.one("SELECT user_id FROM super_admin_identity WHERE singleton_id=1")
        self.assertEqual(stored["user_id"], self.owner)
        self.assertTrue(self.app.admin.is_owner(self.owner))
        self.assertEqual(self.app.admin.permissions(self.owner), ["*"])

        with patch.dict(os.environ, {"LUMINA_SUPER_ADMIN_USER_ID": str(uuid.uuid4())}):
            with self.assertRaisesRegex(RuntimeError, "conflicts with the existing singleton owner"):
                AdminService(self.app)

        with patch.dict(os.environ):
            os.environ.pop("LUMINA_SUPER_ADMIN_USER_ID", None)
            self.assertFalse(AdminService(self.app).is_owner(self.owner))

    def test_user_roles_cannot_escalate_or_claim_super_admin(self):
        self.app.admin.update_user(self.owner, self.target, {"role": "viewer"})
        self.assertIn("admin.dashboard.read", self.app.admin.permissions(self.target))
        self.assertNotIn("*", self.app.admin.permissions(self.target))
        with self.assertRaises(PermissionError):
            self.app.admin.update_user(self.target, str(uuid.uuid4()), {"role": "operator"})
        with self.assertRaises(AdminError):
            self.app.admin.update_user(self.owner, self.target, {
                "role": "operator", "permissions": ["*"],
            })
        with self.assertRaises(AdminError):
            self.app.admin.update_user(self.owner, self.owner, {"status": "suspended"})

    def test_support_can_only_change_status_not_roles(self):
        support = str(uuid.uuid4())
        self.app.admin.update_user(self.owner, support, {"role": "support"})
        with patch.object(self.app.admin, "_auth_admin", return_value={}):
            changed = self.app.admin.update_user(support, self.target, {"status": "inactive"})
        self.assertEqual(changed["status"], "inactive")
        self.assertEqual(changed["role"], "member")
        with self.assertRaises(PermissionError):
            self.app.admin.update_user(support, self.target, {"role": "operator"})

    def test_auth_user_invitation_and_confirmed_deletion(self):
        with patch.object(self.app.admin, "_auth_admin", return_value={"user": {"id": self.target}}) as auth_admin:
            invited = self.app.admin.invite_user(self.owner, "new.creator@example.test")
        self.assertTrue(invited["invited"])
        self.assertEqual(invited["id"], self.target)
        auth_admin.assert_called_once()

        with patch.object(self.app.admin, "_auth_admin", return_value={
            "user": {"id": self.target, "email": "new.creator@example.test"},
        }) as auth_admin:
            with self.assertRaisesRegex(AdminError, "exact email"):
                self.app.admin.delete_user(self.owner, self.target, "wrong@example.test")
            auth_admin.assert_called_once()

        with patch.object(self.app.admin, "_auth_admin", side_effect=[
            {"user": {"id": self.target, "email": "new.creator@example.test"}}, {},
        ]) as auth_admin:
            result = self.app.admin.delete_user(self.owner, self.target, "new.creator@example.test")
        self.assertTrue(result["deleted"])
        self.assertTrue(result["application_data_retained"])
        self.assertEqual(auth_admin.call_count, 2)

    def test_integration_status_never_returns_secret_values(self):
        private_values = {
            "SUPABASE_SERVICE_ROLE_KEY": "service-role-private-test",
            "RESEND_API_KEY": "resend-private-test",
            "YOUTUBE_CLIENT_SECRET": "youtube-private-test",
        }
        with patch.dict(os.environ, private_values):
            result = self.app.admin.integrations(self.owner)
        serialized = json.dumps(result)
        for secret in private_values.values():
            self.assertNotIn(secret, serialized)
        self.assertTrue(result["configured"]["SUPABASE_SERVICE_ROLE_KEY"])
        self.assertTrue(result["configured"]["RESEND_API_KEY"])

    def test_account_state_features_and_critical_agent_protection(self):
        with patch.object(self.app.admin, "_auth_admin", return_value={}) as auth_admin:
            self.app.admin.update_user(self.owner, self.target, {"status": "suspended"})
            auth_admin.assert_called_once_with("PUT", f"admin/users/{self.target}",
                                               {"ban_duration": "876000h"})
        self.assertFalse(self.app.admin.identity_allowed(self.target))
        with patch.object(self.app.admin, "_auth_admin", return_value={}) as auth_admin:
            self.app.admin.update_user(self.owner, self.target, {"status": "active"})
            auth_admin.assert_called_once_with("PUT", f"admin/users/{self.target}",
                                               {"ban_duration": "none"})
        self.assertTrue(self.app.admin.identity_allowed(self.target))

        self.app.admin.set_feature(self.owner, "new_video_jobs", False)
        self.assertFalse(self.app.admin.feature_enabled("new_video_jobs"))
        with self.assertRaises(AdminError):
            self.app.admin.set_feature(self.owner, "unknown", True)

        with self.assertRaises(AdminError):
            self.app.admin.update_agent(self.owner, "security", False)
        optional_agent = next(agent for agent in self.app.registry.values() if not agent.critical)
        self.app.admin.update_agent(self.owner, optional_agent.id, False)
        self.assertFalse(self.app.admin.agent_enabled(optional_agent.id))

    def test_admin_routes_enforce_verified_owner_identity_and_account_status(self):
        member = str(uuid.uuid4())
        identities = {
            "owner-token": {"id": self.owner, "email": "owner@example.test", "session_id": "owner-session"},
            "member-token": {"id": member, "email": "member@example.test", "session_id": "member-session"},
        }
        self.app.auth.supabase_identity = lambda token: identities.get(token)
        server = make_server(self.app, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def request(token, method="GET", path="/api/admin/overview", body=None):
            req = urllib.request.Request(base + path,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Authorization": "Bearer " + token,
                                                  "Content-Type": "application/json"},
                                         method=method)
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code

        try:
            self.assertEqual(request("owner-token"), 200)
            self.assertEqual(request("member-token"), 403)
            self.assertEqual(request("owner-token", "POST", "/api/admin/bootstrap", {}), 404)
            self.assertEqual(request("member-token", "PUT", "/api/settings", {"render.quality": "draft"}), 403)
            self.assertEqual(request("member-token", "PUT", "/api/keys",
                                     {"keys": {"GEMINI_API_KEY": "test-key-value"}}), 403)
            self.assertEqual(request("member-token", "POST", "/api/backup"), 403)
            self.assertEqual(request("member-token", "POST", "/api/token/rotate"), 403)
            with patch.object(self.app.admin, "_auth_admin", return_value={}):
                self.app.admin.update_user(self.owner, member, {"status": "suspended"})
            self.assertEqual(request("member-token"), 403)
            self.app.admin.set_feature(self.owner, "new_video_jobs", False)
            self.assertEqual(request("owner-token", "POST", "/api/jobs",
                                     {"topic": "a valid new job topic", "format": "short"}), 503)
            self.app.admin.set_maintenance(self.owner, True)
            self.assertEqual(request("owner-token", "GET", "/api/status"), 503)
            self.assertEqual(request("owner-token", "GET", "/api/admin/overview"), 200)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
