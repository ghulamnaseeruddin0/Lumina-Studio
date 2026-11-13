"""Subtitles: styled ASS for burning in, plain SRT files (also for translations)."""
import re

SENT_END = ("." , "!", "?", "…", "。", "!", "?", "؟", "۔")


def chunk_words(words, max_words, max_chars):
    chunks, cur = [], []
    for w in words:
        trial = cur + [w]
        too_long = len(trial) > max_words or sum(len(x["w"]) + 1 for x in trial) > max_chars
        if cur and (too_long or cur[-1]["w"].endswith(SENT_END)):
            chunks.append(cur)
            cur = [w]
        else:
            cur = trial
    if cur:
        chunks.append(cur)
    return chunks


def ass_time(t):
    t = max(t, 0)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def srt_time(t):
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _esc(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(events, w, h, short, font="Arial", accent="FFD23F"):
    """events: list of {words:[{w,s,e}]} with times relative to the clip being burned."""
    fs = int(h * (0.046 if short else 0.048))
    mv = int(h * (0.20 if short else 0.07))
    outline = max(3, int(fs * 0.075))
    r, g, b = accent[0:2], accent[2:4], accent[4:6]
    hi = f"&H00{b}{g}{r}&"
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,{font},{fs},&H00FFFFFF,&H000000FF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,"
            f"{int(w * 0.06)},{int(w * 0.06)},{mv},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    max_words, max_chars = (3, 24) if short else (7, 56)
    for ev in events:
        for chunk in chunk_words(ev["words"], max_words, max_chars):
            if short:
                for i, word in enumerate(chunk):
                    start = word["s"]
                    end = chunk[i + 1]["s"] if i + 1 < len(chunk) else word["e"] + 0.08
                    text = " ".join((f"{{\\c{hi}}}{_esc(x['w'])}{{\\r}}" if j == i else _esc(x["w"])) for j, x in enumerate(chunk))
                    lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(max(end, start + 0.05))},Default,,0,0,0,,{text}")
            else:
                text = _esc(" ".join(x["w"] for x in chunk))
                lines.append(f"Dialogue: 0,{ass_time(chunk[0]['s'])},{ass_time(chunk[-1]['e'] + 0.1)},Default,,0,0,0,,{text}")
    return head + "\n".join(lines) + "\n"


def cues_from_words(words, max_chars=42, max_dur=6.0):
    cues, cur = [], []
    for w in words:
        trial = cur + [w]
        chars = sum(len(x["w"]) + 1 for x in trial)
        dur = trial[-1]["e"] - trial[0]["s"]
        if cur and (chars > max_chars * 2 or dur > max_dur or cur[-1]["w"].endswith(SENT_END)):
            cues.append(cur)
            cur = [w]
        else:
            cur = trial
    if cur:
        cues.append(cur)
    return [{"s": c[0]["s"], "e": c[-1]["e"] + 0.05, "text": " ".join(x["w"] for x in c)} for c in cues]


def wrap_cue(text, width=42):
    if len(text) <= width:
        return text
    mid = len(text) // 2
    left = text.rfind(" ", 0, mid + 8)
    right = text.find(" ", mid)
    cut = left if left > 0 and (right < 0 or mid - left <= right - mid) else right
    return text if cut <= 0 else text[:cut] + "\n" + text[cut + 1:]


def write_srt(cues, path):
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{srt_time(c['s'])} --> {srt_time(c['e'])}\n{wrap_cue(c['text'])}\n")
    path.write_text("\n".join(out), encoding="utf-8")


def scene_cues(timeline_scene, words):
    base = timeline_scene["voice_start"]
    shifted = [{"w": x["w"], "s": base + x["s"], "e": base + x["e"]} for x in words]
    return cues_from_words(shifted)


def text_cues(timeline_scene, text, max_chars=42):
    """Cues for translated text: spread across the scene's voice window by character count."""
    parts = [p for p in re.split(r"(?<=[.!?。！？؟۔])\s+", text.strip()) if p]
    pieces = []
    for p in parts:
        while len(p) > max_chars * 2:
            cut = p.rfind(" ", 0, max_chars * 2)
            cut = cut if cut > 0 else max_chars * 2
            pieces.append(p[:cut])
            p = p[cut:].strip()
        if p:
            pieces.append(p)
    total = float(sum(len(p) for p in pieces)) or 1.0
    t, out = timeline_scene["voice_start"], []
    for p in pieces:
        d = timeline_scene["voice_dur"] * len(p) / total
        out.append({"s": t, "e": t + d, "text": p})
        t += d
    return out
