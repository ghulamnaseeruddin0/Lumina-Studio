"""Fake internet for tests: AI model, Pexels, Pixabay, Wikipedia, Wikimedia, autocomplete and media files."""
import json
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SENT = ["The deep ocean hides creatures that live without any sunlight at all.",
        "Some of them make their own light to hunt and to hide from danger.",
        "Pressure down there would crush a submarine built from ordinary steel.",
        "Scientists keep finding new species every time a robot dives deeper.",
        "Warm water rises while cold water sinks, and this slow motion shapes our climate.",
        "Most of this world has never been seen by a single human being."]


_COUNTER = [0]


def words_text(n, offset=0):
    out, i = [], offset
    while sum(len(s.split()) for s in out) < n:
        _COUNTER[0] += 1
        out.append(SENT[i % len(SENT)].rstrip(".") + f" in zone {_COUNTER[0]}.")
        i += 1
    return " ".join(out)


def make_media(folder):
    folder.mkdir(parents=True, exist_ok=True)
    specs = [("vid1.mp4", "640x360", 5), ("vid2.mp4", "360x640", 6), ("vid3.mp4", "1280x720", 8)]
    for name, size, dur in specs:
        p = folder / name
        if not p.exists():
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={dur}",
                            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(p)], check=True)
    for name, size in [("pic1.jpg", "1600x900"), ("pic2.jpg", "900x1400"), ("pic3.jpg", "2000x1000")]:
        p = folder / name
        if not p.exists():
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={size}", "-frames:v", "1", str(p)], check=True)


