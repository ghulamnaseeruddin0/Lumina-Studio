"""End-to-end tests against fake services. Run: python -m unittest discover -s tests -v"""
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
from lumina import media  # noqa: E402
from lumina.app import App  # noqa: E402
from lumina.job import TERMINAL  # noqa: E402

TMP = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
ROOT = Path(TMP.name)
SRV, PORT, WORLD = fakes.start_world(ROOT / "media")


def wait(app, jid, states=TERMINAL, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        row = app.db.one("SELECT status, error FROM jobs WHERE id=?", (jid,))
        if row["status"] in states:
            return row
        time.sleep(0.4)
    raise AssertionError(f"timeout; last status {row}")


class E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(dir=ROOT))
        cls.cfg = fakes.test_config(cls.tmp, PORT)
        cls.app = App(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.app.stop()

    def job_dir(self, jid):
        return self.cfg.jobs_dir / jid

    def agents_started(self, jid):
        return {e["agent"] for e in self.app.events_since(jid, 0, 5000) if e["type"] == "agent_start"}

    # --------------------------------------------------------------------
    def test_1_short_pipeline(self):
        jid = self.app.submit({"topic": "secrets of the deep sea", "format": "short", "seconds": 60, "translate": ["es"]})
        row = wait(self.app, jid)
        self.assertEqual(row["status"], "done", row)
        d = self.job_dir(jid)
        info = media.probe(d / "final.mp4")
        self.assertTrue(info["has_video"] and info["has_audio"])
        self.assertEqual((info["width"], info["height"]), (540, 960))
        self.assertLessEqual(info["duration"], 90)
        self.assertGreater(info["duration"], 20)
        for f in ("thumbnails/thumb_A.jpg", "thumbnails/thumb_B.jpg", "thumbnails/thumb_C.jpg", "captions/en.srt", "captions/es.srt",
                  "upload_kit.md", "credits.txt", "description.txt", "script.txt"):
            self.assertTrue((d / f).exists(), f)
        started = self.agents_started(jid)
        expected = {"director", "security", "quota", "brand", "analytics", "trends", "research", "script", "hook", "factcheck", "compliance",
                    "shots", "style", "footage", "picker", "narrator", "mastering", "music", "captions", "editor", "seo", "thumbnail",
                    "license", "publish", "qa"}
        self.assertTrue(expected <= started, expected - started)
        qa = json.loads((d / "state.json").read_text())["qa"]
        self.assertNotEqual(qa["status"], "fail", qa)
        self.assertIn("[es]", (d / "captions/es.srt").read_text())
        self.assertIn("pexels", (d / "credits.txt").read_text().lower())
        # brand memory saved the video and the clips
        self.assertGreaterEqual(self.app.db.one("SELECT COUNT(*) n FROM history")["n"], 1)

    def test_2_long_pipeline_with_repurpose(self):
        jid = self.app.submit({"topic": "the deep ocean explained", "format": "long", "minutes": 3, "repurpose": 1})
        row = wait(self.app, jid)
        self.assertEqual(row["status"], "done", row)
        d = self.job_dir(jid)
        info = media.probe(d / "final.mp4")
        self.assertEqual((info["width"], info["height"]), (960, 540))
        self.assertLess(info["duration"], 1800)
        self.assertGreater(info["duration"], 60)
        self.assertIn("CHAPTERS", (d / "description.txt").read_text())
        kids = self.app.db.q("SELECT id FROM jobs WHERE parent=?", (jid,))
        self.assertEqual(len(kids), 1)
        krow = wait(self.app, kids[0]["id"])
        self.assertIn(krow["status"], ("done", "needs_attention"), krow)
        kinfo = media.probe(self.job_dir(kids[0]["id"]) / "final.mp4")
        self.assertEqual((kinfo["width"], kinfo["height"]), (540, 960))
        self.assertLessEqual(kinfo["duration"], 90)

    def test_3_review_gate_and_edit(self):
        jid = self.app.submit({"topic": "pressure in the deep sea", "format": "short", "review_script": True, "review_scenes": True})
        wait(self.app, jid, {"awaiting_review"})
        gate = [e for e in self.app.events_since(jid, 0, 5000) if e["type"] == "gate"][-1]
        self.assertEqual(gate["data"]["gate"], "script")
        script = gate["data"]["script"]
        script["sections"][1]["narration"] = "This sentence was edited by the human reviewer before rendering began."
        self.app.approve(jid, "script", {"script": script})
        wait(self.app, jid, {"awaiting_review"})
        gate2 = [e for e in self.app.events_since(jid, 0, 5000) if e["type"] == "gate"][-1]
        self.assertEqual(gate2["data"]["gate"], "scenes")
        self.assertTrue(gate2["data"]["scenes"][0]["alternates"])
        sc = gate2["data"]["scenes"][1]
        self.app.approve(jid, "scenes", {"swaps": [{"scene_id": sc["id"], "alt": 0}]})
        row = wait(self.app, jid)
        self.assertEqual(row["status"], "done", row)
        self.assertIn("edited by the human reviewer", (self.job_dir(jid) / "script.txt").read_text())

    def test_4_resume_after_failure(self):
        seo = self.app.registry["seo"]
        orig = seo.run
        seo.run = lambda job, phase=None: (_ for _ in ()).throw(RuntimeError("simulated crash"))
        jid = self.app.submit({"topic": "ocean pressure and darkness", "format": "short"})
        row = wait(self.app, jid)
        self.assertEqual(row["status"], "failed")
        self.assertIn("simulated crash", row["error"])
        seo.run = orig
        before = self.app.db.one("SELECT COUNT(*) n FROM events WHERE job_id=? AND type='agent_start' AND agent='research'", (jid,))["n"]
        self.app.retry(jid)
        row = wait(self.app, jid)
        self.assertEqual(row["status"], "done", row)
        after = self.app.db.one("SELECT COUNT(*) n FROM events WHERE job_id=? AND type='agent_start' AND agent='research'", (jid,))["n"]
        self.assertEqual(before, after, "completed steps must not run again")

    def test_5_cancel(self):
        jid = self.app.submit({"topic": "cancel me please soon", "format": "short"})
        time.sleep(0.8)
        self.app.cancel(jid)
        row = wait(self.app, jid)
        self.assertEqual(row["status"], "cancelled")

    def test_6_noncritical_agent_failure_continues(self):
        fakes.FakeWorld.fail_agents = {"trends", "hook", "compliance"}
        self.cfg.set("llm.max_round_wait", 0)
        try:
            jid = self.app.submit({"topic": "resilient pipeline test run", "format": "short"})
            row = wait(self.app, jid)
        finally:
            fakes.FakeWorld.fail_agents = set()
            self.cfg.set("llm.max_round_wait", 90)
        self.assertEqual(row["status"], "done", row)
        warns = [e for e in self.app.events_since(jid, 0, 5000) if e["type"] in ("agent_warn", "warn")]
        self.assertTrue(warns)


if __name__ == "__main__":
    unittest.main()
