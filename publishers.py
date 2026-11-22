from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from .db import connect

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def encryption_key() -> str:
    return os.getenv("TOKEN_ENCRYPTION_KEY", "").strip()


def encrypt_secret(value: str) -> str:
    key = encryption_key()
    if not key:
        raise RuntimeError("Set TOKEN_ENCRYPTION_KEY before connecting YouTube.")
    from cryptography.fernet import Fernet
    return Fernet(key.encode()).encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    key = encryption_key()
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is missing; saved account credentials cannot be decrypted.")
    from cryptography.fernet import Fernet
    return Fernet(key.encode()).decrypt(value.encode()).decode()


def get_secret(name: str) -> str | None:
    db = connect()
    try:
        row = db.execute("SELECT value FROM app_secrets WHERE name=?", (name,)).fetchone()
        return decrypt_secret(row["value"]) if row else None
    finally:
        db.close()


def save_secret(name: str, value: str) -> None:
    from datetime import datetime, timezone
    encrypted = encrypt_secret(value)
    db = connect()
    try:
        db.execute("INSERT INTO app_secrets(name,value,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                   (name, encrypted, datetime.now(timezone.utc).isoformat()))
        db.commit()
    finally:
        db.close()


def youtube_configured() -> bool:
    if not (os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET") and encryption_key()):
        return False
    try:
        return bool(get_secret("youtube_credentials"))
    except Exception:
        return False


def instagram_configured() -> bool:
    return bool(os.getenv("INSTAGRAM_USER_ID") and os.getenv("INSTAGRAM_ACCESS_TOKEN") and os.getenv("APP_BASE_URL", "").startswith("https://"))


def youtube_oauth_flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow
    client_config = {
        "web": {
            "client_id": os.getenv("GOOGLE_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI")],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=YOUTUBE_SCOPES, state=state)
    flow.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
    return flow


def _youtube_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    raw = get_secret("youtube_credentials")
    if not raw:
        raise RuntimeError("YouTube is not connected. Connect it from Settings first.")
    info = json.loads(raw)
    credentials = Credentials.from_authorized_user_info(info, YOUTUBE_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        save_secret("youtube_credentials", credentials.to_json())
    if not credentials.valid:
        raise RuntimeError("YouTube authorization expired. Disconnect and reconnect your account in Settings.")
    return credentials


def publish_youtube(video_path: str, title: str, caption: str) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    credentials = _youtube_credentials()
    service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    description = (caption or "")[:4900]
    body = {
        "snippet": {
            "title": (title or "New Short")[:100],
            "description": description,
            "categoryId": "22",
        },
        "status": {"privacyStatus": "public"},
    }
    media = MediaFileUpload(video_path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        _, response = request.next_chunk()
    return str(response.get("id", ""))


def publish_instagram(video_url: str, caption: str) -> str:
    user_id = os.getenv("INSTAGRAM_USER_ID", "").strip()
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
    version = os.getenv("META_API_VERSION", "v27.0").strip()
    if not (user_id and token):
        raise RuntimeError("Instagram is not configured. Add INSTAGRAM_USER_ID and INSTAGRAM_ACCESS_TOKEN in your host environment.")
    base = f"https://graph.facebook.com/{version}"
    # Check the account's live publishing quota when the permission is available.
    # Meta's published guide describes a moving 24-hour limit; its account endpoint
    # is the authoritative number for this particular account/app.
    quota = requests.get(f"{base}/{user_id}/content_publishing_limit", params={
        "fields": "config,quota_usage", "access_token": token,
    }, timeout=30)
    if quota.ok:
        quota_data = quota.json()
        config = quota_data.get("config") or {}
        used = quota_data.get("quota_usage")
        total = config.get("quota_total")
        try:
            if total is not None and used is not None and int(used) >= int(total):
                raise RuntimeError("Instagram's 24-hour publishing quota is full. Leave this Reel queued and try again after the quota resets.")
        except (TypeError, ValueError):
            pass

    response = requests.post(f"{base}/{user_id}/media", data={
        "media_type": "REELS", "video_url": video_url, "caption": (caption or "")[:2200],
        "share_to_feed": "true", "access_token": token,
    }, timeout=45)
    data = response.json()
    if response.status_code >= 400 or data.get("error"):
        raise RuntimeError(data.get("error", {}).get("message", f"Instagram container request failed: {response.status_code}"))
    container_id = data.get("id")
    if not container_id:
        raise RuntimeError("Instagram did not return a media container ID.")
    # Instagram fetches the public video asynchronously. Publish only after the container is ready.
    for _ in range(5):
        time.sleep(60)
        check = requests.get(f"{base}/{container_id}", params={
            "fields": "status_code,status", "access_token": token,
        }, timeout=30)
        state = check.json()
        if check.status_code >= 400 or state.get("error"):
            raise RuntimeError(state.get("error", {}).get("message", "Could not check Instagram upload status."))
        code = state.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(state.get("status", "Instagram could not process the Reel."))
    else:
        raise RuntimeError("Instagram is still processing the video after five minutes. Retry the post from the queue.")
    published = requests.post(f"{base}/{user_id}/media_publish", data={
        "creation_id": container_id, "access_token": token,
    }, timeout=45)
    result = published.json()
    if published.status_code >= 400 or result.get("error"):
        raise RuntimeError(result.get("error", {}).get("message", "Instagram publishing failed."))
    return str(result.get("id", container_id))


def account_status() -> dict[str, Any]:
    return {
        "youtube": {
            "connected": youtube_configured(),
            "configured_for_oauth": bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET") and os.getenv("GOOGLE_REDIRECT_URI") and encryption_key()),
        },
        "instagram": {
            "connected": instagram_configured(),
            "configured_for_api": bool(os.getenv("INSTAGRAM_USER_ID") and os.getenv("INSTAGRAM_ACCESS_TOKEN")),
            "needs_public_base_url": not os.getenv("APP_BASE_URL", "").startswith("https://"),
        },
    }
