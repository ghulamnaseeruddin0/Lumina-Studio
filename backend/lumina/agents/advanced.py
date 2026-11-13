"""Specialist checks and planning agents for audience fit, evidence, and production quality."""
import hashlib
import json
import time

from .base import Agent
from .common import keywords


class AudienceResearcher(Agent):
    id, number, title, group, kind = "audience", 27, "Audience researcher", "Plan", "code"
    how = "Combines your channel profile, search signals, and past performance into a practical audience brief."
    critical = False

    def run(self, job, phase=None):
        profile = (job.state.get("brand") or {}).get("profile", {})
        trends = job.state.get("trends", {})
        analytics = job.state.get("analytics", {})
        audience = profile.get("audience") or trends.get("audience") or "curious general viewers"
        result = {
            "audience": audience,
            "knowledge_level": "beginner-friendly",
            "viewer_need": trends.get("angle") or f"Understand {job.params['topic']} clearly",
            "evidence": (analytics.get("insights") or [])[:3],
        }
        job.log(f"Audience brief prepared for {audience}.")
        return result


class SourceCitation(Agent):
    id, number, title, group, kind = "source_citation", 28, "Source citation", "Guard", "code"
    how = "Maps researched claims to reference URLs and highlights claims without a traceable source."
    critical = False

    def run(self, job, phase=None):
        research = job.state.get("research", {})
        sources = research.get("sources", [])
        citations = []
        uncited = []
        for fact in research.get("facts", []):
            name = str(fact.get("source", "")).strip()
            source = next((s for s in sources if s.get("title", "").casefold() == name.casefold()), None)
            entry = {"claim": fact.get("fact", ""), "source": name, "confidence": fact.get("confidence", "low")}
            if source:
                entry.update({"url": source.get("url", ""), "license": source.get("license", "")})
            else:
                uncited.append(entry["claim"])
            citations.append(entry)
        if uncited:
            job.warn(f"{len(uncited)} researched claim(s) have no matched reference URL; verify them before publishing.")
        return {"items": citations, "uncited_claims": uncited}


class OriginalityReviewer(Agent):
    id, number, title, group, kind = "originality", 29, "Originality reviewer", "Write", "code"
    how = "Compares the new topic and script with your own previous videos to flag repetition."
    critical = False

    def run(self, job, phase=None):
        script = job.state.get("script", {})
        current = set(keywords(" ".join([job.params["topic"], script.get("title", "")]), 12))
        rows = job.db.q("SELECT title, topic FROM history WHERE user_id=? ORDER BY id DESC LIMIT 100", (job.user_id,))
        matches = []
        for row in rows:
            previous = set(keywords(" ".join((row.get("topic") or "", row.get("title") or "")), 12))
            score = len(current & previous) / max(len(current | previous), 1)
            if score >= 0.6:
                matches.append({"title": row.get("title", ""), "similarity": round(score, 2)})
        if matches:
            job.warn("The topic resembles a previous channel video; review the angle for originality.")
        return {"originality_score": round(1 - max((m["similarity"] for m in matches), default=0), 2),
                "similar_videos": matches[:5]}


class StoryStructureEditor(Agent):
    id, number, title, group, kind = "story_structure", 30, "Story structure editor", "Write", "code"
    how = "Checks the script's opening, section flow, and closing before narration is recorded."
    critical = False

    def run(self, job, phase=None):
        sections = (job.state.get("script") or {}).get("sections", [])
        issues = []
        if len(sections) < 3:
            issues.append("The story has fewer than three sections.")
        if sections and len((sections[0].get("narration") or "").split()) > 45:
            issues.append("The opening may take too long to reach its hook.")
        if sections and len((sections[-1].get("narration") or "").split()) > 45:
            issues.append("The closing may benefit from a shorter payoff.")
        if issues:
            job.warn("Story structure review: " + " ".join(issues))
        return {"section_count": len(sections), "issues": issues, "review_required": bool(issues)}


class AccessibilityReviewer(Agent):
    id, number, title, group, kind = "accessibility", 31, "Accessibility reviewer", "Guard", "code"
    how = "Checks subtitle readability and the availability of accessible caption deliverables."
    critical = False

    def run(self, job, phase=None):
        caption_state = job.state.get("captions", {})
        files = caption_state.get("files", [])
        issues = []
        cues = 0
        for name in files:
            path = job.file("captions", name)
            if not path.is_file():
                issues.append(f"Caption file is missing: {name}")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            cues += text.count("-->")
            for line in text.splitlines():
                if len(line.strip()) > 42:
                    issues.append(f"Long subtitle line in {name} may be difficult to read.")
                    break
        if not files:
            issues.append("No caption deliverables were generated.")
        if issues:
            job.warn("Accessibility review: " + " ".join(issues[:3]))
        return {"caption_files": files, "cue_count": cues, "issues": list(dict.fromkeys(issues))}


