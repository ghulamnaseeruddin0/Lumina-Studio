"""Audio building blocks: voice timeline, music bed (library or synthetic), sound effects and the final master."""
import hashlib
import json
import re
import wave
from array import array
from pathlib import Path

from . import media

RATE = 24000
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
MOODS = {
    "calm": (110.0, 165.0, 220.0), "dark": (110.0, 130.8, 164.8), "upbeat": (130.8, 164.8, 196.0),
    "epic": (98.0, 146.8, 196.0), "inspiring": (130.8, 196.0, 261.6), "news": (123.5, 185.0, 246.9),
}


def read_pcm(path):
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != RATE:
            raise ValueError(f"{Path(path).name} must be 24 kHz mono 16-bit")
        a = array("h")
        a.frombytes(w.readframes(w.getnframes()))
        return a


def write_pcm(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())


def assemble_voice(items, total_seconds, out):
    """items: [(wav_path, start_seconds)] placed on a silent timeline."""
    buf = array("h", bytes(int(total_seconds * RATE) * 2))
    for path, start in items:
        pcm = read_pcm(path)
        off = int(start * RATE)
        n = min(len(pcm), len(buf) - off)
        if n > 0:
            buf[off:off + n] = pcm[:n]
    write_pcm(out, buf)
    return out


def make_whoosh(path):
    if not Path(path).exists():
        media.ffmpeg(["-f", "lavfi", "-i", "anoisesrc=d=0.7:c=pink:r=24000:a=0.5", "-af",
                      "lowpass=f=1600,highpass=f=200,afade=t=in:d=0.3,afade=t=out:st=0.3:d=0.4", "-ac", "1", str(path)])
    return path


def assemble_sfx(whoosh, positions, total_seconds, out, gain=0.35):
    buf = array("h", bytes(int(total_seconds * RATE) * 2))
    pcm = read_pcm(whoosh)
    for pos in positions:
        off = int(max(pos - 0.25, 0) * RATE)
        for i in range(min(len(pcm), len(buf) - off)):
            v = buf[off + i] + int(pcm[i] * gain)
            buf[off + i] = max(-32768, min(32767, v))
    write_pcm(out, buf)
    return out


def scan_music(music_dir):
    return sorted(p for p in Path(music_dir).rglob("*") if p.suffix.lower() in MUSIC_EXT)


def choose_music(music_dir, mood, seed):
    files = scan_music(music_dir)
    if not files:
        return None, None
    tagged = [f for f in files if mood in (str(f.relative_to(music_dir)).lower())]
    pool = tagged or files
    pick = pool[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(pool)]
    credit = None
    try:
        data = json.loads((Path(music_dir) / "credits.json").read_text(encoding="utf-8"))
        credit = data.get(pick.name)
    except (OSError, ValueError):
        pass
    return pick, credit


def music_bed(src, total, out, mood="calm"):
    """Looped, faded music bed of exactly `total` seconds. src=None creates a soft synthetic ambient pad."""
    fade_out = max(total - 3.0, 0)
    if src:
        media.ffmpeg(["-stream_loop", "-1", "-i", str(src), "-t", f"{total:.2f}", "-af",
                      f"afade=t=in:d=2,afade=t=out:st={fade_out:.2f}:d=3", "-ar", "48000", "-ac", "2", str(out)])
    else:
        f1, f2, f3 = MOODS.get(mood, MOODS["calm"])
        media.ffmpeg(["-f", "lavfi", "-i", f"sine=f={f1}:sample_rate=48000", "-f", "lavfi", "-i", f"sine=f={f2}:sample_rate=48000",
                      "-f", "lavfi", "-i", f"sine=f={f3}:sample_rate=48000", "-filter_complex",
                      f"[0][1][2]amix=inputs=3:duration=first,volume=2.2,lowpass=f=900,tremolo=f=0.15:d=0.5,"
                      f"afade=t=in:d=2,afade=t=out:st={fade_out:.2f}:d=3,aformat=channel_layouts=stereo[a]",
                      "-map", "[a]", "-t", f"{total:.2f}", "-ar", "48000", str(out)])
    return out


def volume_stats(path):
    p = media.run([media.FFMPEG, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"], check=False, timeout=120)
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", p.stderr)
    mx = re.search(r"max_volume:\s*(-?[\d.]+) dB", p.stderr)
    return (float(m.group(1)) if m else None, float(mx.group(1)) if mx else None)


def clean_voice(src, dst, target_mean=-21.0):
    """Remove rumble and trailing silence, then even out loudness between scenes."""
    tmp = Path(str(dst) + ".tmp.wav")
    media.ffmpeg(["-i", str(src), "-af",
                  "highpass=f=70,areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.12,areverse",
                  "-ar", "24000", "-ac", "1", str(tmp)])
    mean, _ = volume_stats(tmp)
    gain = 0.0 if mean is None else max(-12.0, min(12.0, target_mean - mean))
    media.ffmpeg(["-i", str(tmp), "-af", f"volume={gain:.2f}dB,alimiter=limit=0.95", "-ar", "24000", "-ac", "1", str(dst)])
    tmp.unlink(missing_ok=True)
    return gain


def master(voice, music, sfx, out, music_volume=0.16, lufs=-14.0, has_music=True):
    """Duck music under the voice, mix, then normalise loudness for YouTube."""
    inputs = ["-i", str(voice)]
    if has_music and music:
        inputs += ["-i", str(music)]
    if sfx:
        inputs += ["-i", str(sfx)]
    idx = 1
    chains = ["[0:a]highpass=f=70,acompressor=threshold=0.1:ratio=3:attack=15:release=250,aformat=channel_layouts=stereo,asplit=2[voice][vsc]"]
    mix_in = ["[voice]"]
    if has_music and music:
        chains.append(f"[{idx}:a]volume={music_volume}[m0]")
        chains.append("[m0][vsc]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=500[duck]")
        mix_in.append("[duck]")
        idx += 1
    else:
        chains[0] = chains[0].replace("asplit=2[voice][vsc]", "anull[voice]")
    if sfx:
        chains.append(f"[{idx}:a]aformat=channel_layouts=stereo[fx]")
        mix_in.append("[fx]")
    n = len(mix_in)
    tail = f"loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000[out]"
    for mixer in (f"amix=inputs={n}:duration=first:normalize=0", f"amix=inputs={n}:duration=first,volume={n}"):
        fc = ";".join(chains + [f"{''.join(mix_in)}{mixer}[mix]", f"[mix]{tail}"]) if n > 1 else \
             ";".join(chains + [f"[voice]{tail}"])
        try:
            media.ffmpeg([*inputs, "-filter_complex", fc, "-map", "[out]", "-ac", "2", str(out)], timeout=900)
            return out
        except media.MediaError as e:
            last = e
            if n == 1:
                break
    raise last


def measure_loudness(path):
    p = media.run([media.FFMPEG, "-hide_banner", "-i", str(path), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                  check=False, timeout=300)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return float(d["input_i"]), float(d["input_tp"])
    except (ValueError, KeyError):
        return None
