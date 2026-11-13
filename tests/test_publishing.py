"""Publishing OAuth, account tokens, durable delivery, and failed-only retries."""
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lumina.app import App
from lumina.config import Config
from lumina.publishing import PublishingError, PublishingService


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(Path(self.tmp.name) / "data", assets_dir=Path(self.tmp.name) / "assets",
                          load_dotenv=False)
        self.key = Fernet.generate_key().decode()
        self.env = patch.dict(os.environ, {
            "LUMINA_OAUTH_ENCRYPTION_KEY": self.key,
            "LUMINA_PUBLIC_URL": "https://studio.example",
            "LUMINA_FRONTEND_URL": "https://app.example",
            "YOUTUBE_CLIENT_ID": "client-id",
            "YOUTUBE_CLIENT_SECRET": "client-secret",
            "RESEND_API_KEY": "test-key",
            "RESEND_FROM_EMAIL": "studio@example.test",
        })
        self.env.start()
        self.app = App(self.cfg, start_worker=False)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_oauth_state_is_single_use_and_tokens_are_encrypted(self):
        def fake_request(url, method="GET", headers=None, body=None, timeout=30):
            if url == "https://oauth2.googleapis.com/token":
                return 200, {}, {"access_token": "plain-access-token", "refresh_token": "refresh",
                                 "expires_in": 3600}
            if url == "https://openidconnect.googleapis.com/v1/userinfo":
                return 200, {}, {"sub": "google-user", "email": "creator@example.test", "name": "Creator"}
            self.fail(f"Unexpected provider request: {url}")

        service = self.app.publishing
        service.http_request = fake_request
        started = service.start_oauth("youtube", "user-a")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(started["authorization_url"]).query)
        state = query["state"][0]
        saved = self.app.db.one("SELECT state_hash,user_id FROM oauth_states")
        self.assertEqual(saved["user_id"], "user-a")
        self.assertNotEqual(saved["state_hash"], state)

        result = service.complete_oauth("youtube", {"state": [state], "code": ["authorization-code"]})
        connection = self.app.db.one("SELECT * FROM platform_connections WHERE user_id=?", ("user-a",))
        self.assertEqual(result["count"], 1)
        self.assertNotIn("plain-access-token", connection["encrypted_tokens"])
        self.assertEqual(service.decrypt_tokens(connection["encrypted_tokens"])["access_token"], "plain-access-token")
        with self.assertRaises(PublishingError):
            service.complete_oauth("youtube", {"state": [state], "code": ["authorization-code"]})

    def test_ready_email_uses_idempotent_outbox(self):
        job_id = self.app.create_job({"topic": "A calm ocean story", "format": "short"},
                                     user_id="user-a", account_email="creator@example.test")
        self.app.db.x("UPDATE jobs SET status='done' WHERE id=?", (job_id,))
        job_dir = self.cfg.jobs_dir / job_id
        (job_dir / "state.json").write_text(json.dumps({"deliverables": ["final.mp4", "script.txt"]}))
        self.app._queue_ready_notification(job_id)
        row = self.app.db.one("SELECT * FROM notification_outbox WHERE job_id=?", (job_id,))
        self.assertEqual(row["recipient"], "creator@example.test")
        self.assertEqual(row["status"], "queued")
        calls = []
        self.app.publishing.send_resend = lambda recipient, subject, body, key: calls.append(
            (recipient, subject, body, key)) or "resend-id"
        self.app._deliver_notification(row["id"])
        self.app._queue_ready_notification(job_id)
        sent = self.app.db.one("SELECT status,provider_id FROM notification_outbox WHERE id=?", (row["id"],))
        self.assertEqual((sent["status"], sent["provider_id"]), ("sent", "resend-id"))
        self.assertEqual(len(calls), 1)
        self.assertIn("review the complete package", calls[0][2])

    def test_publish_batch_retries_failed_destinations_only(self):
        encrypted = Fernet(self.key.encode()).encrypt(json.dumps({"access_token": "unused"}).encode()).decode()
        self.app.db.x(
            "INSERT INTO platform_connections(id,user_id,provider,account_id,account_name,encrypted_tokens,"
            "scopes,metadata,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("conn-youtube", "user-a", "youtube", "account-1", "My channel", encrypted, "youtube.upload",
             "{}", time.time(), time.time()))
        job_id = self.app.create_job({"topic": "A calm ocean story", "format": "short"},
                                     user_id="user-a", account_email="creator@example.test")
        self.app.db.x("UPDATE jobs SET status='done' WHERE id=?", (job_id,))
        (self.cfg.jobs_dir / job_id / "final.mp4").write_bytes(b"mp4 test")
        batch = self.app.create_publish_batch(job_id, "user-a", ["conn-youtube"],
                                              {"title": "Reviewed title"}, "creator@example.test")
        outcomes = iter((PublishingError("Provider unavailable."), {"remote_id": "video-1",
                                                                      "remote_url": "https://youtube.example/video-1"}))
        self.app.publishing.publish_video = lambda *args: next(outcomes)
        self.app._run_publish_batch(batch["id"])
        failed = self.app.db.one("SELECT status,attempts FROM publish_targets WHERE batch_id=?", (batch["id"],))
        self.assertEqual((failed["status"], failed["attempts"]), ("failed", 1))

        retry = self.app.retry_publish_batch(batch["id"], "user-a")
        self.assertEqual(retry["queued"], 1)
        self.app._run_publish_batch(batch["id"])
        complete = self.app.db.one("SELECT status FROM publish_batches WHERE id=?", (batch["id"],))
        published = self.app.db.one("SELECT status,attempts,remote_url FROM publish_targets WHERE batch_id=?",
                                    (batch["id"],))
        self.assertEqual(complete["status"], "completed")
        self.assertEqual((published["status"], published["attempts"]), ("published", 2))
        self.assertEqual(published["remote_url"], "https://youtube.example/video-1")
        emails = self.app.db.q("SELECT idempotency_key FROM notification_outbox WHERE job_id=?", (job_id,))
        self.assertEqual(len(emails), 2)

    def test_temporary_media_ticket_is_expiring_and_scoped(self):
        job_id = self.app.create_job({"topic": "A calm ocean story", "format": "short"},
                                     user_id="user-a")
        self.app.db.x("UPDATE jobs SET status='done' WHERE id=?", (job_id,))
        final = self.cfg.jobs_dir / job_id / "final.mp4"
        final.write_bytes(b"mp4 test")
        url = self.app.public_media_url(job_id, "user-a")
        ticket = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["ticket"][0]
        self.assertEqual(self.app.check_public_media_ticket(ticket, job_id), final)
        self.assertIsNone(self.app.check_public_media_ticket(ticket, "another-job"))
        self.app.db.x("UPDATE public_media_tokens SET expires=? WHERE job_id=?", (time.time() - 1, job_id))
        self.assertIsNone(self.app.check_public_media_ticket(ticket, job_id))


if __name__ == "__main__":
    unittest.main()
