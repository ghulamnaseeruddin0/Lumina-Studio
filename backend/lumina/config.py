"""Settings, secret vault and paths. Standard library only."""
import copy
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("LUMINA_DATA") or ROOT / "data")
ASSETS = Path(os.environ.get("LUMINA_ASSETS") or ROOT / "assets")

SECRET_NAMES = [
    "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY",
    "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
    "PEXELS_API_KEY", "PIXABAY_API_KEY",
]
DOTENV_NAMES = set(SECRET_NAMES) | {
    "LUMINA_DATA", "LUMINA_ASSETS", "LUMINA_TOKEN", "LUMINA_ALLOWED_ORIGINS",
    "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_JWT_SECRET", "SUPABASE_SERVICE_ROLE_KEY",
    "LUMINA_SUPER_ADMIN_USER_ID", "LUMINA_AUTH_AUDIT_SALT", "LUMINA_DATABASE_URL", "DATABASE_URL",
    "REQUIRE_POSTGRES", "REQUIRE_SUPABASE_AUTH", "LUMINA_PUBLIC_URL", "LUMINA_FRONTEND_URL",
    "LUMINA_OAUTH_ENCRYPTION_KEY", "LUMINA_FILE_SIGNING_KEY", "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET", "META_APP_ID", "META_APP_SECRET", "TIKTOK_CLIENT_KEY",
    "TIKTOK_CLIENT_SECRET", "META_GRAPH_VERSION", "RESEND_API_KEY", "RESEND_FROM_EMAIL",
    "FFMPEG_PATH", "FFPROBE_PATH", "PORT",
}

DEFAULTS = {
    "llm": {
        "providers": [
            {"name": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
             "key_env": "GEMINI_API_KEY", "models": ["gemini-2.5-flash", "gemini-2.5-flash-lite"]},
            {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY",
             "models": [], "auto_free": True, "max_auto_models": 10,
             "prefer": ["qwen", "deepseek", "gpt-oss", "llama", "nemotron", "glm", "gemma"]},
            {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
             "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]},
            {"name": "cerebras", "base_url": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY",
             "models": ["gpt-oss-120b", "llama-3.3-70b"]},
            {"name": "cloudflare", "base_url": "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1",
             "key_env": "CLOUDFLARE_API_TOKEN", "models": ["@cf/meta/llama-3.1-8b-instruct"]},
            {"name": "local", "base_url": "http://localhost:11434/v1", "key_env": None, "models": ["qwen2.5:3b"]},
        ],
        "max_tokens": 4096, "temperature": 0.7, "request_timeout": 120,
    },
    "stock": {
        "pexels_base": "https://api.pexels.com", "pixabay_base": "https://pixabay.com/api",
        "wikimedia_base": "https://commons.wikimedia.org/w/api.php",
        "wikipedia_base": "https://{lang}.wikipedia.org",
        "autocomplete_url": "https://suggestqueries.google.com/complete/search",
        "download_hosts": ["videos.pexels.com", "images.pexels.com", "cdn.pixabay.com", "pixabay.com",
                           "upload.wikimedia.org"],
        "candidates_per_scene": 8, "cache_hours": 24,
    },
    "limits": {"short_max_seconds": 90, "long_max_seconds": 1800, "short_default": 60, "long_default_minutes": 8},
    "voice": {"provider": "auto", "voice": "", "rate": "-4%", "pitch": "+0Hz", "words_per_second": 2.5,
              "piper_model": ""},
    "render": {"quality": "standard", "fps": 30, "font": "", "transitions": True, "sfx": True,
               "burn_captions_short": True, "burn_captions_long": False, "watermark": True, "threads": 0},
    "audio": {"music": True, "music_volume": 0.16, "target_lufs": -14},
    "api_limits": {
        "pexels": {"per_minute": 30, "per_hour": 190}, "pixabay": {"per_minute": 80},
        "wikimedia": {"per_minute": 40},
        "wikipedia": {"per_minute": 40}, "autocomplete": {"per_minute": 30},
    },
    "server": {"host": "127.0.0.1", "port": 8787, "allowed_origins": [], "rate_limit_per_min": 300,
               "job_creations_per_hour": 12, "max_upload_mb": 5},
    "picker_ai": True,
    "strict_compliance": True,
    "allow_local_sources": False,
}

VOICES = {
    "en": "en-US-GuyNeural", "es": "es-ES-AlvaroNeural", "fr": "fr-FR-HenriNeural", "de": "de-DE-ConradNeural",
    "pt": "pt-BR-AntonioNeural", "hi": "hi-IN-MadhurNeural", "ur": "ur-PK-AsadNeural", "ar": "ar-SA-HamedNeural",
    "ru": "ru-RU-DmitryNeural", "ja": "ja-JP-KeitaNeural", "zh": "zh-CN-YunxiNeural", "tr": "tr-TR-AhmetNeural",
    "id": "id-ID-ArdiNeural", "it": "it-IT-DiegoNeural", "ko": "ko-KR-InJoonNeural",
}
LANG_NAMES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
              "hi": "Hindi", "ur": "Urdu", "ar": "Arabic", "ru": "Russian", "ja": "Japanese",
              "zh": "Chinese", "tr": "Turkish", "id": "Indonesian", "it": "Italian", "ko": "Korean"}


