"""Visual agents: 5 Shot planner, 16 Visual style director, 6 Footage finder, 7 Picker."""
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw

from .. import media
from ..config import LANG_NAMES
from ..quota import QuotaExceeded
from ..render import card_png, hex_rgb
from ..sources import StockError, tokens
from .base import Agent
from .common import STOP, clip, keywords, sentences, word_count
from .planning import lang_name

MOODS = ("calm", "dark", "upbeat", "epic", "inspiring", "news")
HISTORY_RX = re.compile(r"\b(history|historical|war|ancient|empire|king|queen|century|medieval|revolution|museum|painting|biograph\w*|dynasty)\b", re.I)


def split_long(sentence, max_words):
    parts = re.split(r"(?<=[,;:])\s+", sentence)
    out, cur = [], ""
    for p in parts:
        if cur and word_count(cur + " " + p) > max_words:
            out.append(cur)
            cur = p
        else:
            cur = (cur + " " + p).strip()
    if cur:
        out.append(cur)
    return out


def make_scenes(script, short):
    target, lo, hi = (11, 5, 22) if short else (26, 12, 44)
    scenes = []
    for si, sec in enumerate(script["sections"]):
        pieces = []
        for st in sentences(sec["narration"]):
            pieces += split_long(st, hi) if word_count(st) > hi else [st]
        cur = ""
        for p in pieces:
            if cur and word_count(cur) >= lo and word_count(cur + " " + p) > target:
                scenes.append((si, cur))
                cur = p
            else:
                cur = (cur + " " + p).strip()
        if cur:
            if scenes and scenes[-1][0] == si and word_count(cur) < lo:
                scenes[-1] = (si, scenes[-1][1] + " " + cur)
            else:
                scenes.append((si, cur))
    return [{"id": f"sc_{i + 1:04d}", "section": si, "text": t} for i, (si, t) in enumerate(scenes)]


def fallback_queries(text, topic):
    kw = keywords(text, 4)
    tk = keywords(topic, 2)
    qs = []
    if kw:
        qs.append(" ".join(kw[:3]))
        qs.append(" ".join(kw[:2] + tk[:1]))
    if tk:
        qs.append(" ".join(tk))
    return list(dict.fromkeys(q for q in qs if q)) or [topic]


# ----------------------------------------------------------------------- 5
class ShotPlanner(Agent):
    id, number, title, group, kind = "shots", 5, "Shot planner", "Visuals", "ai+code"
    how = ("Splits the script into scenes of a few seconds each and, for every scene, decides what the viewer should "
           "see: search terms from specific to general, photo or video, and the mood. These drive the footage search.")

    def run(self, job, phase=None):
        script = job.state["script"]
        scenes = make_scenes(script, job.is_short)
        job.log(f"The script became {len(scenes)} scenes.")
        topic = job.params["topic"]
        batch = len(scenes) if job.is_short else 20
        moods = {}
        for g in range(0, len(scenes), batch):
            job.check()
            chunk = scenes[g:g + batch]
            job.progress(100.0 * g / len(scenes), f"Planning shots {g + 1} to {g + len(chunk)}")
            try:
                out = job.ai("[agent:shots] You are a film editor choosing STOCK FOOTAGE search terms. Use concrete things a camera can "
                             "film (people, places, objects, nature, tools), 2 to 4 words each, in English, no abstract words, "
                             "no real people's names, no brands.",
                             f"Video topic: {topic}\nFor each numbered narration line give 3 search queries ordered specific, broader, "
                             "generic; prefer 'video' (or 'photo' for history, documents, maps, statistics); a mood from "
                             f"{', '.join(MOODS)}.\n\n" + "\n".join(f"{g + k + 1}. {s['text']}" for k, s in enumerate(chunk)) +
                             "\n\nReturn JSON {\"scenes\": [{\"i\": int, \"queries\": [str, str, str], \"prefer\": \"video|photo\", \"mood\": str}]}")
                by_i = {int(x.get("i", 0)): x for x in out.get("scenes", []) if isinstance(x, dict)}
            except Exception as e:
                job.warn(f"AI shot planning failed for part of the script ({str(e)[:80]}); using keyword search terms.")
                by_i = {}
            for k, s in enumerate(chunk):
                item = by_i.get(g + k + 1, {})
                qs = [q for q in item.get("queries", []) if isinstance(q, str) and q.strip()][:3]
                s["queries"] = qs or fallback_queries(s["text"], topic)
                s["prefer"] = "photo" if item.get("prefer") == "photo" else "video"
                m = str(item.get("mood", "")).lower()
                s["mood"] = m if m in MOODS else ""
                if s["mood"]:
                    moods[s["mood"]] = moods.get(s["mood"], 0) + 1
        mood = max(moods, key=moods.get) if moods else ("news" if re.search(r"\bnews|update|breaking\b", topic, re.I) else "calm")
        job.state["scenes"] = scenes
        job.state["mood"] = mood
        job.artifact("shots", f"{len(scenes)} scenes planned", {"scenes": [{"id": s["id"], "text": clip(s["text"], 90), "queries": s["queries"]} for s in scenes[:30]]})
        return {"scenes": len(scenes), "mood": mood}


