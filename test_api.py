import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import app as app_module
from app import LinksBody, ScheduleAllBody, SchedulePairBody, process_links, schedule_all_both, schedule_pair
from clipforge.db import connect, init_db


def clean_db():
    init_db()
    db = connect()
    for table in ("app_posts", "app_clips", "app_jobs", "app_oauth_states", "app_secrets"):
        db.execute(f"DELETE FROM {table}")
    db.commit()
    db.close()


def add_clip(clip_id: str, job_id: str = "job-for-scheduling"):
    db = connect()
    if not db.execute("SELECT id FROM app_jobs WHERE id=?", (job_id,)).fetchone():
        db.execute(
            "INSERT INTO app_jobs(id,source_path,source_name,status,instructions,settings_json,created_at,source_platform) VALUES(?,?,?,?,?,?,?,?)",
            (job_id, "", "https://youtu.be/abcdef12345", "ready", "", "{}", datetime.now(timezone.utc).isoformat(), "youtube"),
        )
    db.execute(
        "INSERT INTO app_clips(id,job_id,filename,title,caption,start,end,words_json,edit_json,render_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'ready',?)",
        (clip_id, job_id, f"{clip_id}.mp4", f"Title {clip_id}", f"Caption {clip_id}", 0, 30, "[]", "{}", datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    db.close()


def test_submit_one_youtube_source_for_up_to_30_clips():
    clean_db()
    result = process_links(LinksBody(youtube_url="https://youtu.be/abcdef12345", requested_clips=30))
    assert result["count"] == 1
    assert result["requested_clips"] == 30
    assert result["crosspost_targets"] == ["youtube", "instagram"]
    assert result["usage"]["clips"]["used"] == 30
    db = connect()
    rows = db.execute("SELECT source_platform,status,settings_json FROM app_jobs").fetchall()
    db.close()
    assert len(rows) == 1 and rows[0]["source_platform"] == "youtube" and rows[0]["status"] == "queued"
    assert json.loads(rows[0]["settings_json"])["requested_clips"] == 30


def test_instagram_link_is_not_accepted_as_the_source():
    clean_db()
    with pytest.raises(HTTPException) as exc:
        process_links(LinksBody(youtube_url="https://instagram.com/reel/ABC123/", requested_clips=5))
    assert exc.value.status_code == 400
    assert "YouTube" in exc.value.detail


def test_no_daily_clip_cap_allows_multiple_batches():
    clean_db()
    process_links(LinksBody(youtube_url="https://youtu.be/abcdef12345", requested_clips=30))
    result = process_links(LinksBody(youtube_url="https://youtu.be/zyxwvu98765", requested_clips=30))
    assert result["requested_clips"] == 30


def test_job_pipeline_renders_multiple_highlights_from_one_source(monkeypatch, tmp_path):
    clean_db()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake input")
    db = connect()
    job_id = "jobpipeline01"
    settings = {
        "source_url": "https://youtu.be/abcdef12345", "requested_clips": 3,
        "min_duration": 15, "max_duration": 30, "target_duration": 30,
        "aspect_ratio": "9:16", "crop_mode": "blur", "burn_captions": True,
        "caption_style": "bold", "caption_size": 66, "caption_position": "bottom",
        "caption_words": 5, "hook_overlay": False, "normalize_audio": True,
    }
    db.execute("INSERT INTO app_jobs(id,source_path,source_name,status,instructions,settings_json,created_at,source_platform) VALUES(?,?,?,?,?,?,?,?)", (job_id, "", "test video", "queued", "find the point", json.dumps(settings), datetime.now(timezone.utc).isoformat(), "youtube"))
    db.commit()
    db.close()

    monkeypatch.setattr(app_module, "download_public_video", lambda *_args: source)
    monkeypatch.setattr(app_module, "probe", lambda *_args: {"duration": 150.0, "size": 100, "width": 1280, "height": 720})
    segments = [
        {"start": 0, "end": 29, "text": "Here is the first important secret and why it works!"},
        {"start": 40, "end": 69, "text": "The second surprising lesson is actually very useful!"},
        {"start": 90, "end": 119, "text": "Finally this is the third reason you should remember."},
    ]
    words = [{"start": 1, "end": 1.3, "word": "Here"}]
    monkeypatch.setattr(app_module, "transcribe_video", lambda *_args: (segments, words))
    rendered = []
    def fake_render(_src, start, end, dest, *_args):
        rendered.append((start, end))
        dest.write_bytes(b"mp4 fake")
    monkeypatch.setattr(app_module, "render_clip", fake_render)
    app_module._process_job(job_id)
    db = connect()
    job = db.execute("SELECT status,error FROM app_jobs WHERE id=?", (job_id,)).fetchone()
    clips = db.execute("SELECT * FROM app_clips WHERE job_id=? ORDER BY start", (job_id,)).fetchall()
    db.close()
    assert job["status"] == "ready", job["error"]
    assert len(clips) == 3 and len(rendered) == 3
    assert all(clip["filename"].endswith(".mp4") for clip in clips)
    assert len({(clip["start"], clip["end"]) for clip in clips}) == 3


def test_schedule_one_clip_creates_matching_instagram_and_youtube_posts():
    clean_db()
    add_clip("clip001")
    when = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    result = schedule_pair(SchedulePairBody(clip_id="clip001", scheduled_at=when))
    assert result["count"] == 2
    db = connect()
    posts = db.execute("SELECT platform,scheduled_at FROM app_posts ORDER BY platform").fetchall()
    db.close()
    assert [row["platform"] for row in posts] == ["instagram", "youtube"]
    assert posts[0]["scheduled_at"] == posts[1]["scheduled_at"]


def test_bulk_schedule_creates_equal_posts_for_both_platforms():
    clean_db()
    add_clip("clip001")
    add_clip("clip002")
    when = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    result = schedule_all_both(ScheduleAllBody(clip_ids=["clip001", "clip002"], scheduled_at=when, interval_minutes=60))
    assert result["clips"] == 2 and result["posts_created"] == 4
    db = connect()
    counts = {row["platform"]: row["n"] for row in db.execute("SELECT platform,COUNT(*) n FROM app_posts GROUP BY platform").fetchall()}
    pair_times = db.execute("SELECT clip_id,COUNT(DISTINCT scheduled_at) n,COUNT(*) c FROM app_posts GROUP BY clip_id").fetchall()
    db.close()
    assert counts == {"instagram": 2, "youtube": 2}
    assert all(row["n"] == 1 and row["c"] == 2 for row in pair_times)


def test_bulk_schedule_has_no_app_imposed_daily_cap():
    clean_db()
    for i in range(40):
        add_clip(f"clip{i:03d}")
    when_dt = datetime.now(timezone.utc) + timedelta(hours=2)
    result = schedule_all_both(ScheduleAllBody(clip_ids=[f"clip{i:03d}" for i in range(40)], scheduled_at=when_dt.isoformat(), interval_minutes=10))
    assert result["clips"] == 40 and result["posts_created"] == 80