class FakeWorld(BaseHTTPRequestHandler):
    media_dir = Path(".")
    port = 0
    calls = {}
    fail_agents = set()

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def do_GET(self):
        path = self.path.split("?")[0]
        FakeWorld.calls[path] = FakeWorld.calls.get(path, 0) + 1
        b = self.base()
        if path.startswith("/media/"):
            f = self.media_dir / path.split("/")[-1]
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4" if f.suffix == ".mp4" else "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/pexels/videos/search":
            vids = []
            for i, f in enumerate(["vid1.mp4", "vid2.mp4", "vid3.mp4"] * 2):
                w, h = {"vid1.mp4": (640, 360), "vid2.mp4": (360, 640), "vid3.mp4": (1280, 720)}[f]
                vids.append({"id": 1000 + i, "url": f"https://www.pexels.com/video/ocean-waves-and-deep-sea-{1000 + i}/", "duration": 6,
                             "width": w, "height": h, "image": "", "user": {"name": f"Filmer {i}", "url": "https://pexels.com/@f"},
                             "video_files": [{"width": w, "height": h, "link": f"{b}/media/{f}", "file_type": "video/mp4"}]})
            return self._json({"videos": vids})
        if path == "/pexels/v1/search":
            return self._json({"photos": [{"id": 2000 + i, "url": f"https://www.pexels.com/photo/ocean-{2000 + i}/", "alt": "ocean deep sea waves",
                                           "width": 1600, "height": 900, "photographer": f"Shooter {i}", "photographer_url": "https://pexels.com/@s",
                                           "src": {"large2x": f"{b}/media/pic{i % 3 + 1}.jpg", "medium": ""}} for i in range(4)]})
        if path == "/pixabay/videos/":
            return self._json({"hits": [{"id": 3000 + i, "pageURL": "https://pixabay.com/videos/x", "tags": "ocean, sea, underwater",
                                         "duration": 7, "user": "pixuser", "user_id": 5,
                                         "videos": {"large": {"url": f"{b}/media/vid3.mp4", "width": 1280, "height": 720}}} for i in range(3)]})
        if path == "/pixabay/":
            return self._json({"hits": [{"id": 4000 + i, "pageURL": "https://pixabay.com/photos/x", "tags": "ocean, sea",
                                         "largeImageURL": f"{b}/media/pic1.jpg", "imageWidth": 1600, "imageHeight": 900, "user": "pixuser", "user_id": 5,
                                         "previewURL": ""} for i in range(3)]})
        if path == "/wiki/w/api.php":
            q = self.path
            if "list=search" in q:
                return self._json({"query": {"search": [{"title": "Deep sea"}, {"title": "Ocean"}]}})
            title = "Deep sea" if "Deep" in q or "deep" in q else "Ocean"
            return self._json({"query": {"pages": {"1": {"title": title, "extract": "The deep sea is the lowest layer of the ocean. " * 12 +
                                                         "Pressure rises with depth and sunlight does not reach below about 1000 metres."}}}})
        if path == "/commons":
            return self._json({"query": {"pages": {}}})
        if path == "/ac":
            return self._json(["deep sea", ["deep sea creatures", "deep sea mysteries", "deep sea pressure"]])
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        FakeWorld.calls["llm"] = FakeWorld.calls.get("llm", 0) + 1
        msgs = body.get("messages", [])
        system = msgs[0]["content"] if msgs else ""
        user = msgs[-1]["content"] if msgs else ""
        m = re.search(r"\[agent:(\w+)\]", system)
        agent = m.group(1) if m else "unknown"
        FakeWorld.calls["agent:" + agent] = FakeWorld.calls.get("agent:" + agent, 0) + 1
        if agent in FakeWorld.fail_agents:
            return self._json({"error": {"message": "boom", "code": 400}}, 400)
        text = json.dumps(self.answer(agent, user, msgs))
        self._json({"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 50}})

    def answer(self, agent, user, msgs):
        if agent == "trends":
            return {"primary": "deep sea", "secondary": ["deep sea creatures", "ocean pressure"], "angle": "What lives where light never reaches", "audience": "curious adults"}
        if agent == "research":
            return {"angle": "life without sunlight", "facts": [{"fact": f"Deep sea fact number {i} about pressure and darkness", "source": "Deep sea", "confidence": "high"} for i in range(10)],
                    "key_terms": ["pressure"], "common_misconceptions": []}
        if agent == "script":
            return {"title": "Secrets of the Deep Sea", "sections": [
                {"heading": "Hook", "narration": "What if I told you most of our planet is still unexplored?"},
                {"heading": "Darkness", "narration": words_text(36, 0)}, {"heading": "Pressure", "narration": words_text(36, 2)},
                {"heading": "Life", "narration": words_text(36, 4)}, {"heading": "Closing", "narration": "Follow for more stories from the deep."}]}
        if agent == "script_outline":
            n = int(re.search(r"exactly (\d+) sections", user).group(1))
            return {"title": "The Deep Sea Explained", "sections": [{"heading": f"Part {i + 1}: layer of the deep", "points": ["light", "pressure", "life"]} for i in range(n)]}
        if agent == "script_section":
            m = re.search(r"Target about (\d+) words", user)
            return {"narration": words_text(int(m.group(1)) if m else 60)}
        if agent == "script_tighten":
            return {"sections": [{"heading": "Hook", "narration": "Short hook."}, {"heading": "Body", "narration": words_text(40)}]}
        if agent == "hook":
            return {"hooks": ["Did you know 90 percent of the ocean is dark and unexplored?", "Welcome to this video about the sea.", "The deep sea is strange."]}
        if agent == "factcheck":
            secs = re.findall(r"### SECTION (\d+)\n(.*?)(?=\n### SECTION|\n\nFor each section)", user, re.S)
            return {"sections": [{"i": int(i), "narration": t.strip(), "issues": []} for i, t in secs]}
        if agent == "compliance":
            return {"risk": "low", "concerns": []}
        if agent == "shots":
            lines = re.findall(r"^(\d+)\. ", user, re.M)
            return {"scenes": [{"i": int(i), "queries": ["deep sea creatures", "ocean waves", "water"], "prefer": "video", "mood": "calm"} for i in lines]}
        if agent == "picker":
            idx = re.findall(r"^(\d+)\. ", user, re.M)
            return {"picks": [{"i": int(i), "pick": 1} for i in idx]}
        if agent == "translate":
            lines = re.findall(r"^\d+\. (.*)$", user, re.M)
            return {"lines": ["[es] " + l for l in lines]}
        if agent == "seo":
            return {"titles": ["Secrets of the Deep Sea: 5 Facts You Never Knew", "What Lives in the Deep Ocean?", "Deep Sea Mysteries Explained"],
                    "description": "Deep sea facts that will surprise you. Discover the darkness, the pressure and the life below.\n\nA calm, clear journey.",
                    "tags": ["deep sea", "ocean", "marine biology", "documentary"], "hashtags": ["#deepsea", "#ocean", "#science"]}
        if agent == "thumbnail":
            return {"text": "Deep Sea Secrets"}
        if agent == "repurpose":
            return {"picks": [0, 1]}
        if agent == "ideas":
            return {"ideas": [{"topic": "Why the deep sea glows", "why": "searched often"}]}
        return {}


def start_world(media_dir):
    make_media(media_dir)
    handler = type("H", (FakeWorld,), {"media_dir": media_dir})
    srv = HTTPServer(("127.0.0.1", 0), handler)
    handler.port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, handler.port, handler


def test_config(tmp, port):
    from lumina.config import Config
    b = f"http://127.0.0.1:{port}"
    cfg = Config(tmp / "data", assets_dir=tmp / "assets", load_dotenv=False, overrides={
        "llm": {"providers": [{"name": "fake", "base_url": b + "/llm", "key_env": "GEMINI_API_KEY", "models": ["fake-1"]}]},
        "stock": {"pexels_base": b + "/pexels", "pixabay_base": b + "/pixabay", "wikimedia_base": b + "/commons",
                  "wikipedia_base": b + "/wiki", "autocomplete_url": b + "/ac"},
        "allow_local_sources": True,
        "voice": {"provider": "test"},
        "render": {"quality": "draft", "fps": 24},
        "api_limits": {"pexels": {"per_minute": 5000, "per_hour": 50000}, "pixabay": {"per_minute": 5000}},
    })
    cfg.set_secret("GEMINI_API_KEY", "fake-key-1234567890")
    cfg.set_secret("PEXELS_API_KEY", "pexels-key-1234567890")
    cfg.set_secret("PIXABAY_API_KEY", "pixabay-key-1234567890")
    return cfg
