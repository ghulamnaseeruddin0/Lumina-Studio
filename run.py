#!/usr/bin/env python3
"""Lumina Studio launcher.

    python run.py              start the studio (opens your browser)
    python run.py doctor       check that everything is installed and working
    python run.py backup       save a backup of the database
    python run.py token        show your access link again
    python run.py clean        delete cached downloads and temporary render files
"""
import argparse
import os
import shutil
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lumina import __version__, media  # noqa: E402
from lumina.app import App  # noqa: E402
from lumina.config import Config  # noqa: E402
from lumina.server import make_server  # noqa: E402


def doctor(app):
    ok = lambda b: "OK " if b else "FIX"
    ver = media.ffmpeg_version()
    print(f"[{ok(sys.version_info >= (3, 9))}] Python {sys.version.split()[0]} (need 3.9+)")
    print(f"[{ok(bool(ver))}] FFmpeg {'.'.join(map(str, ver)) if ver else 'not found: install it (docs/SETUP.md)'}")
    for f in ("xfade", "zoompan", "sidechaincompress", "loudnorm", "ass"):
        print(f"[{ok(media.has_filter(f))}] FFmpeg filter '{f}'")
    try:
        import PIL
        print(f"[OK ] Pillow {PIL.__version__}")
    except ImportError:
        print("[FIX] Pillow missing: pip install -r requirements.txt")
    try:
        import edge_tts  # noqa: F401
        print("[OK ] edge-tts (free neural voices)")
    except ImportError:
        print("[FIX] edge-tts missing: pip install -r requirements.txt (without it the video has no voice)")
    keys = app.cfg.secret_status()
    print(f"[{ok(keys['GEMINI_API_KEY'] or keys['OPENROUTER_API_KEY'] or keys['GROQ_API_KEY'])}] AI model key (Gemini/OpenRouter/Groq)")
    print(f"[{ok(keys['PEXELS_API_KEY'] or keys['PIXABAY_API_KEY'])}] Stock footage key (Pexels or Pixabay)")
    try:
        text = app.llm.ask("Reply with one word.", "Say OK.", max_tokens=16)
        print(f"[OK ] AI model replied: {text.strip()[:20]}")
    except Exception as e:
        print(f"[FIX] AI model test failed: {str(e)[:100]}")
    free = shutil.disk_usage(app.cfg.data).free / 1e9
    print(f"[{ok(free > 5)}] Free disk space: {free:.1f} GB")
    from lumina import audio
    music = len(audio.scan_music(app.cfg.assets / "music"))
    print(f"[{'OK ' if music else 'tip'}] Music library: {music} file(s) in assets/music (optional, improves sound)")


def main():
    ap = argparse.ArgumentParser(prog="lumina")
    ap.add_argument("cmd", nargs="?", default="serve", choices=["serve", "doctor", "backup", "token", "clean"])
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    cfg = Config()
    app = App(cfg, start_worker=(args.cmd == "serve"))
    host = args.host or cfg.get("server.host")
    port = args.port or (int(os.environ["PORT"]) if os.environ.get("PORT") else None) or cfg.get("server.port")

    if args.cmd == "doctor":
        return doctor(app)
    if args.cmd == "backup":
        print("Backup saved:", app.backup())
        return
    if args.cmd == "token":
        print(f"http://{host}:{port}/#token={app.auth.token}")
        return
    if args.cmd == "clean":
        n = 0
        for sub in ("assets",):
            d = cfg.cache_dir / sub
            if d.exists():
                n += sum(1 for _ in d.glob("*"))
                shutil.rmtree(d, ignore_errors=True)
        for job in cfg.jobs_dir.glob("*/parts"):
            shutil.rmtree(job, ignore_errors=True)
        print(f"Cleaned {n} cached downloads and temporary render files.")
        return

    if host not in ("127.0.0.1", "localhost"):
        print("WARNING: the studio is reachable from other devices. Keep your access token secret and use HTTPS (docs/VERCEL.md).")
    srv = make_server(app, host, port)
    url = f"http://{host}:{port}/#token={app.auth.token}"
    print(f"\nLumina Studio {__version__} is running.\n  Open this link (it contains your private access token):\n  {url}\n  Press Ctrl+C to stop.\n")
    if not args.no_browser and host in ("127.0.0.1", "localhost"):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        app.stop()
        srv.server_close()


if __name__ == "__main__":
    main()
