"""Agent 24 (Quota and cost manager): sliding-window API limits, search cache, usage status."""
import json
import time


class QuotaExceeded(Exception):
    pass


class Quota:
    def __init__(self, db, cfg):
        self.db, self.cfg = db, cfg

    def _limits(self, provider):
        return self.cfg.get(f"api_limits.{provider}", {}) or {}

    def _count(self, provider, window):
        r = self.db.one("SELECT COUNT(*) n, MIN(ts) first FROM api_calls WHERE provider=? AND ts>?",
                        (provider, time.time() - window))
        return r["n"], r["first"]

    def wait_needed(self, provider):
        worst = 0.0
        for key, window in (("per_minute", 60), ("per_hour", 3600), ("per_day", 86400)):
            lim = self._limits(provider).get(key)
            if not lim:
                continue
            n, first = self._count(provider, window)
            if n >= lim and first:
                worst = max(worst, first + window - time.time())
        return max(worst, 0.0)

    def acquire(self, provider, max_wait=75, sleep=time.sleep):
        wait = self.wait_needed(provider)
        if wait > max_wait:
            raise QuotaExceeded(f"{provider} limit reached; next free slot in {int(wait)}s.")
        if wait > 0:
            sleep(wait + 0.2)
        self.db.x("INSERT INTO api_calls(provider, ts) VALUES(?,?)", (provider, time.time()))
        if int(time.time()) % 50 == 0:
            self.db.x("DELETE FROM api_calls WHERE ts<?", (time.time() - 2 * 86400,))

    def status(self):
        out = {}
        for provider, lims in self.cfg.get("api_limits", {}).items():
            row = {}
            for key, window in (("per_minute", 60), ("per_hour", 3600), ("per_day", 86400)):
                if lims.get(key):
                    row[key] = {"used": self._count(provider, window)[0], "limit": lims[key]}
            out[provider] = row
        return out

    def cache_get(self, key, hours=None):
        hours = hours if hours is not None else self.cfg.get("stock.cache_hours", 24)
        r = self.db.one("SELECT ts, payload FROM cache WHERE key=?", (key,))
        if r and time.time() - r["ts"] < hours * 3600:
            try:
                return json.loads(r["payload"])
            except ValueError:
                return None
        return None

    def cache_set(self, key, payload):
        self.db.x("INSERT INTO cache(key,ts,payload) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET ts=excluded.ts, payload=excluded.payload",
                  (key, time.time(), json.dumps(payload)))
