from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import os
import re
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from clipforge.ai import choose_highlights, transcribe_video
from clipforge.db import CLIP_DIR, DATA_DIR, TEMP_DIR, UPLOAD_DIR, connect, init_db, json_load
from clipforge.links import download_public_video, validate_source_url
from clipforge.media import CAPTION_POSITIONS, CAPTION_STYLES, CROP_MODES, probe, render_clip, safe_video_name
from clipforge.publishers import account_status, publish_instagram, publish_youtube, save_secret, youtube_oauth_flow

ROOT = Path(__file__).resolve().parent
MAX_CONCURRENT_JOBS = max(1, min(2, int(os.getenv("MAX_CONCURRENT_JOBS", "1"))))
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
app = FastAPI(title="ClipForge Free", version="4.0.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


class LoginBody(BaseModel):
    password: str


class LinksBody(BaseModel):
    youtube_url: str = Field(min_length=10, max_length=2048)
    requested_clips: int = Field(default=30, ge=1, le=30) # max clips per batch; no daily cap
    instructions: str = Field(default="Prioritise a clear hook, useful takeaway, surprising moment, story or strong opinion. Keep the speaker's meaning intact.", max_length=4000)
    target_duration: int = Field(default=45, ge=8, le=180)
    min_duration: int = Field(default=15, ge=8, le=180)
    max_duration: int = Field(default=60, ge=8, le=180)
    aspect_ratio: str = "9:16"
    crop_mode: str = "blur"
    burn_captions: bool = True
    caption_style: str = "bold"
    caption_size: int = Field(default=66, ge=28, le=100)
    caption_position: str = "bottom"
    caption_words: int = Field(default=5, ge=2, le=8)
    hook_overlay: bool = False
    normalize_audio: bool = True


class ScheduleBody(BaseModel):
    clip_id: str
    platform: str
    scheduled_at: str
    caption: str = Field(default="", max_length=2200)


class SchedulePairBody(BaseModel):
    clip_id: str
    scheduled_at: str
    caption: str = Field(default="", max_length=2200)


class ScheduleAllBody(BaseModel):
    clip_ids: list[str] = Field(min_length=1, max_length=500)
    scheduled_at: str
    interval_minutes: int = Field(default=60, ge=5, le=1440)


class ClipUpdateBody(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    caption: str | None = Field(default=None, max_length=2200)


class ClipEditBody(BaseModel):
    start: float | None = None
    end: float | None = None
    aspect_ratio: str = "9:16"
    crop_mode: str = "blur"
    burn_captions: bool = True
    caption_style: str = "bold"
    caption_size: int = Field(default=66, ge=28, le=100)
    caption_position: str = "bottom"
    caption_words: int = Field(default=5, ge=2, le=8)
    hook_overlay: bool = False
    normalize_audio: bool = True


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Kolkata")


def _local_day_utc_bounds(instant: datetime | None = None) -> tuple[str, str]:
    local_now = datetime.now(_zone()) if instant is None else instant.astimezone(_zone())
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat(), (start + timedelta(days=1)).astimezone(timezone.utc).isoformat()


def _source_usage(db) -> dict:
    """Informational usage only; there is no local daily clip cap."""
    start, end = _local_day_utc_bounds()
    rows = db.execute(
        """SELECT (SELECT COUNT(*) FROM app_clips c WHERE c.job_id=j.id) AS rendered,
                  j.status,j.settings_json FROM app_jobs j
           WHERE j.source_platform='youtube' AND j.created_at>=? AND j.created_at<?""",
        (start, end),
    ).fetchall()
    used = sum(max(0, int(row['rendered'] or 0)) if row['status'] in {'ready','failed'} else max(1, int(json_load(row['settings_json'], {}).get('requested_clips', 1))) for row in rows)
    return {"date_timezone": APP_TIMEZONE, "clips": {"used": used, "limit": None, "remaining": None}, "total_used": used, "total_limit": None}


def _secret_bytes() -> bytes:
    secret = os.getenv("MEDIA_SIGNING_SECRET") or os.getenv("APP_PASSWORD")
    if secret:
        return secret.encode()
    path = DATA_DIR / ".media_secret"
    if not path.exists():
        path.write_text(uuid.uuid4().hex + uuid.uuid4().hex, encoding="utf-8")
    return path.read_text(encoding="utf-8").strip().encode()


def signed_media_url(filename: str, expires: int | None = None) -> str:
    expires = expires or int((datetime.now(timezone.utc) + timedelta(days=14)).timestamp())
    signature = hmac.new(_secret_bytes(), f"{filename}:{expires}".encode(), hashlib.sha256).hexdigest()
    return f"/media/{filename}?expires={expires}&sig={signature}"


def valid_media_signature(filename: str, expires: int, signature: str) -> bool:
    if expires < int(datetime.now(timezone.utc).timestamp()):
        return False
    expected = hmac.new(_secret_bytes(), f"{filename}:{expires}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def password_enabled() -> bool:
    return bool(os.getenv("APP_PASSWORD"))


def session_signature() -> str:
    return hmac.new(os.getenv("APP_PASSWORD", "").encode(), b"clipforge-session-v1", hashlib.sha256).hexdigest()


@app.middleware("http")
async def protect_dashboard(request: Request, call_next):
    if password_enabled():
        path = request.url.path
        open_paths = ("/api/auth/status", "/api/auth/login", "/api/health", "/auth/youtube/callback", "/static/", "/media/")
        if not any(path == item or path.startswith(item) for item in open_paths):
            token = request.cookies.get("clipforge_session", "")
            if not hmac.compare_digest(token, session_signature()):
                return JSONResponse({"detail": "Please sign in to ClipForge."}, status_code=401)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "public, max-age=60"
    return response


def _clip_dict(row) -> dict:
    item = dict(row)
    item["edit"] = json_load(item.pop("edit_json", "{}"), {})
    item["words"] = json_load(item.pop("words_json", "[]"), [])
    item["url"] = signed_media_url(item["filename"])
    filename = item.pop("filename")
    item["can_render_edit"] = True
    return item


def _save_job_status(job_id: str, status: str, error: str | None = None, finished: bool = False) -> None:
    db = connect()
    db.execute(
        "UPDATE app_jobs SET status=?,error=?,finished_at=? WHERE id=?",
        (status, error[:2000] if error else None, now_iso() if finished else None, job_id),
    )
    db.commit()
    db.close()


def _process_job(job_id: str) -> None:
    db = connect()
    job = db.execute("SELECT * FROM app_jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        db.close()
        return
    # A worker can be interrupted after a few clips have been committed. On
    # restart, the job is re-queued; remove its unfinished outputs first so a
    # retry regenerates one clean batch instead of duplicating those clips.
    previous = db.execute("SELECT filename FROM app_clips WHERE job_id=?", (job_id,)).fetchall()
    if previous:
        db.execute("DELETE FROM app_clips WHERE job_id=?", (job_id,))
        db.execute("UPDATE app_jobs SET transcript_json=? WHERE id=?", ("{}", job_id))
        db.commit()
    db.close()
    for item in previous:
        (CLIP_DIR / item["filename"]).unlink(missing_ok=True)
    settings = json_load(job["settings_json"], {})
    source_path = Path(job["source_path"]) if job["source_path"] else None
    try:
        if not source_path or not source_path.exists():
            _save_job_status(job_id, "downloading")
            platform, source_url = validate_source_url(settings.get("source_url", ""))
            if platform != "youtube":
                raise RuntimeError("ClipForge expects one YouTube video URL as the source for this workflow.")
            source_path = download_public_video(source_url, job_id, UPLOAD_DIR)
            db = connect()
            db.execute("UPDATE app_jobs SET source_path=?,source_name=? WHERE id=?", (str(source_path), source_path.name, job_id))
            db.commit()
            db.close()

        _save_job_status(job_id, "checking_video")
        media_info = probe(source_path)
        if media_info["duration"] < 8:
            raise RuntimeError("This source is shorter than 8 seconds, so it is too short to make an edited short.")

        _save_job_status(job_id, "transcribing_locally")
        segments, words = transcribe_video(str(source_path), job_id)
        transcript = {"segments": segments, "words": words}
        db = connect()
        db.execute("UPDATE app_jobs SET transcript_json=? WHERE id=?", (json.dumps(transcript, ensure_ascii=False), job_id))
        db.commit()
        db.close()

        requested = max(1, min(30, int(settings.get("requested_clips", 30))))
        _save_job_status(job_id, "selecting_highlights")
        candidates = choose_highlights(
            segments, float(media_info["duration"]), settings,
            job["instructions"], requested_count=requested,
        )
        if not candidates:
            raise RuntimeError("No usable highlight could be selected. Try a longer source or reduce the minimum clip length.")

        created_ids: list[str] = []
        for index, candidate in enumerate(candidates, start=1):
            start, end = float(candidate["start"]), float(candidate["end"])
            if end - start < 8:
                continue
            _save_job_status(job_id, f"rendering_clip_{index}_of_{len(candidates)}")
            clip_id = uuid.uuid4().hex[:14]
            filename = f"{clip_id}.mp4"
            destination = CLIP_DIR / filename
            render_clip(source_path, start, end, destination, words, settings, candidate["title"])
            edit_settings = dict(settings)
            edit_settings["source_platform"] = "youtube"
            db = connect()
            db.execute(
                "INSERT INTO app_clips(id,job_id,filename,title,caption,start,end,words_json,edit_json,render_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'ready',?)",
                (clip_id, job_id, filename, candidate["title"], candidate["caption"], start, end,
                 json.dumps(words, ensure_ascii=False), json.dumps(edit_settings), now_iso()),
            )
            db.commit()
            db.close()
            created_ids.append(clip_id)
        if not created_ids:
            raise RuntimeError("No clips were rendered successfully.")
        # Release unused reserved quota when the source is too short to supply the full request.
        settings["requested_clips"] = len(created_ids)
        db = connect()
        db.execute("UPDATE app_jobs SET settings_json=? WHERE id=?", (json.dumps(settings), job_id))
        db.commit()
        db.close()
        _save_job_status(job_id, "ready", None, finished=True)
    except Exception as exc:
        _save_job_status(job_id, "failed", str(exc), finished=True)
    finally:
        shutil.rmtree(TEMP_DIR / job_id, ignore_errors=True)


async def processing_worker() -> None:
    running: set[asyncio.Task] = set()
    while True:
        try:
            if len(running) < MAX_CONCURRENT_JOBS:
                db = connect()
                row = db.execute("SELECT id FROM app_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                if row:
                    claimed = db.execute("UPDATE app_jobs SET status='starting' WHERE id=? AND status='queued'", (row["id"],)).rowcount
                    db.commit()
                    if claimed:
                        task = asyncio.create_task(asyncio.to_thread(_process_job, row["id"]))
                        running.add(task)
                        task.add_done_callback(running.discard)
                db.close()
        except Exception:
            pass
        await asyncio.sleep(1.5)


def _integration_ready(platform: str) -> bool:
    state = account_status().get(platform, {})
    if not state.get("connected"):
        return False
    if platform == "instagram" and not os.getenv("APP_BASE_URL", "").startswith("https://"):
        return False
    return True


def _publish_one(post_id: str) -> None:
    db = connect()
    row = db.execute("SELECT p.*,c.filename,c.title FROM app_posts p JOIN app_clips c ON p.clip_id=c.id WHERE p.id=?", (post_id,)).fetchone()
    db.close()
    if not row:
        return
    try:
        path = CLIP_DIR / row["filename"]
        if not path.exists():
            raise RuntimeError("The edited clip file is missing from local storage.")
        if row["platform"] == "youtube":
            external_id = publish_youtube(str(path), row["title"], row["caption"])
        elif row["platform"] == "instagram":
            expiry = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp())
            video_url = os.getenv("APP_BASE_URL", "").rstrip("/") + signed_media_url(row["filename"], expiry)
            if not video_url.startswith("https://"):
                raise RuntimeError("Instagram auto-publishing needs a public HTTPS address. For a free local setup, use a temporary Cloudflare Tunnel or publish manually.")
            external_id = publish_instagram(video_url, row["caption"])
        else:
            raise RuntimeError("Unknown publishing platform.")
        db = connect()
        db.execute("UPDATE app_posts SET status='published',external_id=?,published_at=?,error=NULL WHERE id=?", (external_id, now_iso(), post_id))
        db.commit()
        db.close()
    except Exception as exc:
        db = connect()
        db.execute("UPDATE app_posts SET status='failed',error=? WHERE id=?", (str(exc)[:2000], post_id))
        db.commit()
        db.close()


async def publishing_worker() -> None:
    active: set[asyncio.Task] = set()
    while True:
        try:
            slots = max(0, 2 - len(active))
            if slots:
                db = connect()
                rows = db.execute("SELECT id,platform FROM app_posts WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at LIMIT 12", (now_iso(),)).fetchall()
                for row in rows:
                    if len(active) >= 2:
                        break
                    if not _integration_ready(row["platform"]):
                        continue  # Leave scheduled until the user connects the platform.
                    claimed = db.execute("UPDATE app_posts SET status='publishing',error=NULL WHERE id=? AND status='scheduled'", (row["id"],)).rowcount
                    if claimed:
                        task = asyncio.create_task(asyncio.to_thread(_publish_one, row["id"]))
                        active.add(task)
                        task.add_done_callback(active.discard)
                db.commit()
                db.close()
        except Exception:
            pass
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    db = connect()
    db.execute("""UPDATE app_jobs SET status='queued'
                  WHERE status IN ('starting','downloading','checking_video','transcribing_locally','selecting_highlight','selecting_highlights')
                     OR status LIKE 'rendering_clip_%'""")
    db.execute("UPDATE app_posts SET status='failed',error='App restarted during a platform upload. Check the platform before retrying to avoid a duplicate.' WHERE status='publishing'")
    db.commit()
    db.close()
    tasks = [asyncio.create_task(processing_worker()), asyncio.create_task(publishing_worker())]
    application.state.worker_tasks = tasks
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


app.router.lifespan_context = lifespan


@app.get("/", response_class=HTMLResponse)
def home():
    return (ROOT / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/auth/status")
def auth_status(request: Request):
    enabled = password_enabled()
    signed_in = (not enabled) or hmac.compare_digest(request.cookies.get("clipforge_session", ""), session_signature())
    return {"password_enabled": enabled, "signed_in": signed_in}


@app.post("/api/auth/login")
def auth_login(body: LoginBody, request: Request):
    expected = os.getenv("APP_PASSWORD", "")
    if not expected:
        return {"ok": True}
    if not hmac.compare_digest(body.password, expected):
        raise HTTPException(401, "Incorrect password.")
    response = JSONResponse({"ok": True})
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
    response.set_cookie("clipforge_session", session_signature(), httponly=True, secure=secure, samesite="strict", max_age=30 * 24 * 3600)
    return response


@app.post("/api/auth/logout")
def auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("clipforge_session")
    return response


@app.get("/api/health")
def health():
    try:
        ffmpeg_ok = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10).returncode == 0
        ffprobe_ok = subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        ffmpeg_ok = ffprobe_ok = False
    return {
        "status": "online", "free_local_processing": True,
        "yt_dlp_installed": importlib.util.find_spec("yt_dlp") is not None,
        "faster_whisper_installed": importlib.util.find_spec("faster_whisper") is not None,
        "ffmpeg": ffmpeg_ok and ffprobe_ok,
        "integrations": account_status(),
        "password_protection": password_enabled(),
    }


@app.get("/api/settings")
def settings():
    db = connect()
    usage = _source_usage(db)
    db.close()
    integrations = account_status()
    return {
        "daily_usage": usage,
        "processing_mode": "Free local tools only: yt-dlp + Faster-Whisper + FFmpeg; no paid AI API",
        "integrations": integrations,
        "instagram_public_url_ready": os.getenv("APP_BASE_URL", "").startswith("https://"),
        "timezone": APP_TIMEZONE,
    }


@app.get("/api/usage")
def usage():
    db = connect()
    result = _source_usage(db)
    db.close()
    return result


@app.post("/api/process-links")
def process_links(body: LinksBody):
    if body.min_duration > body.max_duration or not body.min_duration <= body.target_duration <= body.max_duration:
        raise HTTPException(400, "Target clip seconds must be between the minimum and maximum duration.")
    if body.aspect_ratio not in {"9:16", "1:1", "16:9"} or body.crop_mode not in CROP_MODES:
        raise HTTPException(400, "Choose a supported aspect ratio and crop style.")
    if body.caption_style not in CAPTION_STYLES or body.caption_position not in CAPTION_POSITIONS:
        raise HTTPException(400, "Choose a supported caption style and position.")
    try:
        platform, source_url = validate_source_url(body.youtube_url)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid YouTube link: {exc}")
    if platform != "youtube":
        raise HTTPException(400, "Paste one YouTube video, Shorts or live link. Instagram source links are not used in this workflow.")

    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        settings_data = {
            "source_url": source_url,
            "requested_clips": body.requested_clips,
            "target_duration": body.target_duration, "min_duration": body.min_duration,
            "max_duration": body.max_duration, "aspect_ratio": body.aspect_ratio,
            "crop_mode": body.crop_mode, "burn_captions": body.burn_captions,
            "caption_style": body.caption_style, "caption_size": body.caption_size,
            "caption_position": body.caption_position, "caption_words": body.caption_words,
            "hook_overlay": body.hook_overlay, "normalize_audio": body.normalize_audio,
        }
        job_id = uuid.uuid4().hex[:14]
        db.execute(
            "INSERT INTO app_jobs(id,source_path,source_name,status,instructions,settings_json,created_at,source_platform) VALUES(?,?,?,?,?,?,?,?)",
            (job_id, "", source_url[:220], "queued", body.instructions[:4000], json.dumps(settings_data), now_iso(), "youtube"),
        )
        db.commit()
        after = _source_usage(db)
        return {
            "created": [{"id": job_id, "platform": "youtube", "url": source_url, "requested_clips": body.requested_clips, "status": "queued"}],
            "count": 1,
            "requested_clips": body.requested_clips,
            "crosspost_targets": ["youtube", "instagram"],
            "usage": after,
            "message": f"One YouTube source added. ClipForge will try to create up to {body.requested_clips} different clips for both Instagram and YouTube.",
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/api/jobs")
def list_jobs():
    db = connect()
    jobs = db.execute("SELECT * FROM app_jobs ORDER BY created_at DESC LIMIT 120").fetchall()
    result = []
    for job in jobs:
        clips = db.execute("SELECT * FROM app_clips WHERE job_id=? ORDER BY created_at", (job["id"],)).fetchall()
        result.append({
            "id": job["id"], "source_name": job["source_name"], "source_platform": job["source_platform"],
            "status": job["status"], "created_at": job["created_at"], "error": job["error"],
            "clips": [_clip_dict(c) for c in clips],
        })
    db.close()
    return result


@app.get("/api/clips")
def list_clips():
    db = connect()
    rows = db.execute("SELECT c.*,j.source_platform FROM app_clips c JOIN app_jobs j ON j.id=c.job_id WHERE j.status='ready' ORDER BY c.created_at DESC LIMIT 120").fetchall()
    result = [_clip_dict(row) for row in rows]
    db.close()
    return result


@app.patch("/api/clips/{clip_id}")
def update_clip(clip_id: str, body: ClipUpdateBody):
    db = connect()
    row = db.execute("SELECT id FROM app_clips WHERE id=?", (clip_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Clip not found")
    if body.title is not None:
        db.execute("UPDATE app_clips SET title=? WHERE id=?", (body.title.strip() or "Untitled clip", clip_id))
    if body.caption is not None:
        db.execute("UPDATE app_clips SET caption=? WHERE id=?", (body.caption.strip(), clip_id))
    updated = db.execute("SELECT c.*,j.source_platform FROM app_clips c JOIN app_jobs j ON j.id=c.job_id WHERE c.id=?", (clip_id,)).fetchone()
    db.commit()
    db.close()
    return _clip_dict(updated)


@app.post("/api/clips/{clip_id}/edit")
async def edit_clip(clip_id: str, body: ClipEditBody):
    db = connect()
    row = db.execute("SELECT c.*,j.source_path FROM app_clips c JOIN app_jobs j ON c.job_id=j.id WHERE c.id=?", (clip_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Clip not found")
    start = float(row["start"] if body.start is None else body.start)
    end = float(row["end"] if body.end is None else body.end)
    if start < 0 or end - start < 8 or end - start > 180:
        raise HTTPException(400, "Choose a clip range between 8 and 180 seconds.")
    source_path = Path(row["source_path"])
    if not source_path.exists():
        raise HTTPException(404, "Original source file is missing. Reprocess the source link first.")
    try:
        source_duration = probe(source_path)["duration"]
    except Exception as exc:
        raise HTTPException(400, f"Could not read the original video: {str(exc)[:250]}")
    if end > source_duration:
        raise HTTPException(400, f"Clip end exceeds source video length ({source_duration:.1f} seconds).")
    if body.aspect_ratio not in {"9:16", "1:1", "16:9"} or body.crop_mode not in CROP_MODES:
        raise HTTPException(400, "Unsupported format or crop mode.")
    if body.caption_style not in CAPTION_STYLES or body.caption_position not in CAPTION_POSITIONS:
        raise HTTPException(400, "Unsupported caption style or position.")
    settings_data = body.model_dump()
    settings_data["caption_size"] = max(28, min(100, body.caption_size))
    new_filename = f"{clip_id}_{uuid.uuid4().hex[:6]}.mp4"
    destination = CLIP_DIR / new_filename
    words = json_load(row["words_json"], [])
    try:
        await asyncio.to_thread(render_clip, source_path, start, end, destination, words, settings_data, row["title"])
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(500, f"Could not render this edit: {str(exc)[:500]}")
    db = connect()
    old_filename = row["filename"]
    db.execute("UPDATE app_clips SET filename=?,start=?,end=?,edit_json=?,render_status='ready',render_error=NULL WHERE id=?", (new_filename, start, end, json.dumps(settings_data), clip_id))
    updated = db.execute("SELECT c.*,j.source_platform FROM app_clips c JOIN app_jobs j ON j.id=c.job_id WHERE c.id=?", (clip_id,)).fetchone()
    db.commit()
    db.close()
    (CLIP_DIR / old_filename).unlink(missing_ok=True)
    return _clip_dict(updated)


def _schedule_datetime(value: str) -> datetime:
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Choose a valid publishing date and time.")
    if when.tzinfo is None:
        raise HTTPException(400, "The schedule must include a timezone.")
    when = when.astimezone(timezone.utc)
    if when <= datetime.now(timezone.utc) + timedelta(seconds=10):
        raise HTTPException(400, "Choose a future time at least 10 seconds from now.")
    if when > datetime.now(timezone.utc) + timedelta(days=60):
        raise HTTPException(400, "Schedule posts within the next 60 days.")
    return when


def _check_pair_daily_limits(db, slots: list[datetime]) -> None:
    # No app-imposed daily publishing/scheduling cap. Platform API limits still apply.
    return None


@app.post("/api/schedule-pair")
def schedule_pair(body: SchedulePairBody):
    when = _schedule_datetime(body.scheduled_at)
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        clip = db.execute("SELECT c.id,c.caption,j.status FROM app_clips c JOIN app_jobs j ON j.id=c.job_id WHERE c.id=?", (body.clip_id,)).fetchone()
        if not clip:
            raise HTTPException(404, "Clip not found")
        if clip["status"] != "ready":
            raise HTTPException(409, "Wait until the full clip batch is ready before scheduling it.")
        _check_pair_daily_limits(db, [when])
        caption = (body.caption or clip["caption"] or "")[:2200]
        ids = []
        for platform in ("instagram", "youtube"):
            post_id = uuid.uuid4().hex[:14]
            db.execute("INSERT INTO app_posts(id,clip_id,platform,scheduled_at,caption,status,created_at) VALUES(?,?,?,?,?,'scheduled',?)", (post_id, body.clip_id, platform, when.isoformat(), caption, now_iso()))
            ids.append(post_id)
        db.commit()
        return {"clip_id": body.clip_id, "scheduled_posts": ids, "count": 2, "platforms": ["instagram", "youtube"], "scheduled_at": when.isoformat()}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/api/schedule-all")
def schedule_all_both(body: ScheduleAllBody):
    if len(set(body.clip_ids)) != len(body.clip_ids):
        raise HTTPException(400, "The clip list contains duplicates.")
    first = _schedule_datetime(body.scheduled_at)
    slots = [first + timedelta(minutes=i * body.interval_minutes) for i in range(len(body.clip_ids))]
    if slots[-1] > datetime.now(timezone.utc) + timedelta(days=60):
        raise HTTPException(400, "The final post would be more than 60 days away. Increase or reduce the batch size.")
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in body.clip_ids)
        rows = db.execute(f"SELECT c.id,c.caption,j.status FROM app_clips c JOIN app_jobs j ON j.id=c.job_id WHERE c.id IN ({placeholders})", tuple(body.clip_ids)).fetchall()
        by_id = {row["id"]: row for row in rows}
        missing = [clip_id for clip_id in body.clip_ids if clip_id not in by_id]
        if missing:
            raise HTTPException(404, "One or more clips no longer exist. Refresh Clip Library and try again.")
        if any(by_id[clip_id]["status"] != "ready" for clip_id in body.clip_ids):
            raise HTTPException(409, "Wait until every clip batch is ready before scheduling it.")
        _check_pair_daily_limits(db, slots)
        created = []
        for clip_id, when in zip(body.clip_ids, slots):
            caption = (by_id[clip_id]["caption"] or "")[:2200]
            for platform in ("instagram", "youtube"):
                post_id = uuid.uuid4().hex[:14]
                db.execute("INSERT INTO app_posts(id,clip_id,platform,scheduled_at,caption,status,created_at) VALUES(?,?,?,?,?,'scheduled',?)", (post_id, clip_id, platform, when.isoformat(), caption, now_iso()))
                created.append({"id": post_id, "clip_id": clip_id, "platform": platform, "scheduled_at": when.isoformat()})
        db.commit()
        return {"clips": len(body.clip_ids), "posts_created": len(created), "posts": created, "platforms": ["instagram", "youtube"], "message": f"Scheduled {len(body.clip_ids)} clips to both platforms ({len(created)} posts total)."}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/api/schedule")
def schedule_post(body: ScheduleBody):
    if body.platform not in {"instagram", "youtube"}:
        raise HTTPException(400, "Choose Instagram or YouTube.")
    try:
        when = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Choose a valid publishing date and time.")
    if when.tzinfo is None:
        raise HTTPException(400, "The schedule must include a timezone.")
    when = when.astimezone(timezone.utc)
    if when <= datetime.now(timezone.utc) + timedelta(seconds=10):
        raise HTTPException(400, "Choose a future time at least 10 seconds from now.")
    if when > datetime.now(timezone.utc) + timedelta(days=60):
        raise HTTPException(400, "Schedule posts within the next 60 days.")
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        clip = db.execute("SELECT c.id,j.status FROM app_clips c JOIN app_jobs j ON j.id=c.job_id WHERE c.id=?", (body.clip_id,)).fetchone()
        if not clip:
            raise HTTPException(404, "Clip not found")
        if clip["status"] != "ready":
            raise HTTPException(409, "Wait until the full clip batch is ready before scheduling it.")
        post_id = uuid.uuid4().hex[:14]
        db.execute("INSERT INTO app_posts(id,clip_id,platform,scheduled_at,caption,status,created_at) VALUES(?,?,?,?,?,'scheduled',?)", (post_id, body.clip_id, body.platform, when.isoformat(), body.caption[:2200], now_iso()))
        db.commit()
        return {"id": post_id, "status": "scheduled", "message": "Saved to your schedule. Auto-publishing starts when the relevant account is connected."}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/api/posts")
def list_posts():
    db = connect()
    rows = db.execute("SELECT p.*,c.title,c.filename FROM app_posts p LEFT JOIN app_clips c ON p.clip_id=c.id ORDER BY CASE WHEN p.status IN ('scheduled','publishing') THEN 0 ELSE 1 END,p.scheduled_at ASC LIMIT 200").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["url"] = signed_media_url(item["filename"]) if item.get("filename") else None
        result.append(item)
    db.close()
    return result


@app.delete("/api/posts/{post_id}")
def cancel_post(post_id: str):
    db = connect()
    row = db.execute("SELECT status FROM app_posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Scheduled post not found")
    if row["status"] not in {"scheduled", "failed"}:
        db.close()
        raise HTTPException(400, "Only scheduled or failed posts can be cancelled.")
    db.execute("UPDATE app_posts SET status='cancelled' WHERE id=?", (post_id,))
    db.commit()
    db.close()
    return {"ok": True}


@app.post("/api/posts/{post_id}/retry")
def retry_post(post_id: str):
    db = connect()
    row = db.execute("SELECT status FROM app_posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Post not found")
    if row["status"] != "failed":
        db.close()
        raise HTTPException(400, "Only failed posts can be retried.")
    db.execute("UPDATE app_posts SET status='scheduled',scheduled_at=?,error=NULL WHERE id=?", (now_iso(), post_id))
    db.commit()
    db.close()
    return {"ok": True, "message": "Post returned to the publishing queue."}


@app.get("/media/{name}")
def media(name: str, expires: int = 0, sig: str = ""):
    try:
        safe_video_name(name)
    except ValueError:
        raise HTTPException(400, "Invalid video file name.")
    if not valid_media_signature(name, expires, sig):
        raise HTTPException(403, "This media link is invalid or expired. Refresh the dashboard.")
    path = CLIP_DIR / name
    if not path.exists():
        raise HTTPException(404, "Video not found.")
    return FileResponse(path, media_type="video/mp4", filename=name, content_disposition_type="inline", headers={"Cache-Control": "private, max-age=300"})


@app.get("/auth/youtube/login")
def youtube_login():
    if not (os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET") and os.getenv("GOOGLE_REDIRECT_URI") and os.getenv("TOKEN_ENCRYPTION_KEY")):
        raise HTTPException(400, "YouTube auto-publishing is optional. Add Google OAuth credentials and TOKEN_ENCRYPTION_KEY to enable it.")
    state = uuid.uuid4().hex + uuid.uuid4().hex
    db = connect()
    db.execute("INSERT INTO app_oauth_states(state,created_at) VALUES(?,?)", (state, now_iso()))
    db.commit()
    db.close()
    flow = youtube_oauth_flow(state)
    url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    return RedirectResponse(url)


@app.get("/auth/youtube/callback")
def youtube_callback(request: Request, state: str = "", error: str | None = None):
    if error:
        return RedirectResponse("/?oauth_error=" + quote(error[:120], safe="") + "#settings")
    db = connect()
    row = db.execute("SELECT state,created_at FROM app_oauth_states WHERE state=?", (state,)).fetchone()
    db.execute("DELETE FROM app_oauth_states WHERE state=?", (state,))
    db.commit()
    db.close()
    if not row:
        raise HTTPException(400, "OAuth state expired or invalid. Start the connection again.")
    try:
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - created > timedelta(minutes=10):
            raise HTTPException(400, "OAuth state expired. Start again.")
        code = request.query_params.get("code", "")
        if not code:
            raise RuntimeError("Google did not return an authorization code.")
        flow = youtube_oauth_flow(state)
        flow.fetch_token(code=code)
        save_secret("youtube_credentials", flow.credentials.to_json())
    except Exception as exc:
        return RedirectResponse("/?oauth_error=" + quote(str(exc)[:140], safe="") + "#settings")
    return RedirectResponse("/?connected=youtube#settings")


@app.post("/api/youtube/disconnect")
def youtube_disconnect():
    db = connect()
    db.execute("DELETE FROM app_secrets WHERE name='youtube_credentials'")
    db.commit()
    db.close()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")), reload=False)