def deep_merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    def __init__(self, data_dir=None, overrides=None, assets_dir=None, load_dotenv=True):
        if load_dotenv:
            self._load_dotenv()
        self.data = Path(data_dir or os.environ.get("LUMINA_DATA") or DATA)
        self.assets = Path(assets_dir or os.environ.get("LUMINA_ASSETS") or ASSETS)
        for sub in ("jobs", "cache", "backups"):
            (self.data / sub).mkdir(parents=True, exist_ok=True)
        (self.assets / "music").mkdir(parents=True, exist_ok=True)
        (self.assets / "brand").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        saved = {}
        sp = self.data / "settings.json"
        if sp.exists():
            try:
                saved = json.loads(sp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
        self.s = deep_merge(deep_merge(DEFAULTS, saved), overrides or {})
        self._vault = {}
        vp = self.data / "secrets.json"
        if vp.exists():
            try:
                self._vault = json.loads(vp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._vault = {}

    @property
    def db_path(self):
        return self.data / "lumina.db"

    @property
    def jobs_dir(self):
        return self.data / "jobs"

    @property
    def cache_dir(self):
        return self.data / "cache"

    def get(self, path, default=None):
        cur = self.s
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, path, value):
        with self._lock:
            parts = path.split(".")
            cur = self.s
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = value

    def save(self, keys=("render", "voice", "audio", "limits", "server", "picker_ai", "strict_compliance")):
        with self._lock:
            out = {k: self.s[k] for k in keys if k in self.s}
            (self.data / "settings.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    def _load_dotenv(self):
        root_env = ROOT / ".env"
        dotenv_data = Path(os.environ.get("LUMINA_DATA") or DATA)
        paths = (root_env, dotenv_data / ".env")
        for path in paths:
            try:
                if not path.is_file():
                    continue
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k in DOTENV_NAMES and v and k not in os.environ:
                        os.environ[k] = v
            except OSError:
                pass

    def secret(self, name):
        return os.environ.get(name) or self._vault.get(name, "")

    def set_secret(self, name, value):
        if name not in SECRET_NAMES:
            raise ValueError("Unknown key name.")
        with self._lock:
            if value:
                self._vault[name] = value.strip()
            else:
                self._vault.pop(name, None)
            p = self.data / "secrets.json"
            p.write_text(json.dumps(self._vault), encoding="utf-8")
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass

    def secret_status(self):
        return {n: bool(self.secret(n)) for n in SECRET_NAMES}

    def all_secret_values(self):
        vals = {self.secret(n) for n in SECRET_NAMES}
        return sorted((v for v in vals if v and len(v) >= 8), key=len, reverse=True)
