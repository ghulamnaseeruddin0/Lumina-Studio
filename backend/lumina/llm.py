"""Model router for free tiers: many providers, automatic fallback, cooldowns, robust JSON answers."""
import json
import re
import threading
import time
import urllib.error
import urllib.request

from .security import redact


class LLMUnavailable(Exception):
    pass


class _RateLimit(Exception):
    def __init__(self, wait, daily=False):
        super().__init__("rate limited")
        self.wait, self.daily = wait, daily


class _Transient(Exception):
    pass


class _Fatal(Exception):
    pass


class _Bad(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


DAILY_RE = re.compile(r"per[-_ ]?day|daily", re.I)
RETRY_RE = re.compile(r"retry (?:in|after) ([\d.]+)\s*s", re.I)
FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)
THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def extract_json(text):
    """Find the first valid JSON object/array in a model reply (tolerates fences, chatter, trailing commas)."""
    text = THINK_RE.sub("", text or "")
    candidates = [m for m in FENCE_RE.findall(text)] + [text]
    dec = json.JSONDecoder()
    for chunk in candidates:
        for i, ch in enumerate(chunk):
            if ch not in "{[":
                continue
            snippet = chunk[i:]
            for attempt in (snippet, re.sub(r",\s*([}\]])", r"\1", snippet)):
                try:
                    obj, _ = dec.raw_decode(attempt)
                    return obj
                except ValueError:
                    continue
    raise ValueError("no JSON found in the model reply")


