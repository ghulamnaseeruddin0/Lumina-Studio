"""Audio agents: 8 Narrator, 9 Music and sound, 20 Audio mastering, 19 Caption and translation."""
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import audio, captions, media
from ..timeline import build_timeline
from ..tts import TTSError
from .base import Agent
from .common import clip
from .planning import lang_name


def rate_to_int(rate):
    m = re.match(r"([+-]?\d+)", str(rate))
    return int(m.group(1)) if m else 0


# ----------------------------------------------------------------------- 8
class Narrator(Agent):
    id, number, title, group, kind = "narrator", 8, "Narrator", "Audio", "code"
    how = ("Turns each scene's text into natural speech with a free neural voice and records the exact moment every word "
           "is spoken. If the total would exceed the length limit it speaks slightly faster, then trims the least important scenes.")

    def synth_all(self, job, rate, base_rate=None):
        scenes = job.state["scenes"]
        nar = job.app.narration
        provs = nar.providers()
        workers = 3 if provs and provs[0] == "edge" else 1
        done = 0
        acceleration = rate_to_int(rate) - rate_to_int(base_rate or rate)

        def one(i_sc):
            i, sc = i_sc
            job.check()
            out = job.file("audio", f"voice_{i:04d}.wav")
            scene_rate = sc.get("rate_override")
            if scene_rate is not None:
                scene_rate = f"{int(scene_rate) + acceleration:+d}%"
            res = nar.synth(sc["text"], out, job.params.get("language", "en"),
                            sc.get("voice_override") or job.params.get("voice", ""), scene_rate or rate)
            return sc, res

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for sc, res in pool.map(one, list(enumerate(scenes))):
                sc["voice"] = {"path": res["path"], "duration": res["duration"], "words": res["words"],
                               "provider": res["provider"], "silent": bool(res.get("silent"))}
                done += 1
                job.progress(100.0 * done / len(scenes), f"Narrated scene {done} of {len(scenes)}")

    def run(self, job, phase=None):
        scenes = job.state["scenes"]
        nar = job.app.narration
        job.log(f"Voice engines available: {', '.join(nar.providers())}.")
        base_rate = job.cfg.get("voice.rate", "-4%")
        try:
            self.synth_all(job, base_rate, base_rate)
        except TTSError as e:
            raise RuntimeError(str(e))
        provs = {s["voice"]["provider"] for s in scenes}
        if "silent" in provs:
            job.warn("No speech engine worked, so the video has no voice. Install edge-tts (pip install edge-tts) and check your internet.")
        limit = job.max_seconds - 0.5
        total = build_timeline(job)["total"]
        job.log(f"Estimated video length: {total:.1f}s (limit {limit:.0f}s).")
        if total > limit and "edge" in provs:
            extra = min(14, int((total / limit - 1) * 100) + 3)
            faster = f"{rate_to_int(base_rate) + extra:+d}%"
            job.warn(f"Slightly over the limit. Re-recording at a faster pace ({faster}).")
            for i, sc in enumerate(scenes):
                Path(sc["voice"]["path"]).unlink(missing_ok=True)
            self.synth_all(job, faster, base_rate)
            total = build_timeline(job)["total"]
        dropped = 0
        while total > limit and len(scenes) > 2:
            victim = len(scenes) - 2  # keep the closing scene
            job.warn(f"Still over the limit: removing scene {victim + 1} ('{clip(scenes[victim]['text'], 50)}').")
            scenes.pop(victim)
            dropped += 1
            total = build_timeline(job)["total"]
        if dropped:
            job.warn(f"{dropped} scene(s) were removed to respect the {int(job.max_seconds)}-second limit.")
        job.state["scenes"] = scenes
        return {"seconds": round(total, 1), "scenes": len(scenes), "dropped": dropped, "providers": sorted(provs)}


# ----------------------------------------------------------------------- 9
class MusicSound(Agent):
    id, number, title, group, kind = "music", 9, "Music and sound", "Audio", "code"
    how = ("Picks a background track that matches the mood from your music folder (or makes a soft ambient bed if "
           "the folder is empty), loops it to the exact length, and adds subtle whoosh sounds at scene changes.")
    critical = False

    def run(self, job, phase=None):
        tl = job.state["timeline"]
        total = tl["total"]
        res = {"music": None, "credit": None, "synthetic": False, "sfx": False}
        if job.params.get("music", True) and job.cfg.get("audio.music", True):
            mood = job.state.get("style", {}).get("mood", "calm")
            src, credit = audio.choose_music(job.cfg.assets / "music", mood, job.id)
            out = job.file("audio", "music.wav")
            audio.music_bed(src, total, out, mood)
            res.update({"music": str(out), "synthetic": src is None,
                        "credit": credit or ({"title": src.name, "artist": "unknown", "license": "user-supplied: verify licence"} if src else None),
                        "file": src.name if src else "synthetic ambient pad"})
            job.log(f"Music: {res['file']}" + (" (add royalty-free tracks to assets/music for richer sound)" if src is None else ""))
        if job.cfg.get("render.sfx", True) and len(tl["sections"]) + len(tl["scenes"]) > 2:
            positions = [s["start"] for s in tl["sections"][1:]] if not job.is_short else [s["start"] for s in tl["scenes"][1:9]]
            if positions:
                wh = audio.make_whoosh(job.file("audio", "whoosh.wav"))
                audio.assemble_sfx(wh, positions, total, job.file("audio", "sfx.wav"), gain=0.3 if job.is_short else 0.4)
                res["sfx"] = True
                job.log(f"Added {len(positions)} soft transition sounds.")
        return res


