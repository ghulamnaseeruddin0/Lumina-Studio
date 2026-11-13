"""The Director runs specialist agents in order, with checkpoints, review gates and resume."""
import json
import time

from ..job import Cancelled
from ..security import redact
from ..timeline import build_timeline
from .base import Agent

PIPELINE = [
    ("security", "start"), ("quota", None), ("cost", None), ("brand", "load"), ("analytics", None),
    ("audience", None), ("trends", None), ("research", None), ("script", None), ("story_structure", None),
    ("originality", None), ("hook", None), ("factcheck", None), ("compliance", None), ("source_citation", None),
    ("gate", "script"),
    ("shots", None), ("style", None), ("footage", None), ("picker", None),
    ("gate", "scenes"),
    ("narrator", None), ("mastering", "clean"), ("music", None), ("mastering", "mix"), ("captions", None),
    ("accessibility", None), ("localization", None), ("editor", None), ("seo", None), ("thumbnail", None),
    ("thumbnail_experiment", None), ("license", None), ("rights", None), ("security", "final"),
    ("publish", None), ("qa", None), ("repurpose", None), ("calendar", None), ("brand", "save"),
]
REPURPOSE_PIPELINE = [
    ("cost", None), ("timeline", None), ("style", None), ("music", None), ("mastering", "mix"),
    ("captions", None), ("accessibility", None), ("localization", None), ("editor", None), ("seo", None),
    ("thumbnail", None), ("thumbnail_experiment", None), ("license", None), ("rights", None), ("security", "final"),
    ("publish", None), ("qa", None), ("calendar", None), ("brand", "save"),
]


class Paused(Exception):
    pass


class StepFailed(Exception):
    pass


def summarize(res):
    if not isinstance(res, dict):
        return ""
    bits = []
    for k, v in res.items():
        if isinstance(v, (int, float, str, bool)) and len(str(v)) < 40 and k not in ("path", "master"):
            bits.append(f"{k}: {v}")
        if len(bits) >= 4:
            break
    return ", ".join(bits)


