"""Agent 10 engine: turns planned scenes into video with FFmpeg, section by section (low memory, resumable)."""
import hashlib
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import captions, media
from .timeline import INTRO_SECONDS, OUTRO_SECONDS

QUALITY = {"draft": ("ultrafast", 30), "standard": ("veryfast", 22), "high": ("medium", 18)}
TRANSITIONS_OK = {"fade", "dissolve", "slideleft", "slideright", "wipeleft", "wiperight", "fadeblack"}


def hex_rgb(h, default=(255, 210, 63)):
    h = (h or "").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def load_font(path, size):
    try:
        if path:
            return ImageFont.truetype(path, size)
    except OSError:
        pass
    return ImageFont.load_default(size=size)


def compose_still(src, w, h, scale=1.5, out=None):
    """Cover-crop a photo (or blur-pad it when the shape is very different) to scale*(w,h)."""
    tw, th = int(w * scale), int(h * scale)
    with Image.open(src) as im:
        im = im.convert("RGB")
        ratio = (im.width / im.height) / (tw / th)
        if 0.65 <= ratio <= 1.55:
            s = max(tw / im.width, th / im.height)
            im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
            l, t = (im.width - tw) // 2, (im.height - th) // 2
            res = im.crop((l, t, l + tw, t + th))
        else:
            s = max(tw / im.width, th / im.height)
            bg = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.BILINEAR)
            l, t = (bg.width - tw) // 2, (bg.height - th) // 2
            bg = bg.crop((l, t, l + tw, t + th)).filter(ImageFilter.GaussianBlur(28))
            f = min(tw / im.width, th / im.height)
            fg = im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))), Image.LANCZOS)
            bg.paste(fg, ((tw - fg.width) // 2, (th - fg.height) // 2))
            res = bg
    res.save(out, "JPEG", quality=92)
    return out


def lower_third_png(text, w, h, accent, font_path, out):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = load_font(font_path, int(h * 0.042))
    lines = textwrap.wrap(text, width=34)[:2] or [""]
    bw = max(d.textbbox((0, 0), ln, font=f)[2] for ln in lines)
    lh = d.textbbox((0, 0), "Ag", font=f)[3] + 8
    pad = int(h * 0.02)
    x0, y0 = int(w * 0.05), int(h * 0.74)
    d.rectangle([x0, y0, x0 + bw + pad * 3, y0 + lh * len(lines) + pad * 2], fill=(0, 0, 0, 170))
    d.rectangle([x0, y0, x0 + max(6, int(w * 0.005)), y0 + lh * len(lines) + pad * 2], fill=accent + (255,))
    for i, ln in enumerate(lines):
        d.text((x0 + pad * 2, y0 + pad + i * lh), ln, font=f, fill=(255, 255, 255, 255))
    img.save(out)
    return out


def card_png(title, subtitle, w, h, accent, font_path, out):
    img = Image.new("RGB", (w, h), (14, 16, 22))
    d0 = ImageDraw.Draw(img)
    for y in range(h):
        k = y / h
        d0.line([(0, y), (w, y)], fill=(int(14 + 22 * k), int(16 + 20 * k), int(22 + 34 * k)))
    d = ImageDraw.Draw(img)
    ft, fs = load_font(font_path, int(h * 0.07)), load_font(font_path, int(h * 0.035))
    y = h * 0.38
    for ln in textwrap.wrap(title, width=22 if w < h else 28)[:4]:
        bb = d.textbbox((0, 0), ln, font=ft)
        d.text(((w - bb[2]) / 2, y), ln, font=ft, fill=(255, 255, 255))
        y += bb[3] * 1.25
    d.rectangle([w * 0.42, y + 10, w * 0.58, y + 16], fill=accent)
    if subtitle:
        bb = d.textbbox((0, 0), subtitle, font=fs)
        d.text(((w - bb[2]) / 2, y + 40), subtitle, font=fs, fill=(200, 205, 215))
    img.save(out)
    return out


class Renderer:
    def __init__(self, job):
        self.job = job
        self.cfg = job.cfg
        self.w, self.h = job.size
        self.fps = int(self.cfg.get("render.fps", 30))
        q = job.params.get("quality") or self.cfg.get("render.quality", "standard")
        self.preset, self.crf = QUALITY.get(q, QUALITY["standard"])
        self.font = media.find_font(self.cfg.get("render.font", ""))
        self.style = job.state.get("style", {})
        self.accent = hex_rgb(self.style.get("accent", "FFD23F"))
        self.threads = str(self.cfg.get("render.threads", 0) or 0)

    def enc(self):
        return ["-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf), "-pix_fmt", "yuv420p",
                "-r", str(self.fps), "-threads", self.threads, "-an", "-movflags", "+faststart"]

    # ------------------------------------------------------------------ scenes
    def scene_part(self, scene, tl_scene, index):
        job = self.job
        out = job.file("parts", f"scene_{index:04d}.mp4")
        length = tl_scene["len"]
        if out.exists():
            try:
                if abs(media.probe(out)["duration"] - length) < 0.25:
                    return out
            except media.MediaError:
                pass
        a = scene["asset"]
        grade = self.style.get("grade", "eq=contrast=1.04:saturation=1.08")
        w, h = self.w, self.h
        if a["kind"] == "video":
            dur = a.get("duration") or media.probe(a["path"])["duration"]
            start = 0.0
            if dur > length + 1.0:
                start = int(hashlib.sha1(scene["id"].encode()).hexdigest(), 16) % int(dur - length)
            aw, ah = a.get("width") or w, a.get("height") or h
            ratio = (aw / max(ah, 1)) / (w / h)
            if 0.65 <= ratio <= 1.55:
                fc = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={self.fps},{grade},setsar=1[v]"
            else:
                fc = (f"[0:v]split=2[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=24:3[bg];"
                      f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,"
                      f"fps={self.fps},{grade},setsar=1[v]")
            cmd = ["-stream_loop", "-1", "-ss", f"{start:.2f}", "-i", a["path"], "-t", f"{length:.3f}",
                   "-filter_complex", fc, "-map", "[v]", *self.enc(), str(out)]
        else:
            still = job.file("parts", f"still_{index:04d}.jpg")
            compose_still(a["path"], w, h, 1.5, still)
            frames = max(int(length * self.fps), 2)
            zmax = float(self.style.get("zoom", 0.16))
            inc = zmax / frames
            mode = index % 4
            if mode == 0:
                z, x, y = f"min(1+{inc:.6f}*on,{1 + zmax})", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            elif mode == 1:
                z, x, y = f"max({1 + zmax}-{inc:.6f}*on,1)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
            elif mode == 2:
                z, x, y = f"{1 + zmax * 0.8}", f"(iw-iw/zoom)*on/{frames}", "ih/2-(ih/zoom/2)"
            else:
                z, x, y = f"{1 + zmax * 0.8}", f"(iw-iw/zoom)*(1-on/{frames})", "ih/2-(ih/zoom/2)"
            vf = f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={self.fps},{grade},setsar=1"
            cmd = ["-i", str(still), "-t", f"{length:.3f}", "-vf", vf, *self.enc(), str(out)]
        media.ffmpeg(cmd, timeout=900)
        return out

    # ---------------------------------------------------------------- sections
    def section(self, sec, tl_scenes, parts, burn, words_by_scene, heading):
        job = self.job
        n = len(parts)
        T = sec["transition"]
        lens = [s["len"] for s in tl_scenes]
        out = job.file("parts", f"section_{sec['index']:03d}.mp4")
        inputs, fparts = [], []
        for p in parts:
            inputs += ["-i", str(p)]
        trans = self.style.get("transitions") or ["fade"]
        cur = "[0:v]"
        offset_sum = 0.0
        for k in range(1, n):
            offset_sum += lens[k - 1]
            off = offset_sum - k * T
            t = trans[(sec["index"] + k) % len(trans)]
            t = t if t in TRANSITIONS_OK else "fade"
            fparts.append(f"{cur}[{k}:v]xfade=transition={t}:duration={T:.3f}:offset={off:.3f}[x{k}]")
            cur = f"[x{k}]"
        if n == 1:
            fparts.append("[0:v]null[x0]")
            cur = "[x0]"
        sec_len = sec["len"]
        next_in = n
        cwd = job.file("parts")
        if burn:
            ass_name = f"cap_{sec['index']:03d}.ass"
            events = []
            for ts in tl_scenes:
                w_ = words_by_scene.get(ts["id"], [])
                base = ts["voice_start"] - sec["start"]
                events.append({"words": [{"w": x["w"], "s": base + x["s"], "e": base + x["e"]} for x in w_]})
            (cwd / ass_name).write_text(captions.build_ass(
                events, self.w, self.h, job.is_short, media.ass_font_name(self.font), self.style.get("accent", "FFD23F")), encoding="utf-8")
            fparts.append(f"{cur}ass={ass_name}[cap]")
            cur = "[cap]"
        wm = self.cfg.assets / "brand" / "logo.png"
        if self.cfg.get("render.watermark", True) and wm.exists():
            inputs += ["-i", str(wm)]
            fparts.append(f"[{next_in}:v]scale=-1:{int(self.h * 0.07)},format=rgba,colorchannelmixer=aa=0.65[wm]")
            fparts.append(f"{cur}[wm]overlay=W-w-{int(self.w * 0.03)}:{int(self.h * 0.03)}[wmo]")
            cur = "[wmo]"
            next_in += 1
        if heading and not job.is_short:
            lt = lower_third_png(heading, self.w, self.h, self.accent, self.font, cwd / f"lt_{sec['index']:03d}.png")
            inputs += ["-loop", "1", "-framerate", str(self.fps), "-t", "4.5", "-i", lt.name]
            fparts.append(f"[{next_in}:v]format=rgba,fade=t=in:st=0.8:d=0.4:alpha=1,fade=t=out:st=3.8:d=0.5:alpha=1[lt]")
            fparts.append(f"{cur}[lt]overlay=0:0:eof_action=pass[ltv]")
            cur = "[ltv]"
            next_in += 1
        fade = 0.3
        fparts.append(f"{cur}format=yuv420p,fade=t=in:st=0:d={fade},fade=t=out:st={max(sec_len - fade, 0):.3f}:d={fade}[vout]")
        media.ffmpeg([*inputs, "-filter_complex", ";".join(fparts), "-map", "[vout]", "-t", f"{sec_len:.3f}",
                      *self.enc(), str(out)], cwd=str(cwd), timeout=1800)
        return out

    def card(self, name, title, subtitle, seconds):
        png = self.job.file("parts", f"{name}.png")
        card_png(title, subtitle, self.w, self.h, self.accent, self.font, png)
        out = self.job.file("parts", f"{name}.mp4")
        media.ffmpeg(["-loop", "1", "-framerate", str(self.fps), "-i", str(png), "-t", f"{seconds:.2f}", "-vf",
                      f"fade=t=in:st=0:d=0.5,fade=t=out:st={seconds - 0.5:.2f}:d=0.5,format=yuv420p", *self.enc(), str(out)], timeout=300)
        return out

    # ------------------------------------------------------------------ driver
    def render(self, timeline, scenes_by_id, words_by_scene, burn):
        job = self.job
        files = []
        title = job.state["script"]["title"]
        if timeline["intro"]:
            files.append(self.card("intro", title, job.state.get("brand", {}).get("profile", {}).get("channel_name", ""), INTRO_SECONDS))
        total_scenes = len(timeline["scenes"])
        done = 0
        tl_by_id = {t["id"]: t for t in timeline["scenes"]}
        for sec in timeline["sections"]:
            job.check()
            parts, tls = [], []
            for sid in sec["scene_ids"]:
                tl = tl_by_id[sid]
                parts.append(self.scene_part(scenes_by_id[sid], tl, int(sid.split("_")[-1]) if "_" in sid else done))
                tls.append(tl)
                done += 1
                job.progress(100.0 * done / total_scenes, f"Rendered scene {done} of {total_scenes}")
            files.append(self.section(sec, tls, parts, burn, words_by_scene, sec["heading"]))
        if timeline["outro"]:
            files.append(self.card("outro", "Thanks for watching", "Like and subscribe for more", OUTRO_SECONDS))
        lst = job.file("parts", "concat.txt")
        lst.write_text("".join(f"file '{Path(f).name}'\n" for f in files), encoding="utf-8")
        out = job.file("video_only.mp4")
        media.ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-fflags", "+genpts", str(out)], timeout=900)
        return out
