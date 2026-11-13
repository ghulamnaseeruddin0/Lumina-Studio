"""A Job: its folder, persisted state, live event stream and cancellation."""
import json
import threading
import time

from .security import redact

TERMINAL = {"done", "needs_attention", "failed", "cancelled"}


class Cancelled(Exception):
    pass


class Bus:
    """Wakes live-stream listeners when a new event is written."""

    def __init__(self):
        self.cond = threading.Condition()

    def notify(self):
        with self.cond:
            self.cond.notify_all()

    def wait(self, timeout):
        with self.cond:
            self.cond.wait(timeout)


class Job:
    def __init__(self, jid, app):
        self.id, self.app = jid, app
        self.cfg, self.db = app.cfg, app.db
        self.dir = app.cfg.jobs_dir / jid
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "parts").mkdir(exist_ok=True)
        (self.dir / "audio").mkdir(exist_ok=True)
        for sub in ("media", "thumbs", "thumbnails", "captions"):
            (self.dir / sub).mkdir(exist_ok=True)
        row = self.db.one("SELECT params, parent, user_id FROM jobs WHERE id=?", (jid,)) or {}
        self.params = json.loads(row.get("params") or "{}")
        self.parent = row.get("parent")
        self.user_id = row.get("user_id") or "local"
        self.state = {}
        sp = self.dir / "state.json"
        if sp.exists():
            try:
                self.state = json.loads(sp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.state = {}
        self.cancel_flag = threading.Event()
        self.agent = None
        self.llm = app.llm
        self.quota = app.quota
        self.stock = app.stock

    # ---- format helpers
    @property
    def is_short(self):
        return self.params.get("format") == "short"

    @property
    def max_seconds(self):
        lim = self.cfg.get("limits")
        return lim["short_max_seconds"] if self.is_short else lim["long_max_seconds"]

    @property
    def target_seconds(self):
        if self.is_short:
            return min(self.params.get("seconds", 60), self.max_seconds)
        return min(self.params.get("minutes", 8) * 60, self.max_seconds - 15)

    @property
    def size(self):
        q = self.params.get("quality") or self.cfg.get("render.quality")
        w, h = (1080, 1920) if self.is_short else (1920, 1080)
        if q == "draft":
            w, h = w // 2, h // 2
        return w, h

    # ---- persistence
    def save_state(self):
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.dir / "state.json")

    def set_status(self, status, error=None, progress=None):
        self.db.x("UPDATE jobs SET status=?, updated=?, error=COALESCE(?,error), progress=COALESCE(?,progress), current_agent=? WHERE id=?",
                  (status, time.time(), error, progress, self.agent, self.id))
        self.app.bus.notify()

    # ---- events
    def emit(self, type, message="", data=None, agent=None):
        self.db.x("INSERT INTO events(job_id,ts,agent,type,message,data) VALUES(?,?,?,?,?,?)",
                  (self.id, time.time(), agent or self.agent, type, redact(message)[:800],
                   json.dumps(data, ensure_ascii=False) if data is not None else None))
        self.app.bus.notify()

    def log(self, message, data=None):
        self.emit("log", message, data)

    def progress(self, pct, detail=""):
        self.emit("progress", detail, {"pct": round(float(pct), 1)})

    def artifact(self, kind, title, payload):
        self.emit("artifact", title, {"kind": kind, "payload": payload})

    def warn(self, message):
        self.emit("warn", message)

    def check(self):
        if self.cancel_flag.is_set():
            raise Cancelled()

    def ai(self, system, user, json_mode=True, **kw):
        """Ask the model; every request and the model that answered is shown live."""
        self.check()
        self.log("asking the AI model…")
        fn = self.llm.ask_json if json_mode else self.llm.ask
        out = fn(system, user, status=lambda s: self.log(s), **kw)
        self.check()
        return out

    def file(self, *parts):
        return self.dir.joinpath(*parts)
