"""Agent 25 (Security guard) and shared protections: input cleaning, SSRF, auth, rate limits, redaction."""
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time
import base64
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
TAG_RE = re.compile(r"<[^>]{0,200}>")
INJECTION_RE = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|prompts)|disregard (the )?(system|previous)|"
    r"you are now|reveal (your|the) (system )?prompt|act as (an? )?(unrestricted|jailbroken)|"
    r"api[_ -]?key|print (the )?env|\bsudo\b|rm -rf)", re.I)

TOKEN_PATTERNS = [r"sk-or-v1-[A-Za-z0-9]{20,}", r"sk-[A-Za-z0-9_\-]{24,}", r"AIza[0-9A-Za-z_\-]{30,}",
                  r"gsk_[A-Za-z0-9]{20,}", r"csk-[A-Za-z0-9]{20,}", r"gh[pousr]_[A-Za-z0-9]{30,}",
                  r"github_pat_[A-Za-z0-9_]{30,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"]
_TOKEN_RX = [re.compile(p, re.S) for p in TOKEN_PATTERNS]

_secret_values = []


def register_secrets(values):
    global _secret_values
    _secret_values = sorted({v for v in values if v and len(v) >= 8}, key=len, reverse=True)


def redact(text):
    if not text:
        return text
    text = str(text)
    for v in _secret_values:
        text = text.replace(v, "[REDACTED]")
    for rx in _TOKEN_RX:
        text = rx.sub("[REDACTED]", text)
    return text


def sanitize_topic(text, max_len=300):
    text = CONTROL_RE.sub(" ", str(text or "")).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) < 3:
        raise ValueError("The topic is too short (minimum 3 characters).")
    if len(text) > max_len:
        raise ValueError(f"The topic is too long (maximum {max_len} characters).")
    if re.search(r"https?://", text):
        raise ValueError("Please enter a topic in words, not a link.")
    return text


def looks_like_injection(text):
    return bool(INJECTION_RE.search(text or ""))


def clean_untrusted(text, max_len=6000):
    """Make fetched web text safe to place inside a prompt: strip tags, control chars and instruction-like lines."""
    text = CONTROL_RE.sub(" ", str(text or ""))
    text = TAG_RE.sub(" ", text)
    kept = [ln for ln in text.splitlines() if not INJECTION_RE.search(ln)]
    text = re.sub(r"[ \t]+", " ", "\n".join(kept))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_len]


def safe_join(base, rel):
    base = Path(base).resolve()
    full = (base / rel).resolve()
    if full != base and base not in full.parents:
        raise PermissionError("Path is outside the allowed folder.")
    return full


