"""FFmpeg / FFprobe helpers and font discovery."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"


class MediaError(Exception):
    pass


def run(cmd, cwd=None, timeout=None, check=True):
    try:
        p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise MediaError("FFmpeg was not found. Install FFmpeg and make sure it is on your PATH (see docs/SETUP.md).")
    except subprocess.TimeoutExpired:
        raise MediaError(f"A video step timed out: {' '.join(str(c) for c in cmd[:3])}")
    if check and p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-6:])
        raise MediaError(f"FFmpeg failed: {tail}")
    return p


def ffmpeg(args, cwd=None, timeout=None):
    return run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], cwd=cwd, timeout=timeout)


def probe(path):
    p = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            check=False, timeout=60)
    if p.returncode != 0:
        raise MediaError(f"Not a valid media file: {Path(path).name}")
    d = json.loads(p.stdout or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(d.get("format", {}).get("duration") or (v or {}).get("duration") or (a or {}).get("duration") or 0)
    fps = 0.0
    if v and v.get("r_frame_rate") and "/" in v["r_frame_rate"]:
        n, _, m = v["r_frame_rate"].partition("/")
        fps = float(n) / float(m) if float(m or 0) else 0.0
    return {"duration": dur, "width": int((v or {}).get("width") or 0), "height": int((v or {}).get("height") or 0),
            "has_video": bool(v), "has_audio": bool(a), "fps": fps, "codec": (v or {}).get("codec_name", "")}


def ffmpeg_version():
    try:
        out = run([FFMPEG, "-version"], check=False, timeout=15).stdout
        m = re.search(r"version\s+n?(\d+)\.(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    except MediaError:
        return None


def has_filter(name):
    try:
        out = run([FFMPEG, "-hide_banner", "-filters"], check=False, timeout=20).stdout
        return bool(re.search(rf"\s{re.escape(name)}\s", out))
    except MediaError:
        return False


def extract_frame(video, t, out, width=None):
    args = ["-ss", f"{max(t, 0):.2f}", "-i", str(video), "-frames:v", "1"]
    if width:
        args += ["-vf", f"scale={width}:-2"]
    ffmpeg(args + [str(out)], timeout=60)
    return out


FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
]


def find_font(preferred=""):
    for p in ([preferred] if preferred else []) + FONT_CANDIDATES:
        if p and Path(p).exists():
            return p
    return ""


def ass_font_name(path):
    """Family name for libass: derive from the file when possible, otherwise a safe default."""
    if not path:
        return "Arial"
    n = Path(path).stem.lower()
    if "dejavu" in n:
        return "DejaVu Sans"
    if "liberation" in n:
        return "Liberation Sans"
    if "arial" in n:
        return "Arial"
    if "inter" in n:
        return "Inter"
    if "noto" in n:
        return "Noto Sans"
    return "Arial"
