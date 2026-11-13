"""Build and publish agents: 10 Editor and renderer, 11 SEO, 12 Thumbnail, 18 License, 22 Publishing, 13 QA."""
import re
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from .. import audio, media
from ..render import Renderer, hex_rgb, load_font
from ..sources import OK_LICENSE
from .base import Agent
from .common import STOP, clip, keywords, script_text, word_count
from .planning import lang_name


# ---------------------------------------------------------------------- 10
class EditorRenderer(Agent):
    id, number, title, group, kind = "editor", 10, "Editor and renderer", "Build", "code"
    how = ("Cuts every scene to the exact narration length, adds smooth movement on photos, colour grade, transitions, "
           "captions and chapter titles, renders section by section (low memory, resumable) and adds the mastered audio.")

    def run(self, job, phase=None):
        scenes = job.state["scenes"]
        tl = job.state["timeline"]
        master = job.file("audio", "master.wav")
        if not master.exists():
            raise RuntimeError("The mastered audio is missing; run the audio steps again.")
        burn = job.state.get("captions", {}).get("burn", False)
        job.log(f"Rendering {len(scenes)} scenes in {len(tl['sections'])} section(s) at {job.size[0]}x{job.size[1]}"
                + (", captions burned in." if burn else "."))
        r = Renderer(job)
        video = r.render(tl, {s["id"]: s for s in scenes}, {s["id"]: s["voice"]["words"] for s in scenes}, burn)
        final = job.file("final.mp4")
        media.ffmpeg(["-i", str(video), "-i", str(master), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
                      "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(final)], timeout=1800)
        info = media.probe(final)
        job.log(f"Finished: {info['duration']:.1f}s, {info['width']}x{info['height']}.")
        job.artifact("video", "Final video", {"file": "final.mp4", "duration": round(info["duration"], 1)})
        return {"file": "final.mp4", "duration": info["duration"], "width": info["width"], "height": info["height"]}


# ---------------------------------------------------------------------- 11
POWER = ("secret", "truth", "why", "how", "never", "mistake", "hidden", "real", "actually", "best", "ultimate")


def trim_title(t, n=70):
    t = re.sub(r"\s+", " ", t).strip(" \"'")
    if len(t) <= n:
        return t
    cut = t.rfind(" ", 0, n)
    return t[: cut if cut > 30 else n].rstrip(" ,:-")


def score_title(t, primary):
    s = 0.0
    low = t.lower()
    if primary and primary.lower().split()[0] in low:
        s += 2
    s += 1 if 35 <= len(t) <= 65 else 0
    s += 1 if re.search(r"\d", t) else 0
    s += 0.5 if "?" in t else 0
    s += 0.5 * min(2, sum(1 for w in POWER if w in low))
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        s -= 2
    if t.count("!") > 1:
        s -= 1
    return s


class SEO(Agent):
    id, number, title, group, kind = "seo", 11, "SEO agent", "Publish", "ai+code"
    how = ("Writes five title options, a keyword-rich description, tags and hashtags, then checks them against YouTube's "
           "limits and scores the titles. Chapters and credits are added later by the publishing agent.")

    def run(self, job, phase=None):
        script = job.state["script"]
        trends, research = job.state.get("trends", {}), job.state.get("research", {})
        primary = trends.get("primary") or job.params["topic"]
        an = job.state.get("analytics", {}).get("insights", [])
        out = job.ai("[agent:seo] You are a YouTube SEO specialist. Honest, specific titles; never misleading. "
                     f"Write in {lang_name(job)}.",
                     f"Video topic: {job.params['topic']}\nCurrent title: {script['title']}\nPrimary keyword: {primary}\n"
                     f"Related keywords: {', '.join(trends.get('secondary', [])[:8])}\nAngle: {research.get('angle', '')}\n"
                     f"Format: {'YouTube Short' if job.is_short else 'long video'}\nLessons from past videos: {' '.join(an[:3])}\n"
                     f"Script opening: {clip(script['sections'][0]['narration'], 300)}\n\n"
                     "Return JSON {\"titles\": [5 titles, max 65 characters, keyword near the start], \"description\": "
                     "str (2 to 3 short paragraphs, first 150 characters must contain the primary keyword and hook the viewer), "
                     "\"tags\": [12 to 15 tags], \"hashtags\": [3 hashtags starting with #]}")
        titles = [trim_title(t) for t in out.get("titles", []) if isinstance(t, str) and t.strip()]
        titles = list(dict.fromkeys(titles))
        if len(titles) < 3:
            titles += [trim_title(script["title"]), trim_title(f"{primary}: the full story"), trim_title(f"Everything about {primary}")]
        ranked = sorted(({"text": t, "score": round(score_title(t, primary), 1)} for t in dict.fromkeys(titles)), key=lambda x: -x["score"])
        tags, total = [], 0
        for t in [primary] + [str(x) for x in out.get("tags", [])] + trends.get("secondary", [])[:5]:
            t = re.sub(r"[#<>]", "", str(t)).strip().lower()[:30]
            if t and t not in tags and total + len(t) + 1 <= 450:
                tags.append(t)
                total += len(t) + 1
        hashtags = [h if h.startswith("#") else "#" + h for h in (out.get("hashtags") or [])][:3]
        hashtags = [re.sub(r"\s+", "", h) for h in hashtags]
        if job.is_short and "#shorts" not in [h.lower() for h in hashtags]:
            hashtags = (hashtags[:2] + ["#Shorts"])
        desc = str(out.get("description", "")).strip() or script["sections"][0]["narration"]
        desc = clip(re.sub(r"\n{3,}", "\n\n", desc), 1800)
        res = {"titles": ranked[:5], "best_title": ranked[0]["text"], "description": desc, "tags": tags, "hashtags": hashtags,
               "primary_keyword": primary}
        job.log(f"Best title: {res['best_title']}")
        job.artifact("seo", res["best_title"], {"titles": res["titles"], "tags": tags[:10], "hashtags": hashtags})
        return res


# ---------------------------------------------------------------------- 12
def fit_text(draw, text, font_path, max_w, max_h, start, min_size=28):
    """Largest font size where every line fits the width and all lines fit the height."""
    size = start
    while size >= min_size:
        f = load_font(font_path, size)
        words, lines, cur = text.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if draw.textbbox((0, 0), t, font=f)[2] <= max_w or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        lh = draw.textbbox((0, 0), "Ag", font=f)[3] * 1.12
        widest = max(draw.textbbox((0, 0), ln, font=f)[2] for ln in lines)
        if widest <= max_w and len(lines) * lh <= max_h:
            return f, lines, lh
        size -= 4
    f = load_font(font_path, min_size)
    return f, text.split()[:4], min_size * 1.2


def cover_crop(im, w, h):
    s = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    l, t = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((l, t, l + w, t + h))


class Thumbnail(Agent):
    id, number, title, group, kind = "thumbnail", 12, "Thumbnail agent", "Publish", "ai+code"
    how = ("Takes the sharpest real photos or video frames from your footage, adds a short bold headline written by the "
           "AI, and makes three different designs. Each is a real image under 2 MB, ready to upload.")

    def bases(self, job):
        out = []
        scenes = sorted(job.state["scenes"], key=lambda s: -(s["asset"].get("width", 0) * s["asset"].get("height", 0)))
        for s in scenes:
            a = s["asset"]
            if a["source"] == "generated-card":
                continue
            try:
                if a["kind"] == "video":
                    p = job.file("thumbs", f"base_{s['id']}.jpg")
                    media.extract_frame(a["path"], max(a.get("duration", 2) * 0.4, 0.5), p)
                else:
                    p = Path(a["path"])
                out.append(p)
            except media.MediaError:
                continue
            if len(out) >= 3:
                break
        return out

    def run(self, job, phase=None):
        title = job.state["seo"]["best_title"]
        text = " ".join(title.split()[:4]).upper()
        try:
            out = job.ai("[agent:thumbnail] You write punchy thumbnail headlines.",
                         f"Video title: {title}\nWrite a headline of 2 to 4 words (max 24 characters) in {lang_name(job)}, "
                         "high curiosity, no clickbait lies. Return JSON {\"text\": str}")
            t = str(out.get("text", "")).strip()
            if 2 <= len(t) <= 30:
                text = t.upper()
        except Exception as e:
            job.warn(f"Headline AI skipped ({str(e)[:70]}); using the title words.")
        job.log(f"Thumbnail headline: {text}")
        W, H = (1080, 1920) if job.is_short else (1280, 720)
        accent = hex_rgb(job.state.get("style", {}).get("accent", "FFD23F"))
        font = media.find_font(job.cfg.get("render.font", ""))
        bases = self.bases(job)
        if not bases:
            raise RuntimeError("No usable images for a thumbnail.")
        tdir = job.file("thumbnails")
        tdir.mkdir(exist_ok=True)
        made = []
        for k, name in enumerate(("A", "B", "C")):
            base = bases[k % len(bases)]
            with Image.open(base) as im:
                im = cover_crop(im.convert("RGB"), W, H)
            d = ImageDraw.Draw(im, "RGBA")
            if name == "A":
                im = ImageEnhance.Contrast(im).enhance(1.15)
                grad = Image.new("RGBA", (W, H))
                gd = ImageDraw.Draw(grad)
                for x in range(W):
                    gd.line([(x, 0), (x, H)], fill=(0, 0, 0, int(210 * max(0, 1 - x / (W * 0.85)))))
                im = Image.alpha_composite(im.convert("RGBA"), grad).convert("RGB")
                d = ImageDraw.Draw(im)
                f, lines, lh = fit_text(d, text, font, int(W * 0.55), int(H * 0.7), int(H * 0.2))
                y = (H - lh * len(lines)) / 2
                for ln in lines:
                    d.text((int(W * 0.05), y), ln, font=f, fill=accent, stroke_width=max(3, f.size // 16), stroke_fill=(0, 0, 0))
                    y += lh
            elif name == "B":
                im = ImageEnhance.Color(im).enhance(1.25)
                d = ImageDraw.Draw(im, "RGBA")
                f, lines, lh = fit_text(d, text, font, int(W * 0.84), int(H * 0.36), int(H * 0.16))
                bh = lh * len(lines) + H * 0.05
                y0 = H * 0.5 - bh / 2
                d.rectangle([0, y0, W, y0 + bh], fill=accent + (225,))
                y = y0 + H * 0.025
                for ln in lines:
                    w = d.textbbox((0, 0), ln, font=f)[2]
                    d.text(((W - w) / 2, y), ln, font=f, fill=(15, 15, 15))
                    y += lh
            else:
                im = ImageEnhance.Contrast(ImageEnhance.Color(im).enhance(1.3)).enhance(1.2).filter(ImageFilter.UnsharpMask(2, 80, 3))
                d = ImageDraw.Draw(im)
                f, lines, lh = fit_text(d, text, font, int(W * 0.9), int(H * 0.4), int(H * 0.17))
                y = H * 0.06
                for ln in lines:
                    w = d.textbbox((0, 0), ln, font=f)[2]
                    d.text(((W - w) / 2, y), ln, font=f, fill=(255, 255, 255), stroke_width=max(4, f.size // 12), stroke_fill=(0, 0, 0))
                    y += lh
            path = tdir / f"thumb_{name}.jpg"
            q = 90
            while True:
                im.save(path, "JPEG", quality=q)
                if path.stat().st_size < 1_900_000 or q <= 60:
                    break
                q -= 8
            made.append(f"thumbnails/{path.name}")
        job.artifact("thumbnails", "3 thumbnail options", {"files": made})
        return {"files": made, "text": text, "size": [W, H]}


# ---------------------------------------------------------------------- 18
class LicenseAttribution(Agent):
    id, number, title, group, kind = "license", 18, "License and attribution", "Publish", "code"
    how = ("Keeps a ledger of every photo, clip, music track and reference page used: who made it, where it came from and "
           "under what licence. Writes the credits file and flags anything that needs attribution.")

    def run(self, job, phase=None):
        rows = job.db.q("SELECT * FROM assets WHERE job_id=? ORDER BY id", (job.id,))
        items, seen, issues, need = [], set(), [], []
        for r in rows:
            key = (r["source"], r["asset_id"])
            if key in seen or r["source"] == "generated-card":
                continue
            seen.add(key)
            lic = r["license"] or ""
            ok = bool(OK_LICENSE.match(lic))
            if not ok:
                issues.append(f"{r['source']} {r['asset_id']}: licence '{lic}' not recognised as free to use")
            line = f"\"{clip(r['title'] or 'Untitled', 60)}\" by {r['author'] or 'unknown'} ({r['source'].capitalize()}, {lic}) {r['page_url']}".strip()
            items.append({"type": "footage", "source": r["source"], "license": lic, "line": line})
            if re.search(r"cc[- ]by", lic, re.I):
                need.append(line)
        m = job.state.get("music", {})
        if m.get("file"):
            c = m.get("credit") or {}
            line = f"Music: {c.get('title', m['file'])}" + (f" by {c['artist']}" if c.get("artist") else "") + \
                   (f" ({c['license']})" if c.get("license") else "") + (f" {c['url']}" if c.get("url") else "")
            if m.get("synthetic"):
                line = "Music: generated ambient pad (no licence needed)"
            items.append({"type": "music", "line": line, "license": (c.get("license") or "")})
            if c and re.search(r"cc[- ]by", str(c.get("license", "")), re.I):
                need.append(line)
        for s in job.state.get("research", {}).get("sources", []):
            items.append({"type": "reference", "line": f"Reference: {s['title']} - {s['url']} ({s['license']})", "license": s["license"]})
            need.append(f"Reference: {s['title']} - {s['url']} ({s['license']})")
        lines = [i["line"] for i in items]
        (job.dir / "credits.txt").write_text("CREDITS AND LICENCES\n\n" + "\n".join(f"- {l}" for l in lines) + "\n", encoding="utf-8")
        footage = [i["line"] for i in items if i["type"] == "footage"]
        short_credits = footage[:12] + ([f"...and {len(footage) - 12} more (see credits.txt)"] if len(footage) > 12 else [])
        credits_block = "\n".join(f"- {l}" for l in (short_credits + [i["line"] for i in items if i["type"] != "footage"]))
        job.log(f"Ledger: {len(items)} entries, {len(need)} need attribution, {len(issues)} licence issue(s).")
        job.artifact("licenses", f"{len(items)} credited items", {"attribution_required": need[:10], "issues": issues[:10]})
        return {"entries": len(items), "attribution_required": need, "issues": issues, "credits_block": credits_block}


# ---------------------------------------------------------------------- 22
def fmt_time(t):
    t = int(t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class PublishingPackage(Agent):
    id, number, title, group, kind = "publish", 22, "Publishing package", "Publish", "code"
    how = ("Assembles everything you need for YouTube Studio in one place: final title, description with chapters and "
           "credits, tags, a pinned comment, an upload checklist (including the AI-voice disclosure) and posting advice.")

    def run(self, job, phase=None):
        seo, tl = job.state["seo"], job.state["timeline"]
        title = seo["best_title"]
        chapters = []
        if not job.is_short:
            chapters = [(0, "Introduction")]
            for sec in tl["sections"]:
                if sec["heading"] and sec["start"] - chapters[-1][0] >= 10:
                    chapters.append((sec["start"], clip(sec["heading"], 60)))
            if len(chapters) < 3:
                chapters = []
        chapter_text = "\n".join(f"{fmt_time(t)} {n}" for t, n in chapters)
        lic = job.state.get("license", {})
        comp = job.state.get("compliance", {})
        parts = [seo["description"]]
        if chapter_text:
            parts.append("CHAPTERS\n" + chapter_text)
        parts.append("SOURCES AND CREDITS\n" + (lic.get("credits_block") or "-"))
        parts.append("This video uses an AI-generated voice-over with real stock footage and photos.")
        parts.append(" ".join(seo["hashtags"]))
        desc = clip("\n\n".join(p for p in parts if p.strip()), 4900)
        checklist = [
            "Upload final.mp4 in YouTube Studio → Create → Upload videos.",
            "Paste the title and description from this kit; add the tags under 'Show more'.",
            "Choose a thumbnail from the 'thumbnails' folder" + (" (Shorts: the cover is usually chosen in the app)." if job.is_short else "."),
            "In 'Altered content', answer honestly: the narration is a synthetic voice; footage is real stock footage.",
            "Audience: choose whether the video is made for kids (usually No).",
            "Subtitles: upload the .srt files from the 'captions' folder.",
            "Add the pinned comment after publishing.",
        ]
        if comp.get("risk") in ("medium", "high"):
            checklist.insert(0, f"Review the compliance notes first (risk: {comp['risk']}).")
        pinned = "Which part surprised you the most? Tell me below, and I'll cover the top answer next." if not job.state.get("brand", {}).get("profile", {}).get("cta") \
            else "Which part surprised you the most? Tell us below. " + job.state["brand"]["profile"]["cta"]
        pkg = {"title": title, "title_options": [t["text"] for t in seo["titles"]], "description": desc, "tags": seo["tags"],
               "hashtags": seo["hashtags"], "chapters": [{"time": fmt_time(t), "title": n} for t, n in chapters],
               "pinned_comment": pinned, "playlist": clip(job.params.get("niche") or seo["primary_keyword"], 60),
               "posting_advice": "General guidance: publish when your audience is online (many channels see good results on weekday "
                                 "afternoons or weekend mornings in the audience's time zone). Check YouTube Studio → Audience for your own best times.",
               "checklist": checklist}
        (job.dir / "description.txt").write_text(desc, encoding="utf-8")
        (job.dir / "tags.txt").write_text(", ".join(seo["tags"]), encoding="utf-8")
        md = [f"# Upload kit: {title}", "", "## Title options"] + [f"- {t['text']}" for t in seo["titles"]] + \
             ["", "## Description", "```", desc, "```", "", "## Tags", ", ".join(seo["tags"]), "", "## Pinned comment", pinned,
              "", "## Checklist"] + [f"- [ ] {c}" for c in checklist] + ["", "## Posting advice", pkg["posting_advice"]]
        (job.dir / "upload_kit.md").write_text("\n".join(md), encoding="utf-8")
        job.artifact("publish", "Upload kit ready", {"title": title, "chapters": len(chapters)})
        return pkg


# ---------------------------------------------------------------------- 13
class QA(Agent):
    id, number, title, group, kind = "qa", 13, "Quality assurance", "Publish", "code"
    how = ("Opens the finished file and checks it like a reviewer: length limits, resolution, sound, loudness, subtitles, "
           "thumbnails, credits and policy flags. Anything wrong is listed clearly before you upload.")

    def run(self, job, phase=None):
        checks = []

        def add(name, status, detail):
            checks.append({"name": name, "status": status, "detail": detail})

        final = job.file("final.mp4")
        if not final.exists():
            add("Final video", "fail", "final.mp4 is missing")
            return {"status": "fail", "checks": checks}
        info = media.probe(final)
        add("Playable file", "ok" if info["has_video"] and info["has_audio"] else "fail",
            f"video={info['has_video']} audio={info['has_audio']}")
        add("Length limit", "ok" if info["duration"] <= job.max_seconds else "fail",
            f"{info['duration']:.1f}s of {int(job.max_seconds)}s maximum")
        if job.is_short and info["duration"] > 90:
            add("Shorts limit", "fail", "Shorts must be 90 seconds or less")
        w, h = job.size
        add("Resolution", "ok" if (info["width"], info["height"]) == (w, h) else "fail", f"{info['width']}x{info['height']} (expected {w}x{h})")
        add("Frame rate", "ok" if abs(info["fps"] - job.cfg.get("render.fps", 30)) < 1.5 else "warn", f"{info['fps']:.1f} fps")
        lufs = audio.measure_loudness(final)
        if lufs:
            add("Loudness", "ok" if -18 <= lufs[0] <= -11 else "warn", f"{lufs[0]:.1f} LUFS (target about -14)")
        mean, _ = audio.volume_stats(final)
        add("Voice present", "ok" if mean is not None and mean > -45 else "warn", f"mean volume {mean} dB")
        scenes = job.state["scenes"]
        cards = sum(1 for s in scenes if s["asset"]["source"] == "generated-card")
        add("Footage coverage", "ok" if cards <= max(1, len(scenes) * 0.1) else "warn", f"{cards} title-card scene(s) of {len(scenes)}")
        if any(s["voice"].get("silent") for s in scenes):
            add("Narration", "fail", "No speech engine worked: the video is silent")
        else:
            add("Narration", "ok", scenes[0]["voice"]["provider"] + " voice")
        caps = list(job.file("captions").glob("*.srt"))
        add("Subtitles", "ok" if caps else "warn", f"{len(caps)} file(s)")
        th = list(job.file("thumbnails").glob("*.jpg"))
        big = [p.name for p in th if p.stat().st_size > 2_000_000]
        add("Thumbnails", "ok" if th and not big else "warn", f"{len(th)} option(s)" + (f", too large: {big}" if big else ""))
        lic = job.state.get("license", {})
        add("Credits", "ok" if (job.dir / "credits.txt").exists() and not lic.get("issues") else "warn",
            f"{lic.get('entries', 0)} entries; {len(lic.get('issues', []))} licence issue(s)")
        risk = job.state.get("compliance", {}).get("risk", "low")
        add("Monetization risk", {"low": "ok", "medium": "warn", "high": "fail"}[risk], f"script risk: {risk}")
        pub = job.state.get("publish", {})
        add("Title and tags", "ok" if pub and len(pub["title"]) <= 100 and sum(len(t) + 1 for t in pub["tags"]) <= 500 else "warn",
            f"title {len(pub.get('title', ''))} chars, tags {sum(len(t) + 1 for t in pub.get('tags', []))} chars")
        if not job.is_short:
            want = job.params["minutes"] * 60
            add("Requested length", "ok" if info["duration"] >= want * 0.5 else "warn", f"{info['duration'] / 60:.1f} of {want / 60:.0f} minutes requested")
        worst = "fail" if any(c["status"] == "fail" for c in checks) else ("warn" if any(c["status"] == "warn" for c in checks) else "pass")
        job.artifact("qa", f"Quality check: {worst}", {"checks": checks})
        job.log("QA result: " + worst.upper())
        return {"status": worst, "checks": checks}
