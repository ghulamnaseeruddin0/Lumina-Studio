"""Stock footage finder: Pexels, Pixabay and Wikimedia Commons (all free). Real media only, never generated."""
import hashlib
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import media
from .quota import Quota, QuotaExceeded
from .security import validate_download_url

UA = "LuminaStudio/1.0 (+https://github.com/; contact: owner)"
OK_LICENSE = re.compile(r"^(cc0|public domain|pd\b|cc[- ]by(?!-nc)|cc[- ]by-sa|pexels|pixabay)", re.I)
MAX_DOWNLOAD = 250 * 1024 * 1024


class StockError(Exception):
    pass


def http_json(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise StockError("rate limit reached")
        if e.code in (401, 403):
            raise StockError("API key rejected")
        raise StockError(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise StockError(f"network error: {e}")


def tokens(text):
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2]


def slug_words(url):
    m = re.search(r"/(?:video|photo)/([^/]+?)(?:-\d+)?/?$", url or "")
    return (m.group(1) if m else "").replace("-", " ")


class Stock:
    def __init__(self, cfg, db, quota: Quota):
        self.cfg, self.db, self.quota = cfg, db, quota

    # ----------------------------------------------------------------- sources
    def sources(self):
        s = []
        if self.cfg.secret("PEXELS_API_KEY"):
            s.append("pexels")
        if self.cfg.secret("PIXABAY_API_KEY"):
            s.append("pixabay")
        s.append("wikimedia")
        return s

    def has_main_source(self):
        return bool(self.cfg.secret("PEXELS_API_KEY") or self.cfg.secret("PIXABAY_API_KEY"))

    # ----------------------------------------------------------------- search
    def search(self, source, kind, query, orientation="landscape", per_page=8):
        key = hashlib.sha1(f"{source}|{kind}|{query}|{orientation}|{per_page}".encode()).hexdigest()
        cached = self.quota.cache_get("s:" + key)
        if cached is not None:
            return cached
        self.quota.acquire(source if source != "wikimedia" else "wikimedia")
        fn = {"pexels": self._pexels, "pixabay": self._pixabay, "wikimedia": self._wikimedia}[source]
        out = fn(kind, query, orientation, per_page)
        self.quota.cache_set("s:" + key, out)
        return out

    def _pexels(self, kind, q, orient, n):
        base = self.cfg.get("stock.pexels_base").rstrip("/")
        headers = {"Authorization": self.cfg.secret("PEXELS_API_KEY")}
        o = "portrait" if orient == "portrait" else "landscape"
        qs = urllib.parse.urlencode({"query": q, "orientation": o, "per_page": n})
        out = []
        if kind == "video":
            d = http_json(f"{base}/videos/search?{qs}", headers)
            for v in d.get("videos", []):
                files = [{"w": f.get("width") or 0, "h": f.get("height") or 0, "link": f.get("link")}
                         for f in v.get("video_files", []) if f.get("link") and f.get("file_type", "video/mp4") == "video/mp4"]
                if not files:
                    continue
                out.append({"source": "pexels", "id": str(v["id"]), "kind": "video", "page_url": v.get("url", ""),
                            "author": (v.get("user") or {}).get("name", ""), "author_url": (v.get("user") or {}).get("url", ""),
                            "license": "Pexels License", "title": slug_words(v.get("url", "")), "tags": slug_words(v.get("url", "")),
                            "width": v.get("width") or 0, "height": v.get("height") or 0, "duration": float(v.get("duration") or 0),
                            "files": files, "thumb": v.get("image", "")})
        else:
            d = http_json(f"{base}/v1/search?{qs}", headers)
            for p in d.get("photos", []):
                src = p.get("src", {})
                url = src.get("large2x") or src.get("large") or src.get("original")
                if not url:
                    continue
                out.append({"source": "pexels", "id": str(p["id"]), "kind": "photo", "page_url": p.get("url", ""),
                            "author": p.get("photographer", ""), "author_url": p.get("photographer_url", ""),
                            "license": "Pexels License", "title": p.get("alt", ""), "tags": (p.get("alt") or "") + " " + slug_words(p.get("url", "")),
                            "width": p.get("width") or 0, "height": p.get("height") or 0, "duration": 0,
                            "download": url, "thumb": src.get("medium", "")})
        return out

    def _pixabay(self, kind, q, orient, n):
        base = self.cfg.get("stock.pixabay_base").rstrip("/")
        key = self.cfg.secret("PIXABAY_API_KEY")
        n = max(3, min(n, 50))
        out = []
        if kind == "video":
            qs = urllib.parse.urlencode({"key": key, "q": q, "per_page": n, "safesearch": "true"})
            d = http_json(f"{base}/videos/?{qs}")
            for v in d.get("hits", []):
                files = [{"w": f.get("width") or 0, "h": f.get("height") or 0, "link": f.get("url")}
                         for f in (v.get("videos") or {}).values() if f.get("url")]
                if not files:
                    continue
                w = max(f["w"] for f in files)
                h = max(f["h"] for f in files)
                out.append({"source": "pixabay", "id": str(v["id"]), "kind": "video", "page_url": v.get("pageURL", ""),
                            "author": v.get("user", ""), "author_url": f"https://pixabay.com/users/{v.get('user','')}-{v.get('user_id','')}/",
                            "license": "Pixabay Content License", "title": v.get("tags", ""), "tags": v.get("tags", ""),
                            "width": w, "height": h, "duration": float(v.get("duration") or 0), "files": files, "thumb": ""})
        else:
            qs = urllib.parse.urlencode({"key": key, "q": q, "per_page": n, "image_type": "photo", "safesearch": "true",
                                         "orientation": "vertical" if orient == "portrait" else "horizontal", "min_width": 1280})
            d = http_json(f"{base}/?{qs}")
            for p in d.get("hits", []):
                url = p.get("largeImageURL") or p.get("webformatURL")
                if not url:
                    continue
                out.append({"source": "pixabay", "id": str(p["id"]), "kind": "photo", "page_url": p.get("pageURL", ""),
                            "author": p.get("user", ""), "author_url": f"https://pixabay.com/users/{p.get('user','')}-{p.get('user_id','')}/",
                            "license": "Pixabay Content License", "title": p.get("tags", ""), "tags": p.get("tags", ""),
                            "width": p.get("imageWidth") or 0, "height": p.get("imageHeight") or 0, "duration": 0,
                            "download": url, "thumb": p.get("previewURL", "")})
        return out

    def _wikimedia(self, kind, q, orient, n):
        if kind != "photo":
            return []
        base = self.cfg.get("stock.wikimedia_base")
        qs = urllib.parse.urlencode({
            "action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": f"{q} filetype:bitmap",
            "gsrlimit": n, "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1920,
            "format": "json", "origin": "*"})
        d = http_json(f"{base}?{qs}")
        out = []
        for page in (d.get("query", {}).get("pages") or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            ext = info.get("extmetadata", {})
            lic = (ext.get("LicenseShortName") or {}).get("value", "")
            if not OK_LICENSE.match(lic or "") or not info.get("thumburl"):
                continue
            if not str(info.get("mime", "")).startswith("image/") or "svg" in str(info.get("mime", "")):
                continue
            artist = html.unescape(re.sub(r"<[^>]+>", "", (ext.get("Artist") or {}).get("value", ""))).strip()
            title = re.sub(r"^File:|\.\w+$", "", page.get("title", ""))
            out.append({"source": "wikimedia", "id": str(page.get("pageid")), "kind": "photo",
                        "page_url": info.get("descriptionurl", ""), "author": artist or "Unknown", "author_url": "",
                        "license": lic, "title": title, "tags": title + " " + html.unescape(re.sub(r"<[^>]+>", "", (ext.get("ImageDescription") or {}).get("value", "")))[:200],
                        "width": info.get("thumbwidth") or info.get("width") or 0, "height": info.get("thumbheight") or info.get("height") or 0,
                        "duration": 0, "download": info["thumburl"], "thumb": info["thumburl"],
                        "needs_credit": bool(re.search(r"by", lic, re.I))})
        return out

    def search_all(self, queries, orientation, prefer="video", want=8, allow_wiki=False, status=None):
        """Try queries from specific to generic until enough candidates are found. Returns (candidates, warnings)."""
        say = status or (lambda s: None)
        found, seen, warnings = [], set(), []
        for qi, q in enumerate(queries):
            kinds = ["video", "photo"] if prefer == "video" else ["photo", "video"]
            for kind in kinds:
                for src in self.sources():
                    if src == "wikimedia" and not (allow_wiki or not self.has_main_source()):
                        continue
                    try:
                        for c in self.search(src, kind, q, orientation, per_page=12):
                            k = (c["source"], c["id"])
                            if k not in seen:
                                seen.add(k)
                                c["query_rank"] = qi
                                c["query"] = q
                                found.append(c)
                    except (StockError, QuotaExceeded) as e:
                        msg = f"{src}: {e}"
                        if msg not in warnings:
                            warnings.append(msg)
                if len(found) >= want and kind == kinds[0]:
                    break
            if len(found) >= want:
                break
        return found, warnings

    # --------------------------------------------------------------- download
    def _pick_file(self, cand, target_h):
        files = sorted(cand["files"], key=lambda f: f["h"] or 0)
        ok = [f for f in files if (f["h"] or 0) >= target_h and (f["w"] or 0) >= 0]
        return ok[0] if ok else files[-1]

    def download(self, cand, target_h=1080, status=None):
        folder = self.cfg.cache_dir / "assets"
        folder.mkdir(parents=True, exist_ok=True)
        if cand["kind"] == "video":
            f = self._pick_file(cand, target_h)
            url, ext = f["link"], ".mp4"
        else:
            url = cand["download"]
            ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
            ext = ext if ext in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"
        name = f"{cand['source']}_{cand['id']}_{cand['kind'][0]}{target_h if cand['kind']=='video' else ''}{ext}"
        dest = folder / name
        if dest.exists() and dest.stat().st_size > 1000:
            return dest
        validate_download_url(url, self.cfg.get("stock.download_hosts"), self.cfg.get("allow_local_sources", False))
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        part = dest.with_suffix(dest.suffix + ".part")
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as fh:
                ctype = r.headers.get("Content-Type", "")
                if ctype and not (ctype.startswith("video/") or ctype.startswith("image/") or ctype == "application/octet-stream"):
                    raise StockError(f"unexpected content type {ctype}")
                total = 0
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD:
                        raise StockError("file is too large")
                    fh.write(chunk)
            if cand["kind"] == "video":
                info = media.probe(part)
                if not info["has_video"] or info["duration"] <= 0:
                    raise StockError("downloaded video is unreadable")
            else:
                from PIL import Image
                try:
                    with Image.open(part) as im:
                        im.verify()
                except Exception:
                    raise StockError("downloaded image is unreadable")
            part.replace(dest)
        except (urllib.error.URLError, OSError, media.MediaError, ValueError) as e:
            part.unlink(missing_ok=True)
            raise StockError(f"download failed: {e}")
        except StockError:
            part.unlink(missing_ok=True)
            raise
        return dest
