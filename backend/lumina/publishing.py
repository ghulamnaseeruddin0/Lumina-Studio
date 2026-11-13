"""Official-platform OAuth helpers, encrypted token storage, and Resend delivery."""
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.fernet import Fernet, InvalidToken

from . import security

PROVIDERS = ("youtube", "instagram", "facebook", "tiktok")
META_GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v23.0")
OAUTH_ENV = {
    "youtube": ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET"),
    "meta": ("META_APP_ID", "META_APP_SECRET"),
    "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"),
}
SCOPES = {
    "youtube": ("openid", "email", "profile", "https://www.googleapis.com/auth/youtube.upload"),
    "meta": ("pages_show_list", "pages_read_engagement", "pages_manage_posts", "instagram_basic",
             "instagram_content_publish"),
    "tiktok": ("user.info.basic", "video.publish"),
}
PLATFORM_REQUIREMENTS = {
    "youtube": {"short": "Shorts are inferred from the uploaded vertical video and duration.", "long": "YouTube video upload"},
    "instagram": {"short": "Professional Instagram account linked to a Facebook Page; eligible Reel format",
                  "long": "Professional Instagram account linked to a Facebook Page; Reels publishing API only"},
    "facebook": {"short": "Facebook Page with Reels publishing permission", "long": "Facebook Page video/Reels publishing permission"},
    "tiktok": {"short": "TikTok Content Posting API access and an eligible account",
               "long": "TikTok Content Posting API access; duration and privacy depend on app approval"},
}


class PublishingError(ValueError):
    pass