class Director(Agent):
    id, number, title, group, kind = "director", 1, "Director", "Plan", "code"
    how = ("The conductor. It runs specialist agents in the right order, saves progress after every step so a crash "
           "never restarts from zero, pauses at your review points, and stops safely if a critical step fails.")

    def __init__(self, registry):
        self.registry = registry

    def pipeline(self, job):
        return REPURPOSE_PIPELINE if job.parent else PIPELINE

    def run(self, job, phase=None):
        pipeline = self.pipeline(job)
        db = job.db
        done = {r["step"]: r["status"] for r in db.q("SELECT step, status FROM steps WHERE job_id=?", (job.id,))}
        job.agent = "director"
        job.emit("agent_start", "Director", {**self.meta(), "phase": "run", "total_steps": len(pipeline)})
        job.log(f"Plan: {len(pipeline)} steps for a {'Short' if job.is_short else 'long video'} on '{job.params['topic']}'.")
        job.set_status("running", progress=0)
        for idx, (aid, phase) in enumerate(pipeline):
            job.check()
            step = f"{aid}:{phase}" if phase else aid
            if done.get(step) in ("done", "skipped"):
                continue
            if aid == "gate":
                self.gate(job, phase, step)
            elif aid == "timeline":
                job.state["timeline"] = build_timeline(job)
                self.mark(job, step, "done", "timeline built")
            else:
                self.step(job, self.registry[aid], phase, step, idx, len(pipeline))
            job.set_status("running", progress=round(100.0 * (idx + 1) / len(pipeline), 1))
        return self.finish(job)

    # ---------------------------------------------------------------- helpers
    def mark(self, job, step, status, summary=""):
        now = time.time()
        job.db.x("INSERT INTO steps(job_id,step,status,started,finished,summary) VALUES(?,?,?,?,?,?) "
                 "ON CONFLICT(job_id,step) DO UPDATE SET status=excluded.status, finished=excluded.finished, summary=excluded.summary",
                 (job.id, step, status, now, now, summary[:300]))

    def step(self, job, agent, phase, step, idx, total):
        job.agent = agent.id
        if not job.app.admin.agent_enabled(agent.id):
            self.mark(job, step, "skipped", "disabled by administrator")
            job.emit("agent_warn", f"{agent.title} was disabled by the application owner.", {"agent": agent.id})
            job.save_state()
            return
        job.emit("agent_start", agent.title, {**agent.meta(), "phase": phase or "", "index": idx + 1, "total_steps": total})
        job.db.x("INSERT INTO steps(job_id,step,status,started) VALUES(?,?,?,?) "
                 "ON CONFLICT(job_id,step) DO UPDATE SET status='running', started=excluded.started", (job.id, step, "running", time.time()))
        t0 = time.time()
        try:
            res = agent.run(job, phase)
        except (Cancelled, Paused):
            raise
        except Exception as e:
            msg = redact(str(e) or type(e).__name__)
            if agent.critical:
                self.mark(job, step, "failed", msg)
                job.emit("agent_error", f"{agent.title} failed: {msg}", {"agent": agent.id})
                raise StepFailed(f"{agent.title}: {msg}")
            self.mark(job, step, "skipped", msg)
            job.emit("agent_warn", f"{agent.title} was skipped: {msg}", {"agent": agent.id})
            job.save_state()
            return
        key = agent.state_key or agent.id
        old = job.state.get(key)
        job.state[key] = {**old, **res} if isinstance(old, dict) and isinstance(res, dict) else (res if res is not None else old)
        job.save_state()
        secs = round(time.time() - t0, 1)
        self.mark(job, step, "done", summarize(res))
        job.emit("agent_done", summarize(res) or "done", {"agent": agent.id, "seconds": secs})

    def gate(self, job, which, step):
        job.agent = "director"
        need = bool(job.params.get("review", {}).get(which))
        reason = "You asked to review this before continuing."
        if which == "script" and job.state.get("compliance", {}).get("block"):
            need, reason = True, "The compliance agent found a HIGH monetization risk. Please review and edit the script."
        if not need:
            self.mark(job, step, "done", "no review requested")
            return
        job.set_status("awaiting_review")
        payload = {"gate": which, "reason": reason}
        if which == "script":
            payload["script"] = job.state["script"]
        else:
            payload["scenes"] = [{"id": s["id"], "text": s["text"], "thumb": s.get("thumb", ""), "kind": s["asset"]["kind"],
                                  "source": s["asset"]["source"], "alternates": s.get("alternates", [])[:5]} for s in job.state["scenes"]]
        job.emit("gate", reason, payload)
        raise Paused()

    def finish(self, job):
        script = job.state.get("script", {})
        (job.dir / "script.txt").write_text(
            f"{script.get('title', '')}\n\n" + "\n\n".join(f"## {s['heading']}\n{s['narration']}" for s in script.get("sections", [])), encoding="utf-8")
        (job.dir / "publish.json").write_text(json.dumps(job.state.get("publish", {}), ensure_ascii=False, indent=2), encoding="utf-8")
        files = ["final.mp4", "script.txt", "description.txt", "tags.txt", "upload_kit.md", "credits.txt", "publish.json"]
        files += [f"thumbnails/{p.name}" for p in sorted(job.file("thumbnails").glob("*.jpg"))]
        files += [f"captions/{p.name}" for p in sorted(job.file("captions").glob("*.srt"))]
        files = [f for f in files if (job.dir / f).exists()]
        qa = job.state.get("qa", {}).get("status", "pass")
        status = "done" if qa != "fail" else "needs_attention"
        job.state["deliverables"] = files
        job.save_state()
        job.emit("job_done", "Your video is ready." if status == "done" else "Finished, but QA found problems to review.",
                 {"files": files, "qa": qa, "title": job.state.get("publish", {}).get("title", "")})
        job.set_status(status, progress=100)  # status flips last so live streams never end before the final event
        return {"status": status}