class LLM:
    def __init__(self, cfg, db=None, state_file=None):
        self.cfg, self.db = cfg, db
        self.cool = {}
        self.dead = set()
        self._chain = None
        self._lock = threading.Lock()
        self.state_file = state_file or (cfg.data / "llm_state.json")
        self.total_requests = 0
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            now = time.time()
            self.cool = {k: v for k, v in data.get("cool", {}).items() if v > now}
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------ chain
    def _discover_openrouter(self, p, key):
        try:
            req = urllib.request.Request(p["base_url"].rstrip("/") + "/models")
            if key:
                req.add_header("Authorization", f"Bearer {key}")
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
        except Exception:
            return []
        prefer = [x.lower() for x in p.get("prefer", [])]
        found = []
        for m in data.get("data", []):
            mid = m.get("id", "")
            modality = str(m.get("architecture", {}).get("modality", "text->text"))
            ctx = m.get("context_length") or 0
            if not mid.endswith(":free") or not modality.endswith("->text") or ctx < 12000:
                continue
            idx = min((i for i, k in enumerate(prefer) if k in mid.lower()), default=len(prefer))
            found.append((idx, -ctx, mid))
        found.sort()
        return [mid for _, _, mid in found[: p.get("max_auto_models", 10)]]

    def chain(self):
        if self._chain is None:
            chain = []
            for p in self.cfg.get("llm.providers", []):
                env = p.get("key_env")
                key = self.cfg.secret(env) if env else "none"
                if env and not key:
                    continue
                base = p["base_url"]
                for name in ("CLOUDFLARE_ACCOUNT_ID",):
                    if "{" + name + "}" in base:
                        base = base.replace("{" + name + "}", self.cfg.secret(name))
                        if not self.cfg.secret(name):
                            base = ""
                if not base:
                    continue
                models = list(p.get("models", []))
                if p.get("auto_free"):
                    models += [m for m in self._discover_openrouter({**p, "base_url": base}, key) if m not in models]
                for m in models:
                    chain.append({"provider": p["name"], "model": m, "base": base, "key": key, "label": f"{p['name']}/{m}"})
            self._chain = chain
        return self._chain

    def available(self):
        """True when a keyed provider exists, or a local Ollama server is actually running."""
        chain = self.chain()
        if any(c["provider"] != "local" for c in chain):
            return True
        local = [c for c in chain if c["provider"] == "local"]
        if not local:
            return False
        now = time.time()
        if now - getattr(self, "_local_checked", 0) > 30:
            self._local_checked = now
            try:
                urllib.request.urlopen(local[0]["base"].rstrip("/") + "/models", timeout=1.5).close()
                self._local_ok = True
            except Exception:
                self._local_ok = False
        return self._local_ok

    def reset_chain(self):
        self._chain = None

    def _save(self):
        try:
            self.state_file.write_text(json.dumps({"cool": self.cool}), encoding="utf-8")
        except OSError:
            pass

    def _cooling(self, c):
        return c["provider"] in self.dead or max(self.cool.get(c["label"], 0), self.cool.get(c["provider"] + ":*", 0)) > time.time()

    # ------------------------------------------------------------------- HTTP
    def _post(self, c, messages, max_tokens, temperature):
        body = {"model": c["model"], "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {c['key']}"}
        if c["provider"] == "openrouter":
            headers["HTTP-Referer"] = "https://localhost/lumina-studio"
            headers["X-Title"] = "Lumina Studio"
        req = urllib.request.Request(c["base"].rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.get("llm.request_timeout", 120)) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            self._raise_for(e.code, e.read().decode("utf-8", "replace"), e.headers.get("Retry-After"))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise _Transient(f"network error: {e}")
        except ValueError:
            raise _Transient("invalid JSON from server")
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            try:
                code = int(err.get("code", 500)) if isinstance(err, dict) else 500
            except (TypeError, ValueError):
                code = 500
            self._raise_for(code, json.dumps(err), None)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise _Transient("unexpected response shape")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not content or not content.strip():
            raise _Transient("empty reply")
        return content, (data.get("usage") or {}).get("total_tokens", 0)

    @staticmethod
    def _raise_for(code, text, retry_after):
        if code == 429:
            daily = bool(DAILY_RE.search(text))
            wait = 3 * 3600 if daily else 30
            if not daily:
                m = RETRY_RE.search(text)
                if retry_after and str(retry_after).replace(".", "", 1).isdigit():
                    wait = float(retry_after)
                elif m:
                    wait = float(m.group(1))
            raise _RateLimit(max(wait, 5) + 1, daily)
        if code in (401, 403):
            raise _Fatal(f"key rejected (HTTP {code})")
        if code in (400, 404, 422):
            raise _Bad(f"HTTP {code}: {text[:120]}", code)
        raise _Transient(f"HTTP {code}")

    def _call(self, c, messages, max_tokens, temperature):
        try:
            return self._post(c, messages, max_tokens, temperature)
        except _Bad:
            if messages and messages[0]["role"] == "system" and len(messages) > 1:
                merged = [dict(m) for m in messages[1:]]
                merged[0]["content"] = messages[0]["content"] + "\n\n" + merged[0]["content"]
                return self._post(c, merged, max_tokens, temperature)
            raise

    # ------------------------------------------------------------------- chat
    def chat(self, messages, max_tokens=None, temperature=None, status=None):
        say = status or (lambda s: None)
        chain = self.chain()
        if not chain:
            raise LLMUnavailable("No AI model is available. Add a free key in Settings (Gemini or OpenRouter) "
                                 "or start Ollama.")
        max_tokens = max_tokens or self.cfg.get("llm.max_tokens", 4096)
        temperature = self.cfg.get("llm.temperature", 0.7) if temperature is None else temperature
        errors = {}
        for _round in range(4):
            for c in chain:
                if self._cooling(c):
                    continue
                try:
                    text, tokens = self._call(c, messages, max_tokens, temperature)
                    self.total_requests += 1
                    self._count(c["provider"], tokens)
                    say(f"answered by {c['label']}")
                    return text
                except _RateLimit as e:
                    key = c["provider"] + ":*" if (e.daily and c["provider"] == "openrouter") else c["label"]
                    self.cool[key] = time.time() + e.wait
                    errors[c["label"]] = "rate limited"
                    say(f"{c['label']} is rate-limited, switching model")
                except _Fatal as e:
                    self.dead.add(c["provider"])
                    errors[c["label"]] = str(e)
                    say(f"{c['provider']}: {e}; skipping this provider")
                except _Bad as e:
                    if e.code == 404:  # model does not exist (any more): stop trying it for a while
                        self.cool[c["label"]] = time.time() + 600
                    errors[c["label"]] = str(e)[:60]
                    say(f"{c['label']} rejected the request, switching model")
                except _Transient as e:
                    self.cool[c["label"]] = time.time() + (120 if "refused" in str(e).lower() else 20)
                    errors[c["label"]] = str(e)[:60]
                    say(f"{c['label']}: {str(e)[:50]}, switching model")
                self._save()
            waits = [max(self.cool.get(c["label"], 0), self.cool.get(c["provider"] + ":*", 0)) - time.time()
                     for c in chain if c["provider"] not in self.dead and self._cooling(c)]
            if not waits or min(waits) > self.cfg.get("llm.max_round_wait", 90):
                break
            say(f"all models busy, waiting {int(min(waits)) + 1}s")
            time.sleep(min(waits) + 1)
        detail = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:5])
        raise LLMUnavailable("Every AI model is rate-limited or unavailable right now. "
                             f"Wait a few minutes or add another free key. ({detail})")

    def _count(self, provider, tokens):
        if not self.db:
            return
        day = time.strftime("%Y-%m-%d", time.gmtime())
        try:
            self.db.x("INSERT INTO llm_usage(day,provider,count,tokens) VALUES(?,?,1,?) "
                      "ON CONFLICT(day,provider) DO UPDATE SET count=count+1, tokens=tokens+excluded.tokens",
                      (day, provider, int(tokens or 0)))
        except Exception:
            pass

    def ask(self, system, user, status=None, **kw):
        return self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], status=status, **kw)

    def ask_json(self, system, user, status=None, retries=2, **kw):
        system = system + "\nReturn ONLY valid JSON. No markdown fences and no commentary."
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last = None
        for attempt in range(retries + 1):
            text = self.chat(msgs, status=status, **kw)
            try:
                return extract_json(text)
            except ValueError as e:
                last = e
                msgs = msgs + [{"role": "assistant", "content": text[:1500]},
                               {"role": "user", "content": "That was not valid JSON. Reply again with ONLY the JSON."}]
                if status:
                    status("reply was not valid JSON, asking again")
        raise LLMUnavailable(f"The model did not return usable JSON ({last}).")

    def usage_today(self):
        if not self.db:
            return []
        day = time.strftime("%Y-%m-%d", time.gmtime())
        return self.db.q("SELECT provider, count, tokens FROM llm_usage WHERE day=?", (day,))
