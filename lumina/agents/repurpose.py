"""Agent 21: Repurposing. Turns the best moments of a long video into vertical Shorts."""
import re

from .base import Agent
from .common import clip, sentences
from .planning import score_hook


class Repurposing(Agent):
    id, number, title, group, kind = "repurpose", 21, "Repurposing", "Publish", "ai+code"
    how = ("Finds the strongest 35 to 75 second moments in a long video (a hooky opening, a surprising fact) and creates "
           "new vertical Shorts from them, each with its own captions, title and thumbnail.")
    critical = False

    def windows(self, scenes, timeline):
        out = []
        for i in range(len(scenes)):
            total, j = 0.0, i
            while j < len(scenes) and total < 35:
                total += scenes[j]["voice"]["duration"] + 0.4
                j += 1
            if 35 <= total <= 75 and j - i >= 2:
                first = sentences(scenes[i]["text"])[0] if scenes[i]["text"] else ""
                out.append({"a": i, "b": j, "seconds": round(total, 1), "score": score_hook(first, True), "hook": first})
        return out

    def run(self, job, phase=None):
        n = int(job.params.get("repurpose") or 0)
        if n <= 0 or job.is_short:
            return {"skipped": True}
        scenes, tl = job.state["scenes"], job.state["timeline"]
        wins = self.windows(scenes, tl)
        if not wins:
            job.warn("No suitable 35 to 75 second moments were found.")
            return {"created": []}
        ranked = sorted(wins, key=lambda w: -w["score"])[:14]
        picks = None
        try:
            out = job.ai("[agent:repurpose] You pick the most engaging standalone moments from a documentary for YouTube Shorts.",
                         f"Choose the {n} best moments. Each must make sense alone and open with a strong line.\n" +
                         "\n".join(f"{k}. ({w['seconds']}s) {clip(w['hook'], 110)}" for k, w in enumerate(ranked)) +
                         "\n\nReturn JSON {\"picks\": [int, ...]}")
            picks = [p for p in out.get("picks", []) if isinstance(p, int) and 0 <= p < len(ranked)]
        except Exception as e:
            job.warn(f"AI moment selection skipped: {str(e)[:80]}")
        order = [ranked[p] for p in (picks or [])] + ranked
        chosen, used = [], set()
        for w in order:
            rng = set(range(w["a"], w["b"]))
            if rng & used:
                continue
            chosen.append(w)
            used |= rng
            if len(chosen) >= n:
                break
        created = []
        for k, w in enumerate(chosen, 1):
            ids = [s["id"] for s in scenes[w["a"]:w["b"]]]
            cid = job.app.create_repurpose_job(job, ids, k)
            created.append(cid)
            job.log(f"Queued Short {k}: {w['seconds']}s starting with \"{clip(w['hook'], 70)}\"")
        job.artifact("repurpose", f"{len(created)} Short(s) queued", {"jobs": created})
        return {"created": created}