# ---------------------------------------------------------------------- 16
PRESETS = {
    "calm": {"grade": "eq=contrast=1.03:saturation=1.05:gamma=1.02", "transitions": ["fade", "dissolve"], "zoom": 0.12, "accent": "7FD1B9"},
    "dark": {"grade": "eq=contrast=1.12:saturation=0.85:brightness=-0.03", "transitions": ["fadeblack", "fade"], "zoom": 0.14, "accent": "E4572E"},
    "upbeat": {"grade": "eq=contrast=1.06:saturation=1.25", "transitions": ["slideleft", "slideright", "wipeleft", "fade"], "zoom": 0.18, "accent": "FFD23F"},
    "epic": {"grade": "eq=contrast=1.10:saturation=1.10:gamma=0.98", "transitions": ["fade", "dissolve", "wipeleft"], "zoom": 0.20, "accent": "F4A261"},
    "inspiring": {"grade": "eq=contrast=1.05:saturation=1.15:gamma=1.03", "transitions": ["fade", "dissolve", "slideleft"], "zoom": 0.15, "accent": "FFB703"},
    "news": {"grade": "eq=contrast=1.04:saturation=0.98", "transitions": ["fade", "wipeleft"], "zoom": 0.10, "accent": "4EA8DE"},
}


class VisualStyle(Agent):
    id, number, title, group, kind = "style", 16, "Visual style director", "Visuals", "code"
    how = ("Gives the whole video one consistent look: colour grade, transition style, zoom strength and caption colour, "
           "chosen from the mood of the script and your channel's brand colour.")

    def run(self, job, phase=None):
        mood = job.state.get("mood", "calm")
        style = dict(PRESETS.get(mood, PRESETS["calm"]))
        prof = job.state.get("brand", {}).get("profile", {})
        if prof.get("channel_name") and prof.get("accent"):
            style["accent"] = prof["accent"].lstrip("#")[:6]
        if job.is_short:
            style["zoom"] = style["zoom"] + 0.04
        style.update({"mood": mood, "caption_style": "karaoke" if job.is_short else "classic"})
        job.state["style"] = style
        job.log(f"Style '{mood}': {', '.join(style['transitions'])} transitions, accent #{style['accent']}.")
        job.artifact("style", f"Look: {mood}", {"accent": style["accent"], "transitions": style["transitions"], "grade": style["grade"]})
        return style


# ----------------------------------------------------------------------- 6
class FootageFinder(Agent):
    id, number, title, group, kind = "footage", 6, "Footage finder", "Visuals", "code"
    how = ("Searches the free stock libraries (Pexels, Pixabay, and Wikimedia Commons for history) for real video and "
           "photos matching each scene's search terms. Searches are cached and kept inside each site's free limits.")

    def run(self, job, phase=None):
        scenes = job.state["scenes"]
        orient = "portrait" if job.is_short else "landscape"
        want = job.cfg.get("stock.candidates_per_scene", 8)
        wiki_ok = bool(HISTORY_RX.search(job.params["topic"]))
        empty = 0
        for i, sc in enumerate(scenes):
            job.check()
            if sc.get("candidates"):
                continue
            cands, warns = job.stock.search_all(sc["queries"], orient, sc["prefer"], want=want, allow_wiki=wiki_ok or sc["prefer"] == "photo" and wiki_ok)
            if not cands:
                cands, warns2 = job.stock.search_all([" ".join(keywords(job.params["topic"], 2)) or job.params["topic"]], orient, "video", want=want)
                warns += warns2
            for w in warns[:1]:
                job.warn(w)
            sc["candidates"] = cands[:14]
            if not cands:
                empty += 1
            job.progress(100.0 * (i + 1) / len(scenes), f"Scene {i + 1}/{len(scenes)}: {len(cands)} candidates for '{sc['queries'][0]}'")
            if i % 5 == 0:
                job.save_state()
        if empty == len(scenes):
            raise RuntimeError("No footage was found for any scene. Check your Pexels/Pixabay key in Settings and your internet connection.")
        if empty:
            job.warn(f"{empty} scene(s) had no matching footage; title cards will be used for them.")
        return {"scenes": len(scenes), "without_candidates": empty}