def decode_upload_data(data, max_bytes):
    """Decode a strict base64 data URL with an explicit size ceiling."""
    import base64
    import binascii

    if not isinstance(data, str) or not data.startswith("data:") or "," not in data:
        raise ValueError("Upload must use a base64 data URL.")
    header, encoded = data.split(",", 1)
    if ";base64" not in header.lower():
        raise ValueError("Upload data URL must be base64 encoded.")
    if len(encoded) > ((max_bytes + 2) // 3) * 4 + 8:
        raise ValueError("Uploaded file exceeds the configured size limit.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("Uploaded file is not valid base64 data.") from e
    if not content or len(content) > max_bytes:
        raise ValueError("Uploaded file is empty or exceeds the configured size limit.")
    return content


def is_public_host(host):
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def validate_download_url(url, allowed_hosts, allow_local=False):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Only http(s) downloads are allowed.")
    host = u.hostname.lower()
    if allow_local and host in ("127.0.0.1", "localhost"):
        return u
    if not any(host == h or host.endswith("." + h) for h in allowed_hosts):
        raise ValueError(f"Download blocked: {host} is not an approved media host.")
    if not is_public_host(host):
        raise ValueError("Download blocked: not a public address.")
    return u


class Auth:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "auth.json"
        self.token = ""
        self._fails = defaultdict(deque)
        self._lock = threading.Lock()
        self._jwks = None
        self._load()

    @property
    def supabase_enabled(self):
        return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_ANON_KEY"))

    def supabase_identity(self, supplied):
        """Validate a Supabase access token and return verified identity claims."""
        if not self.supabase_enabled or not supplied:
            return None
        import jwt

        base = os.environ["SUPABASE_URL"].rstrip("/")
        try:
            header = jwt.get_unverified_header(supplied)
            if header.get("alg") == "HS256":
                secret = os.environ.get("SUPABASE_JWT_SECRET")
                if not secret:
                    return None
                key = secret
            else:
                if self._jwks is None:
                    self._jwks = jwt.PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json")
                key = self._jwks.get_signing_key_from_jwt(supplied).key
            claims = jwt.decode(
                supplied,
                key,
                algorithms=["HS256", "ES256", "RS256"],
                audience="authenticated",
                issuer=f"{base}/auth/v1",
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
            return {"id": claims.get("sub"), "email": claims.get("email", ""),
                    "session_id": claims.get("session_id") or claims.get("sid"),
                    "iat": claims.get("iat")}
        except (jwt.PyJWTError, OSError, ValueError):
            return None

    def supabase_user(self, supplied):
        identity = self.supabase_identity(supplied)
        return identity["id"] if identity else None

    def file_ticket(self, user_id, job_id, lifetime=3600):
        payload = json.dumps(
            {"user": user_id, "job": job_id, "expires": int(time.time()) + lifetime},
            separators=(",", ":"),
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        key = os.environ.get("LUMINA_FILE_SIGNING_KEY") or self.token
        signature = hmac.new(key.encode(), encoded.encode(), "sha256").hexdigest()
        return f"{encoded}.{signature}"

    def check_file_ticket(self, ticket, job_id):
        try:
            encoded, signature = ticket.split(".", 1)
            key = os.environ.get("LUMINA_FILE_SIGNING_KEY") or self.token
            expected = hmac.new(key.encode(), encoded.encode(), "sha256").hexdigest()
            if not hmac.compare_digest(signature, expected):
                return None
            payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            data = json.loads(payload)
            if data.get("job") != job_id or int(data.get("expires", 0)) < time.time():
                return None
            return data.get("user")
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def _load(self):
        env = os.environ.get("LUMINA_TOKEN")
        if env:
            self.token = env
            return
        try:
            self.token = json.loads(self.path.read_text(encoding="utf-8")).get("token", "")
        except (OSError, ValueError):
            self.token = ""
        if not self.token:
            self.rotate()

    def rotate(self):
        self.token = secrets.token_urlsafe(32)
        try:
            self.path.write_text(json.dumps({"token": self.token}), encoding="utf-8")
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return self.token

    def locked(self, ip):
        with self._lock:
            q = self._fails[ip]
            now = time.time()
            while q and now - q[0] > 300:
                q.popleft()
            return len(q) >= 10

    def check(self, supplied, ip):
        if self.locked(ip):
            return False
        ok = bool(supplied) and hmac.compare_digest(str(supplied), self.token)
        if not ok:
            with self._lock:
                self._fails[ip].append(time.time())
        return ok


class RateLimiter:
    def __init__(self):
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key, limit, window=60):
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


LANGS = {"en", "es", "fr", "de", "pt", "hi", "ur", "ar", "ru", "ja", "zh", "tr", "id", "it", "ko"}


def validate_job_params(raw, limits):
    p = {}
    p["topic"] = sanitize_topic(raw.get("topic"))
    fmt = str(raw.get("format", "short")).lower()
    if fmt not in ("short", "long"):
        raise ValueError("format must be 'short' or 'long'.")
    p["format"] = fmt
    try:
        if fmt == "short":
            sec = int(raw.get("seconds") or limits["short_default"])
            p["seconds"] = max(15, min(sec, limits["short_max_seconds"]))
        else:
            mins = float(raw.get("minutes") or limits["long_default_minutes"])
            p["minutes"] = max(1.0, min(mins, limits["long_max_seconds"] / 60))
    except (TypeError, ValueError):
        raise ValueError("Length must be a number.")
    lang = str(raw.get("language", "en")).lower()
    p["language"] = lang if lang in LANGS else "en"
    p["translate"] = [l for l in (raw.get("translate") or []) if l in LANGS and l != p["language"]][:4]
    p["review"] = {"script": bool(raw.get("review_script", False)), "scenes": bool(raw.get("review_scenes", False))}
    try:
        p["repurpose"] = max(0, min(int(raw.get("repurpose") or 0), 5)) if fmt == "long" else 0
    except (TypeError, ValueError):
        p["repurpose"] = 0
    q = str(raw.get("quality", "")).lower()
    p["quality"] = q if q in ("draft", "standard", "high") else ""
    p["music"] = bool(raw.get("music", True))
    p["niche"] = sanitize_topic(raw["niche"]) if raw.get("niche") else ""
    p["voice"] = re.sub(r"[^A-Za-z0-9\-]", "", str(raw.get("voice", "")))[:60]
    if raw.get("burn_captions") is not None:
        p["burn_captions"] = bool(raw["burn_captions"])
    return p