# ---------------------------------------------------------------------- 20
class AudioMastering(Agent):
    id, number, title, group, kind = "mastering", 20, "Audio mastering", "Audio", "code"
    how = ("Phase 1 cleans every voice clip (rumble, trailing silence, even loudness) and fixes the timeline. Phase 2 lowers "
           "the music whenever the narrator speaks, mixes everything, and sets YouTube's loudness (about -14 LUFS).")

    def run(self, job, phase=None):
        scenes = job.state["scenes"]
        if phase == "clean":
            for i, sc in enumerate(scenes):
                job.check()
                src = Path(sc["voice"]["path"])
                dst = job.file("audio", f"vclean_{i:04d}.wav")
                audio.clean_voice(src, dst)
                dur = media.probe(dst)["duration"]
                sc["voice"].update({"path": str(dst), "duration": dur})
                sc["voice"]["words"] = [w for w in sc["voice"]["words"] if w["s"] < dur] or sc["voice"]["words"][:1]
                for w in sc["voice"]["words"]:
                    w["e"] = min(w["e"], dur)
                job.progress(100.0 * (i + 1) / len(scenes), f"Cleaned voice {i + 1} of {len(scenes)}")
            job.state["timeline"] = build_timeline(job)
            job.log(f"Final timeline: {job.state['timeline']['total']:.1f} seconds.")
            return {"cleaned": len(scenes), "total_seconds": round(job.state["timeline"]["total"], 1)}
        tl = job.state["timeline"]
        items = [(s["voice"]["path"], t["voice_start"]) for s, t in zip(scenes, tl["scenes"])]
        full = audio.assemble_voice(items, tl["total"], job.file("audio", "voice_full.wav"))
        job.log("Voice track assembled on the timeline.")
        music = job.state.get("music", {})
        has_music = bool(music.get("music") and Path(music["music"]).exists())
        sfx = job.file("audio", "sfx.wav") if music.get("sfx") else None
        out = job.file("audio", "master.wav")
        audio.master(full, music.get("music"), sfx, out, job.cfg.get("audio.music_volume", 0.16),
                     job.cfg.get("audio.target_lufs", -14), has_music)
        lufs = audio.measure_loudness(out)
        job.log(f"Mastered. Loudness {lufs[0]:.1f} LUFS, true peak {lufs[1]:.1f} dB." if lufs else "Mastered.")
        return {"master": str(out), "lufs": lufs[0] if lufs else None, "true_peak": lufs[1] if lufs else None}


# ---------------------------------------------------------------------- 19
class CaptionTranslation(Agent):
    id, number, title, group, kind = "captions", 19, "Caption and translation", "Audio", "ai+code"
    how = ("Builds subtitles timed to every spoken word (karaoke-style for Shorts) and exports .srt files. If you chose "
           "other languages, the AI translates the script and makes a subtitle file for each.")

    def run(self, job, phase=None):
        scenes, tl = job.state["scenes"], job.state["timeline"]
        cdir = job.file("captions")
        cdir.mkdir(exist_ok=True)
        cues = []
        for sc, t in zip(scenes, tl["scenes"]):
            cues += captions.scene_cues(t, sc["voice"]["words"])
        main = cdir / f"{job.params.get('language', 'en')}.srt"
        captions.write_srt(cues, main)
        files = [main.name]
        job.log(f"Wrote {len(cues)} subtitle cues.")
        burn = job.params.get("burn_captions")
        if burn is None:
            burn = job.cfg.get("render.burn_captions_short" if job.is_short else "render.burn_captions_long", False)
        for lang in job.params.get("translate", []):
            job.check()
            try:
                texts = self.translate(job, lang)
                tcues = []
                for t, txt in zip(tl["scenes"], texts):
                    tcues += captions.text_cues(t, txt)
                p = cdir / f"{lang}.srt"
                captions.write_srt(tcues, p)
                files.append(p.name)
                job.log(f"Translated subtitles ready: {lang}.")
            except Exception as e:
                job.warn(f"Translation to {lang} failed: {str(e)[:90]}")
        job.artifact("captions", f"{len(files)} subtitle file(s)", {"files": files, "burn_in": bool(burn)})
        return {"files": files, "burn": bool(burn)}

    def translate(self, job, lang):
        from ..config import LANG_NAMES
        scenes = job.state["scenes"]
        out = []
        for g in range(0, len(scenes), 30):
            chunk = scenes[g:g + 30]
            res = job.ai("[agent:translate] You are a professional subtitle translator. Keep meaning and a natural spoken tone.",
                         f"Translate each numbered line into {LANG_NAMES.get(lang, lang)}. Keep the same count.\n" +
                         "\n".join(f"{k + 1}. {s['text']}" for k, s in enumerate(chunk)) +
                         "\n\nReturn JSON {\"lines\": [str, ...]} with exactly one string per input line.")
            lines = res.get("lines") if isinstance(res, dict) else None
            if not isinstance(lines, list) or len(lines) != len(chunk):
                raise ValueError("translation line count did not match")
            out += [str(x) for x in lines]
        return out