def _fernet():
    key = os.environ.get("LUMINA_OAUTH_ENCRYPTION_KEY", "")
    if not key:
        raise PublishingError("Set LUMINA_OAUTH_ENCRYPTION_KEY on the server before connecting publishing accounts.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise PublishingError("LUMINA_OAUTH_ENCRYPTION_KEY must be a valid Fernet key.") from exc


def _request(url, method="GET", headers=None, body=None, timeout=30):
    data = None
    outgoing = dict(headers or {})
    if body is not None:
        if isinstance(body, bytes):
            data = body
        else:
            data = urllib.parse.urlencode(body).encode()
            outgoing.setdefault("Content-Type", "application/x-www-form-urlencoded")
    request = urllib.request.Request(url, data=data, headers=outgoing, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024)
            content_type = response.headers.get("Content-Type", "")
            return response.status, dict(response.headers), json.loads(raw) if "json" in content_type else raw
    except urllib.error.HTTPError as exc:
        raise PublishingError(f"Publishing provider returned HTTP {exc.code}.") from exc
    except (OSError, TimeoutError) as exc:
        raise PublishingError(f"Could not reach the publishing provider ({type(exc).__name__}).") from exc


class PublishingService:
    def __init__(self, db, data_dir, public_url="", frontend_url="", http_request=None):
        self.db = db
        self.data_dir = data_dir
        self.public_url = (public_url or os.environ.get("LUMINA_PUBLIC_URL", "")).rstrip("/")
        self.frontend_url = (frontend_url or os.environ.get("LUMINA_FRONTEND_URL", "")).rstrip("/")
        self.http_request = http_request or _request

    def callback_url(self, provider):
        if not self.public_url:
            raise PublishingError("Set LUMINA_PUBLIC_URL to the public HTTPS Railway service URL.")
        parsed = urllib.parse.urlparse(self.public_url)
        if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise PublishingError("LUMINA_PUBLIC_URL must use HTTPS.")
        return f"{self.public_url}/api/publishing/callback/{provider}"

    @staticmethod
    def _client_credentials(provider):
        names = OAUTH_ENV[provider]
        client_id, client_secret = (os.environ.get(name, "") for name in names)
        if not client_id or not client_secret:
            raise PublishingError(f"Configure {names[0]} and {names[1]} on the server first.")
        return client_id, client_secret

    def capabilities(self, user_id):
        configured = {
            "youtube": all(os.environ.get(name) for name in OAUTH_ENV["youtube"]),
            "instagram": all(os.environ.get(name) for name in OAUTH_ENV["meta"]),
            "facebook": all(os.environ.get(name) for name in OAUTH_ENV["meta"]),
            "tiktok": all(os.environ.get(name) for name in OAUTH_ENV["tiktok"]),
        }
        rows = self.db.q("SELECT id,provider,account_id,account_name,scopes,metadata,expires "
                         "FROM platform_connections WHERE user_id=? ORDER BY provider,account_name", (user_id,))
        connections = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            connections.append({"id": row["id"], "provider": row["provider"], "account_id": row["account_id"],
                                "account_name": row["account_name"], "scopes": row["scopes"],
                                "expires": row["expires"], "metadata": metadata})
        return {
            "platforms": [{"id": provider, "configured": configured[provider],
                           "connected": any(row["provider"] == provider for row in connections),
                           "requirements": PLATFORM_REQUIREMENTS[provider]}
                          for provider in PROVIDERS],
            "connections": connections,
            "resend_configured": bool(os.environ.get("RESEND_API_KEY") and os.environ.get("RESEND_FROM_EMAIL")),
            "encryption_configured": bool(os.environ.get("LUMINA_OAUTH_ENCRYPTION_KEY")),
        }

    def start_oauth(self, provider, user_id):
        if provider not in ("youtube", "meta", "tiktok"):
            raise PublishingError("Unknown provider.")
        client_id, _ = self._client_credentials(provider)
        callback = self.callback_url(provider)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48) if provider == "tiktok" else ""
        self.db.x("DELETE FROM oauth_states WHERE expires<?", (time.time(),))
        self.db.x("INSERT INTO oauth_states(state_hash,user_id,provider,verifier,expires,created) VALUES(?,?,?,?,?,?)",
                  (hashlib.sha256(state.encode()).hexdigest(), user_id, provider, verifier, time.time() + 600, time.time()))
        if provider == "youtube":
            params = {"client_id": client_id, "redirect_uri": callback, "response_type": "code",
                      "scope": " ".join(SCOPES[provider]), "access_type": "offline", "prompt": "consent",
                      "include_granted_scopes": "true", "state": state}
            endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
        elif provider == "meta":
            params = {"client_id": client_id, "redirect_uri": callback, "response_type": "code",
                      "scope": ",".join(SCOPES[provider]), "state": state}
            endpoint = f"https://www.facebook.com/{META_GRAPH_VERSION}/dialog/oauth"
        else:
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            params = {"client_key": client_id, "redirect_uri": callback, "response_type": "code",
                      "scope": ",".join(SCOPES[provider]), "state": state, "code_challenge": challenge,
                      "code_challenge_method": "S256"}
            endpoint = "https://www.tiktok.com/v2/auth/authorize/"
        return {"authorization_url": endpoint + "?" + urllib.parse.urlencode(params), "expires_in": 600}

    def _exchange_code(self, provider, code, callback, verifier=""):
        client_id, client_secret = self._client_credentials(provider)
        if provider == "youtube":
            body = {"code": code, "client_id": client_id, "client_secret": client_secret,
                    "redirect_uri": callback, "grant_type": "authorization_code"}
            _, _, result = self.http_request("https://oauth2.googleapis.com/token", "POST", body=body)
            if not isinstance(result, dict) or not result.get("access_token"):
                raise PublishingError("Google token exchange did not return an access token.")
            _, _, profile = self.http_request("https://openidconnect.googleapis.com/v1/userinfo", headers={
                "Authorization": "Bearer " + result["access_token"]})
            if not isinstance(profile, dict) or not profile.get("sub"):
                raise PublishingError("Google did not return a verified YouTube account identity.")
            return [("youtube", str(profile["sub"]), str(profile.get("name") or profile.get("email") or "YouTube"),
                     result, " ".join(SCOPES["youtube"]), {"email": profile.get("email", "")})]
        if provider == "meta":
            graph = f"https://graph.facebook.com/{META_GRAPH_VERSION}"
            _, _, result = self.http_request(graph + "/oauth/access_token?" + urllib.parse.urlencode({
                "client_id": client_id, "client_secret": client_secret, "redirect_uri": callback,
                "code": code,
            }))
            access = result.get("access_token") if isinstance(result, dict) else None
            if not access:
                raise PublishingError("Meta token exchange did not return an access token.")
            _, _, long_lived = self.http_request(graph + "/oauth/access_token?" + urllib.parse.urlencode({
                "grant_type": "fb_exchange_token", "client_id": client_id, "client_secret": client_secret,
                "fb_exchange_token": access}))
            if not isinstance(long_lived, dict) or not long_lived.get("access_token"):
                raise PublishingError("Meta could not issue a long-lived publishing token.")
            result = long_lived
            access = result["access_token"]
            _, _, pages = self.http_request(graph + "/me/accounts?" + urllib.parse.urlencode({
                "fields": "id,name,access_token,instagram_business_account{id,username}", "access_token": access
            }))
            entries = pages.get("data", []) if isinstance(pages, dict) else []
            saved = []
            for page in entries:
                page_token = {**result, "access_token": page.get("access_token", access)}
                saved.append(("facebook", str(page["id"]), str(page.get("name") or "Facebook Page"),
                              page_token, ",".join(SCOPES["meta"]), {"page_id": str(page["id"])}))
                instagram = page.get("instagram_business_account")
                if instagram and instagram.get("id"):
                    saved.append(("instagram", str(instagram["id"]),
                                  str(instagram.get("username") or "Instagram"), page_token,
                                  ",".join(SCOPES["meta"]), {"instagram_id": str(instagram["id"]),
                                                            "page_id": str(page["id"])}))
            if not saved:
                raise PublishingError("No eligible Facebook Pages or linked Instagram professional accounts were returned.")
            return saved
        if provider == "tiktok":
            body = {"client_key": client_id, "client_secret": client_secret, "code": code,
                    "grant_type": "authorization_code", "redirect_uri": callback, "code_verifier": verifier}
            _, _, result = self.http_request("https://open.tiktokapis.com/v2/oauth/token/", "POST", body=body)
            tokens = result.get("data", result) if isinstance(result, dict) else {}
            access = tokens.get("access_token")
            if not access:
                raise PublishingError("TikTok token exchange did not return an access token.")
            _, _, profile = self.http_request(
                "https://open.tiktokapis.com/v2/user/info/?fields=open_id,display_name",
                headers={"Authorization": "Bearer " + access})
            user = profile.get("data", {}).get("user", {}) if isinstance(profile, dict) else {}
            if not user.get("open_id"):
                raise PublishingError("TikTok did not return an account identity.")
            tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 0))
            scopes = tokens.get("scope", SCOPES["tiktok"])
            if isinstance(scopes, str):
                scopes = scopes.replace(",", " ").split()
            return [("tiktok", str(user["open_id"]), str(user.get("display_name") or "TikTok"), tokens,
                     ",".join(scopes), {})]
        raise PublishingError("Unsupported OAuth provider.")

    def complete_oauth(self, provider, query):
        state = (query.get("state") or [""])[0]
        code = (query.get("code") or [""])[0]
        error = (query.get("error_description") or query.get("error") or [""])[0]
        if provider not in ("youtube", "meta", "tiktok") or not state:
            raise PublishingError("OAuth callback is invalid.")
        state_hash = hashlib.sha256(state.encode()).hexdigest()
        record = self.db.one("SELECT * FROM oauth_states WHERE state_hash=? AND provider=?", (state_hash, provider))
        if not record or record["expires"] < time.time():
            raise PublishingError("OAuth session expired or was already used. Start account connection again.")
        self.db.x("DELETE FROM oauth_states WHERE state_hash=?", (state_hash,))
        if error:
            raise PublishingError(f"Account authorization was declined: {str(error)[:200]}")
        if not code:
            raise PublishingError("OAuth callback did not contain an authorization code.")
        if not self.frontend_url:
            raise PublishingError("Set LUMINA_FRONTEND_URL to the public Cloudflare Pages URL.")
        cipher = _fernet()
        tokens = self._exchange_code(provider, code, self.callback_url(provider), record.get("verifier", ""))
        now = time.time()
        for platform, account_id, name, token_data, scopes, metadata in tokens:
            encrypted = cipher.encrypt(json.dumps(token_data).encode()).decode()
            expires = now + int(token_data.get("expires_in", 0)) if token_data.get("expires_in") else token_data.get("expires_at")
            connection_id = secrets.token_urlsafe(18)
            self.db.x("INSERT INTO platform_connections(id,user_id,provider,account_id,account_name,encrypted_tokens,"
                      "scopes,expires,metadata,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                      "ON CONFLICT(user_id,provider,account_id) DO UPDATE SET account_name=excluded.account_name,"
                      "encrypted_tokens=excluded.encrypted_tokens,scopes=excluded.scopes,expires=excluded.expires,"
                      "metadata=excluded.metadata,updated=excluded.updated",
                      (connection_id, record["user_id"], platform, account_id, name, encrypted, scopes,
                       expires, json.dumps(metadata), now, now))
        self.db.audit(record["user_id"], "connect_platform_account", provider, record["user_id"])
        return {"user_id": record["user_id"], "provider": provider, "count": len(tokens)}

    def decrypt_tokens(self, encrypted):
        try:
            return json.loads(_fernet().decrypt(encrypted.encode()).decode())
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise PublishingError("Stored platform credentials cannot be decrypted; check the OAuth encryption key.") from exc

    def _connection_tokens(self, connection):
        tokens = self.decrypt_tokens(connection["encrypted_tokens"])
        if connection.get("expires") and float(connection["expires"]) < time.time() + 120:
            provider = connection["provider"]
            refresh = tokens.get("refresh_token")
            if provider == "youtube" and refresh:
                client_id, client_secret = self._client_credentials("youtube")
                _, _, updated = self.http_request("https://oauth2.googleapis.com/token", "POST", body={
                    "client_id": client_id, "client_secret": client_secret, "refresh_token": refresh,
                    "grant_type": "refresh_token"})
                if isinstance(updated, dict) and updated.get("access_token"):
                    tokens.update(updated)
                    tokens["refresh_token"] = refresh
                    connection["expires"] = time.time() + int(updated.get("expires_in", 3600))
                else:
                    raise PublishingError("Google access token refresh failed; reconnect the account.")
            elif provider == "tiktok" and refresh:
                client_id, client_secret = self._client_credentials("tiktok")
                _, _, response = self.http_request("https://open.tiktokapis.com/v2/oauth/token/", "POST", body={
                    "client_key": client_id, "client_secret": client_secret, "grant_type": "refresh_token",
                    "refresh_token": refresh})
                updated = response.get("data", response) if isinstance(response, dict) else {}
                if updated.get("access_token"):
                    tokens.update(updated)
                    connection["expires"] = time.time() + int(updated.get("expires_in", 0))
                else:
                    raise PublishingError("TikTok access token refresh failed; reconnect the account.")
            else:
                raise PublishingError("Platform access token expired; reconnect the account.")
            encrypted = _fernet().encrypt(json.dumps(tokens).encode()).decode()
            self.db.x("UPDATE platform_connections SET encrypted_tokens=?,expires=?,updated=? WHERE id=?",
                      (encrypted, connection["expires"], time.time(), connection["id"]))
        return tokens

    @staticmethod
    def _stream_file(url, path, headers=None, timeout=180):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise PublishingError("Platform upload endpoint did not provide a secure URL.")
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout)
        target = urllib.parse.urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
        size = os.path.getsize(path)
        try:
            connection.putrequest("PUT", target)
            for name, value in (headers or {}).items():
                connection.putheader(name, value)
            connection.putheader("Content-Length", str(size))
            connection.endheaders()
            with open(path, "rb") as source:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    connection.send(block)
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024)
            if response.status < 200 or response.status >= 300:
                raise PublishingError(f"Platform upload failed with HTTP {response.status}.")
            try:
                return json.loads(raw) if raw else {}
            except ValueError:
                return {}
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            raise PublishingError(f"Platform upload failed ({type(exc).__name__}).") from exc
        finally:
            connection.close()

    def publish_video(self, target, job, video_path, metadata, media_url=""):
        connection = self.db.one("SELECT * FROM platform_connections WHERE id=? AND user_id=?",
                                 (target["connection_id"], target["user_id"]))
        if not connection:
            raise PublishingError("Publishing account was disconnected.")
        tokens = self._connection_tokens(connection)
        access = tokens.get("access_token")
        if not access:
            raise PublishingError("Publishing account has no usable access token; reconnect it.")
        title = metadata.get("title") or job["params"]["topic"]
        description = metadata.get("description") or ""
        if target["provider"] == "youtube":
            _, headers, _ = self.http_request(
                "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                "POST", headers={"Authorization": "Bearer " + access, "Content-Type": "application/json",
                                 "X-Upload-Content-Type": "video/mp4",
                                 "X-Upload-Content-Length": str(os.path.getsize(video_path))},
                body=json.dumps({"snippet": {"title": title, "description": description},
                                 "status": {"privacyStatus": metadata.get("privacy", "public")}}).encode())
            upload_url = headers.get("Location") or headers.get("location")
            if not upload_url:
                raise PublishingError("YouTube did not start a resumable upload.")
            result = self._stream_file(upload_url, video_path, {"Content-Type": "video/mp4"})
            remote_id = result.get("id")
            if not remote_id:
                raise PublishingError("YouTube did not confirm the uploaded video.")
            return {"remote_id": remote_id, "remote_url": "https://www.youtube.com/watch?v=" + remote_id}
        graph = f"https://graph.facebook.com/{META_GRAPH_VERSION}"
        if target["provider"] in ("instagram", "facebook"):
            if not media_url:
                raise PublishingError("A temporary public video URL is required by Meta publishing.")
            account_metadata = json.loads(connection["metadata"] or "{}")
            if target["provider"] == "instagram":
                instagram_id = account_metadata.get("instagram_id") or connection["account_id"]
                _, _, container = self.http_request(
                    f"{graph}/{urllib.parse.quote(str(instagram_id))}/media?" + urllib.parse.urlencode({
                        "media_type": "REELS", "video_url": media_url, "caption": description or title,
                        "access_token": access}))
                container_id = container.get("id") if isinstance(container, dict) else None
                if not container_id:
                    raise PublishingError("Instagram did not create a publishing container.")
                for _ in range(60):
                    time.sleep(5)
                    _, _, status = self.http_request(
                        f"{graph}/{urllib.parse.quote(str(container_id))}?" + urllib.parse.urlencode({
                            "fields": "status_code,status", "access_token": access}))
                    status_code = status.get("status_code") if isinstance(status, dict) else None
                    if status_code == "FINISHED":
                        break
                    if status_code in ("ERROR", "EXPIRED"):
                        raise PublishingError("Instagram could not process the uploaded video.")
                else:
                    raise PublishingError("Instagram video processing timed out.")
                _, _, published = self.http_request(
                    f"{graph}/{urllib.parse.quote(str(instagram_id))}/media_publish", "POST",
                    body={"creation_id": container_id, "access_token": access})
                remote_id = published.get("id") if isinstance(published, dict) else None
                if not remote_id:
                    raise PublishingError("Instagram did not confirm publication.")
                return {"remote_id": str(remote_id), "remote_url": ""}
            page_id = account_metadata.get("page_id") or connection["account_id"]
            _, _, published = self.http_request(
                f"{graph}/{urllib.parse.quote(str(page_id))}/videos", "POST", body={
                    "file_url": media_url, "title": title, "description": description,
                    "access_token": access})
            remote_id = published.get("id") if isinstance(published, dict) else None
            if not remote_id:
                raise PublishingError("Facebook did not confirm publication.")
            return {"remote_id": str(remote_id), "remote_url": ""}
        if target["provider"] == "tiktok":
            if not self.public_url or not media_url:
                raise PublishingError("TikTok requires a public URL for the video and the configured public service.")
            privacy = {"public": "PUBLIC_TO_EVERYONE", "unlisted": "MUTUAL_FOLLOW_FRIENDS",
                       "private": "SELF_ONLY"}.get(metadata.get("privacy"), "PUBLIC_TO_EVERYONE")
            body = json.dumps({"post_info": {"title": title[:2200], "privacy_level": privacy,
                "disable_duet": False, "disable_comment": False, "disable_stitch": False},
                "source_info": {"source": "PULL_FROM_URL", "video_url": media_url}}).encode()
            _, _, response = self.http_request(
                "https://open.tiktokapis.com/v2/post/publish/video/init/", "POST",
                headers={"Authorization": "Bearer " + access, "Content-Type": "application/json; charset=UTF-8"},
                body=body)
            data = response.get("data", {}) if isinstance(response, dict) else {}
            publish_id = data.get("publish_id")
            if not publish_id:
                raise PublishingError("TikTok did not accept the publishing request.")
            for _ in range(60):
                time.sleep(5)
                _, _, status = self.http_request(
                    "https://open.tiktokapis.com/v2/post/publish/status/fetch/", "POST",
                    headers={"Authorization": "Bearer " + access, "Content-Type": "application/json"},
                    body=json.dumps({"publish_id": publish_id}).encode())
                status_data = status.get("data", {}) if isinstance(status, dict) else {}
                state = status_data.get("status")
                if state in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                    post_id = status_data.get("publicaly_available_post_id", publish_id)
                    return {"remote_id": str(post_id), "remote_url": ""}
                if state in ("FAILED", "REJECTED"):
                    raise PublishingError("TikTok rejected this video. Check account eligibility and video requirements.")
            raise PublishingError("TikTok processing timed out; check the publishing status in TikTok.")
        raise PublishingError("Unsupported publishing provider.")

    def disconnect(self, user_id, connection_id):
        row = self.db.one("SELECT provider,account_name FROM platform_connections WHERE id=? AND user_id=?",
                          (connection_id, user_id))
        if not row:
            raise PublishingError("Connected account not found.")
        if self.db.one("SELECT 1 x FROM publish_targets WHERE connection_id=? AND status IN ('queued','uploading')",
                       (connection_id,)):
            raise PublishingError("Cannot disconnect an account while a publish is in progress.")
        self.db.x("DELETE FROM platform_connections WHERE id=? AND user_id=?", (connection_id, user_id))
        self.db.audit(user_id, "disconnect_platform_account", f"{row['provider']} {row['account_name']}", user_id)
        return {"disconnected": True}

    def set_notification_email(self, user_id, email):
        email = str(email or "").strip()[:254]
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise PublishingError("Enter a valid notification email address or leave it empty to use your sign-in email.")
        self.db.x("INSERT INTO publishing_preferences(user_id,notification_email,updated) VALUES(?,?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET notification_email=excluded.notification_email,updated=excluded.updated",
                  (user_id, email, time.time()))
        self.db.audit(user_id, "update_publishing_preferences", "notification_email", user_id)
        return {"notification_email": email}

    def notification_email(self, user_id, account_email=""):
        row = self.db.one("SELECT notification_email FROM publishing_preferences WHERE user_id=?", (user_id,))
        return (row or {}).get("notification_email") or account_email or ""

    def send_resend(self, recipient, subject, html, idempotency_key=""):
        key = os.environ.get("RESEND_API_KEY", "")
        sender = os.environ.get("RESEND_FROM_EMAIL", "")
        if not key or not sender:
            raise PublishingError("Configure RESEND_API_KEY and RESEND_FROM_EMAIL on the server to send email.")
        payload = json.dumps({"from": sender, "to": [recipient], "subject": subject, "html": html}).encode()
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request("https://api.resend.com/emails", data=payload, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read(1024 * 1024))
                if response.status >= 300 or not body.get("id"):
                    raise PublishingError("Resend did not confirm delivery.")
                return body["id"]
        except urllib.error.HTTPError as exc:
            raise PublishingError(f"Resend returned HTTP {exc.code}.") from exc
        except (OSError, TimeoutError) as exc:
            raise PublishingError(f"Could not reach Resend ({type(exc).__name__}).") from exc


def fernet_key():
    """Create a key for operators to set as LUMINA_OAUTH_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode("ascii")