class LocalizationReviewer(Agent):
    id, number, title, group, kind = "localization", 32, "Localization reviewer", "Publish", "code"
    how = "Verifies translated subtitle deliverables match the requested languages and flags missing files."
    critical = False

    def run(self, job, phase=None):
        requested = list(dict.fromkeys(job.params.get("translate", [])))
        available = set((job.state.get("captions") or {}).get("files", []))
        missing = [lang for lang in requested if f"{lang}.srt" not in available]
        if missing:
            job.warn("Missing translated captions for: " + ", ".join(missing))
        return {"requested": requested, "available": sorted(available), "missing": missing}


class ThumbnailExperimentDesigner(Agent):
    id, number, title, group, kind = "thumbnail_experiment", 33, "Thumbnail experiment designer", "Publish", "code"
    how = "Packages distinct thumbnail candidates as a trackable experiment without changing the selected final image."
    critical = False

    def run(self, job, phase=None):
        thumbnails = job.state.get("thumbnail", {}).get("files", [])
        variants = [{"file": name, "label": f"Concept {i + 1}"} for i, name in enumerate(thumbnails)]
        if variants:
            experiment_id = hashlib.sha256(f"{job.user_id}:{job.id}:thumbnail".encode()).hexdigest()[:24]
            job.db.x("INSERT INTO experiments(id,user_id,job_id,kind,variants,metrics,created) VALUES(?,?,?,?,?,?,?) "
                     "ON CONFLICT(id) DO NOTHING",
                     (experiment_id, job.user_id, job.id, "thumbnail", json.dumps(variants), "{}", time.time()))
        return {"variants": variants, "experiment_id": experiment_id if variants else None}


class ContentCalendarPlanner(Agent):
    id, number, title, group, kind = "calendar", 34, "Content calendar planner", "Plan", "code"
    how = "Creates a draft calendar item for the finished video so you can schedule it from the studio."
    critical = False

    def run(self, job, phase=None):
        title = (job.state.get("publish") or {}).get("title") or job.params.get("topic", "Untitled video")
        calendar_id = hashlib.sha256(f"{job.user_id}:{job.id}:calendar".encode()).hexdigest()[:24]
        job.db.x("INSERT INTO content_calendar(id,user_id,job_id,title,status,metadata) VALUES(?,?,?,?,?,?) "
                 "ON CONFLICT(id) DO NOTHING",
                 (calendar_id, job.user_id, job.id, title, "draft", json.dumps({"format": job.params.get("format")})))
        return {"calendar_id": calendar_id, "title": title, "status": "draft"}


class RightsReleaseAssistant(Agent):
    id, number, title, group, kind = "rights", 35, "Rights and release assistant", "Guard", "code"
    how = "Reviews the footage ledger for missing attribution and licence details before delivery."
    critical = False

    def run(self, job, phase=None):
        entries = job.db.q("SELECT source, asset_id, license, author, page_url, title FROM assets WHERE job_id=?", (job.id,))
        issues = []
        for entry in entries:
            if not entry.get("license") or not entry.get("source") or not entry.get("page_url"):
                issues.append(entry.get("title") or entry.get("asset_id") or "An asset has incomplete rights metadata.")
        if issues:
            job.warn(f"Rights review found {len(issues)} asset(s) needing manual verification.")
        return {"review_required": bool(issues), "issues": issues}


class ProductionCostAnalyst(Agent):
    id, number, title, group, kind = "cost", 36, "Production cost analyst", "Guard", "code"
    how = "Estimates scene count, API demand, and render time before production; actual provider charges depend on your keys."
    critical = False

    def run(self, job, phase=None):
        scenes = max(1, int(job.target_seconds / (5.0 if job.is_short else 10.5)) + 1)
        seconds = int(job.target_seconds)
        estimate = {
            "scenes": scenes,
            "duration_seconds": seconds,
            "estimated_ai_requests": 8 + scenes // 12,
            "estimated_stock_searches": {"pexels": scenes * 2, "pixabay": scenes},
            "estimated_render_minutes": round(max(1, seconds / 30), 1),
            "provider_cost": "Varies by provider and account plan; not charged or guaranteed by Lumina.",
        }
        job.artifact("production_estimate", "Production estimate", estimate)
        return estimate