# ----------------------------------------------------------------------- 7
def make_thumb(path, kind, out, width=360):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if kind == "video":
        media.extract_frame(path, 0.8, out, width=width)
    else:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((width, int(width * 1.8)))
            im.save(out, "JPEG", quality=80)
    return out


def descriptor(c):
    return {k: c.get(k) for k in ("source", "id", "kind", "title", "author", "license", "page_url", "width", "height", "duration", "thumb")}


class Picker(Agent):
    id, number, title, group, kind = "picker", 7, "Picker", "Visuals", "ai+code"
    how = ("Scores every candidate on relevance to the scene, shape and resolution, picks the best, avoids repeating clips "
           "(in this video or earlier ones), downloads it and records its licence. Close calls can be settled by the AI.")

    def score(self, job, scene, c, used_job, used_before, last):
        qt = set()
        for q in scene["queries"]:
            qt |= set(tokens(q))
        qt |= set(keywords(scene["text"], 4))
        ct = set(tokens(c.get("tags", "") + " " + c.get("title", "")))
        rel = len(qt & ct) / max(3, min(len(qt), 6)) if qt else 0
        rel = min(rel, 1.0) * (1 - 0.12 * c.get("query_rank", 0))
        w, h = job.size
        target = w / h
        ca = (c.get("width") or w) / max(c.get("height") or h, 1)
        fit = 1 - min(1.0, abs(math.log(max(ca, 0.1) / target)))
        res = min(1.0, max(c.get("width") or 0, c.get("height") or 0) / max(w, h))
        s = rel * 1.6 + fit * 0.35 + res * 0.2
        s += 0.25 if c["kind"] == scene["prefer"] else 0
        if c["kind"] == "video" and c.get("duration", 0) >= 4:
            s += 0.12
        key = (c["source"], c["id"])
        if key in used_job:
            s -= 1.5
        if key in used_before:
            s -= 0.6
        if last and c.get("author") and c.get("author") == last.get("author"):
            s -= 0.2
        if c.get("needs_credit"):
            s -= 0.1
        return s

    def download_one(self, job, scene, ranked):
        h = job.size[1]
        for c in ranked[:3]:
            try:
                path = job.stock.download(c, target_h=max(h, job.size[0]) if job.is_short else h)
                info = media.probe(path) if c["kind"] == "video" else None
                if c["kind"] == "photo":
                    with Image.open(path) as im:
                        wd, ht = im.size
                else:
                    wd, ht = info["width"], info["height"]
                return c, {"path": str(path), "kind": c["kind"], "width": wd, "height": ht,
                           "duration": info["duration"] if info else 0, "source": c["source"], "id": c["id"],
                           "author": c.get("author", ""), "license": c.get("license", ""), "page_url": c.get("page_url", ""),
                           "title": c.get("title", "")}
            except (StockError, media.MediaError, ValueError, OSError) as e:
                job.warn(f"Download failed for {c['source']} {c['id']}: {str(e)[:80]}. Trying the next option.")
        return None, None

    def placeholder(self, job, scene):
        out = job.file("media", f"{scene['id']}_card.png")
        w, h = job.size
        card_png(" ".join(keywords(scene["text"], 4)).title() or "Scene", "", w, h, hex_rgb(job.state.get("style", {}).get("accent", "FFD23F")),
                 media.find_font(job.cfg.get("render.font", "")), out)
        return {"path": str(out), "kind": "photo", "width": w, "height": h, "duration": 0, "source": "generated-card",
                "id": scene["id"], "author": "", "license": "n/a (title card)", "page_url": "", "title": "Title card"}

    def register(self, job, scene):
        a = scene["asset"]
        job.db.x("DELETE FROM assets WHERE job_id=? AND scene_id=?", (job.id, scene["id"]))
        job.db.x("INSERT INTO assets(job_id,scene_id,source,asset_id,kind,page_url,author,author_url,license,title,local_path) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (job.id, scene["id"], a["source"], str(a["id"]), a["kind"], a.get("page_url", ""),
                                                   a.get("author", ""), "", a.get("license", ""), a.get("title", ""), a["path"]))

    def finalize_scene(self, job, scene, cand, asset):
        scene["asset"] = asset
        thumb = job.file("thumbs", f"{scene['id']}.jpg")
        try:
            make_thumb(asset["path"], asset["kind"], thumb)
            scene["thumb"] = f"thumbs/{scene['id']}.jpg"
        except Exception:
            scene["thumb"] = ""
        self.register(job, scene)

    def run(self, job, phase=None):
        scenes = job.state["scenes"]
        used_before = {(r["source"], r["asset_id"]) for r in job.db.q(
            "SELECT source, asset_id FROM used_assets WHERE user_id=?", (job.user_id,))}
        used_job, last, ranked_by = set(), None, {}
        for i, sc in enumerate(scenes):
            ranked = sorted(sc.get("candidates", []), key=lambda c: -self.score(job, sc, c, used_job, used_before, last))
            ranked_by[sc["id"]] = ranked
            if ranked:
                used_job.add((ranked[0]["source"], ranked[0]["id"]))
                last = ranked[0]
        # AI breaks ties in a few batches (kept small to save free-tier requests)
        close = []
        if job.cfg.get("picker_ai", True):
            for i, sc in enumerate(scenes):
                r = ranked_by[sc["id"]]
                if len(r) >= 2:
                    a = self.score(job, sc, r[0], set(), used_before, None)
                    b = self.score(job, sc, r[1], set(), used_before, None)
                    if abs(a - b) < 0.12:
                        close.append(i)
        for g in range(0, min(len(close), 30), 10):
            idxs = close[g:g + 10]
            try:
                lines = []
                for i in idxs:
                    r = ranked_by[scenes[i]["id"]]
                    lines.append(f"{i}. \"{clip(scenes[i]['text'], 110)}\" options: " + " | ".join(f"({k}) {clip(c.get('title') or c.get('tags', ''), 60)}" for k, c in enumerate(r[:3])))
                out = job.ai("[agent:picker] You choose the stock clip whose description best illustrates the narration. Judge by meaning.",
                             "\n".join(lines) + "\n\nReturn JSON {\"picks\": [{\"i\": int, \"pick\": int}]}")
                for p in out.get("picks", []):
                    i, k = p.get("i"), p.get("pick")
                    if i in idxs and isinstance(k, int) and 0 <= k < min(3, len(ranked_by[scenes[i]["id"]])):
                        r = ranked_by[scenes[i]["id"]]
                        r.insert(0, r.pop(k))
                job.log(f"AI settled {len(idxs)} close call(s).")
            except Exception as e:
                job.warn(f"AI tie-break skipped: {str(e)[:80]}")
        missing = 0
        for i, sc in enumerate(scenes):
            job.check()
            if sc.get("asset") and Path(sc["asset"]["path"]).exists():
                continue
            cand, asset = self.download_one(job, sc, ranked_by[sc["id"]])
            if not asset:
                asset, missing = self.placeholder(job, sc), missing + 1
                cand = None
            sc["alternates"] = [descriptor(c) for c in ranked_by[sc["id"]] if not cand or (c["source"], c["id"]) != (cand["source"], cand["id"])][:6]
            self.finalize_scene(job, sc, cand, asset)
            job.progress(100.0 * (i + 1) / len(scenes), f"Picked scene {i + 1}/{len(scenes)}: {asset['source']} {asset['kind']}")
            job.artifact("scene", sc["id"], {"id": sc["id"], "text": clip(sc["text"], 110), "thumb": sc["thumb"],
                                              "source": asset["source"], "author": asset["author"], "kind": asset["kind"]})
            if i % 5 == 0:
                job.save_state()
        job.state["scenes"] = scenes
        return {"picked": len(scenes) - missing, "title_cards": missing}


def swap_asset(job, scene_id, alt_index):
    """Used by the review screen: replace a scene's footage with one of its alternates."""
    sc = next((s for s in job.state["scenes"] if s["id"] == scene_id), None)
    if not sc or not (0 <= alt_index < len(sc.get("alternates", []))):
        raise ValueError("Unknown scene or alternative.")
    alt = sc["alternates"][alt_index]
    full = next((c for c in sc.get("candidates", []) if (c["source"], c["id"]) == (alt["source"], alt["id"])), None)
    if not full:
        raise ValueError("That alternative is no longer available.")
    picker = Picker()
    cand, asset = picker.download_one(job, sc, [full])
    if not asset:
        raise ValueError("Could not download that clip.")
    old = sc.get("asset")
    sc["asset"] = asset
    sc["alternates"] = [a for k, a in enumerate(sc["alternates"]) if k != alt_index] + ([descriptor(old)] if old and old.get("source") != "generated-card" else [])
    picker.finalize_scene(job, sc, cand, asset)
    job.save_state()
    return sc
