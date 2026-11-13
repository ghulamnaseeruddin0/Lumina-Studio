"""Narration: free text-to-speech with word timings. Order: edge-tts, Piper, eSpeak, silent fallback."""
import asyncio
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import media
from .config import VOICES


class TTSError(Exception):
    pass


def _words(text):
    return re.findall(r"\S+", text)


def spread(text, duration):
    """Estimate word timings by character length when the engine gives none."""
    ws = _words(text)
    if not ws:
        return []
    weights = [max(len(w), 2) for w in ws]
    total = float(sum(weights))
    t, out = 0.0, []
    for w, wt in zip(ws, weights):
        d = duration * wt / total
        out.append({"w": w, "s": round(t, 3), "e": round(t + d, 3)})
        t += d
    return out


def _sentence_to_words(events, text, duration):
    """Convert sentence-level boundaries into per-word timings."""
    out = []
    for ev in events:
        seg = spread(ev["text"], ev["end"] - ev["start"])
        for w in seg:
            out.append({"w": w["w"], "s": round(ev["start"] + w["s"], 3), "e": round(ev["start"] + w["e"], 3)})
    return out or spread(text, duration)


class Narration:
    def __init__(self, cfg):
        self.cfg = cfg

    def providers(self):
        pref = self.cfg.get("voice.provider", "auto")
        order = []
        try:
            import edge_tts  # noqa: F401
            order.append("edge")
        except ImportError:
            pass
        if shutil.which("piper") and self.cfg.get("voice.piper_model"):
            order.append("piper")
        if shutil.which("espeak-ng") or shutil.which("espeak"):
            order.append("espeak")
        order.append("silent")
        if pref == "test":
            return ["test"]
        if pref != "auto" and pref in order:
            return [pref] + [p for p in order if p != pref]
        return order

    def voice_for(self, language, override=""):
        return override or self.cfg.get("voice.voice") or VOICES.get(language, VOICES["en"])

    # ------------------------------------------------------------------ synth
    def synth(self, text, out_wav, language="en", voice="", rate=None, status=None):
        say = status or (lambda s: None)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            raise TTSError("empty narration text")
        rate = rate or self.cfg.get("voice.rate", "-4%")
        last = None
        for prov in self.providers():
            for attempt in range(3 if prov == "edge" else 1):
                try:
                    res = getattr(self, "_" + prov)(text, Path(out_wav), language, self.voice_for(language, voice), rate)
                    res["provider"] = prov
                    return res
                except Exception as e:  # try again / fall through to the next engine
                    last = e
                    if prov == "edge":
                        time.sleep(1.5 * (attempt + 1))
            say(f"voice engine '{prov}' failed ({str(last)[:60]}), trying the next one")
        raise TTSError(f"No voice engine worked: {last}")

    def _finish(self, wav, text, events=None):
        info = media.probe(wav)
        dur = info["duration"]
        words = _sentence_to_words(events, text, dur) if events else spread(text, dur)
        return {"path": str(wav), "duration": dur, "words": words}

    # ---- engines
    def _edge(self, text, wav, language, voice, rate):
        import edge_tts
        mp3 = wav.with_suffix(".mp3")
        events = []

        async def go():
            try:
                comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
            except TypeError:
                comm = edge_tts.Communicate(text, voice, rate=rate)
            with open(mp3, "wb") as fh:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        fh.write(chunk["data"])
                    elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                        s = chunk["offset"] / 1e7
                        events.append({"text": chunk["text"], "start": s, "end": s + chunk["duration"] / 1e7,
                                       "kind": chunk["type"]})

        asyncio.run(asyncio.wait_for(go(), timeout=120))
        if not mp3.exists() or mp3.stat().st_size < 500:
            raise TTSError("edge-tts returned no audio")
        media.ffmpeg(["-i", str(mp3), "-ar", "24000", "-ac", "1", str(wav)])
        mp3.unlink(missing_ok=True)
        dur = media.probe(wav)["duration"]
        if events and events[0]["kind"] == "WordBoundary":
            words = [{"w": e["text"], "s": round(e["start"], 3), "e": round(e["end"], 3)} for e in events]
            return {"path": str(wav), "duration": dur, "words": words}
        return self._finish(wav, text, events if events else None)

    def _piper(self, text, wav, language, voice, rate):
        p = subprocess.run(["piper", "--model", self.cfg.get("voice.piper_model"), "--output_file", str(wav)],
                           input=text, text=True, capture_output=True, timeout=180)
        if p.returncode != 0:
            raise TTSError(p.stderr[-200:])
        return self._finish(wav, text)

    def _espeak(self, text, wav, language, voice, rate):
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        raw = wav.with_suffix(".raw.wav")
        p = subprocess.run([exe, "-v", language, "-s", "150", "-w", str(raw), "--", text], capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise TTSError(p.stderr[-200:])
        media.ffmpeg(["-i", str(raw), "-ar", "24000", "-ac", "1", str(wav)])
        raw.unlink(missing_ok=True)
        return self._finish(wav, text)

    def _silent(self, text, wav, language, voice, rate):
        dur = max(1.0, len(_words(text)) / self.cfg.get("voice.words_per_second", 2.5))
        media.ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{dur:.2f}", str(wav)])
        res = self._finish(wav, text)
        res["silent"] = True
        return res

    def _test(self, text, wav, language, voice, rate):
        dur = max(0.8, len(_words(text)) / 2.6)
        media.ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=24000:duration={dur:.2f}",
                      "-af", "volume=0.2,tremolo=f=5:d=0.6", "-ac", "1", str(wav)])
        return self._finish(wav, text)
