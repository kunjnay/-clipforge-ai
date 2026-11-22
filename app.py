
import os
import uuid
import shutil
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from moviepy import VideoFileClip

app = FastAPI(title="ClipForge AI")

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
CLIP_DIR = BASE_DIR / "clips"

UPLOAD_DIR.mkdir(exist_ok=True)
CLIP_DIR.mkdir(exist_ok=True)

jobs = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    index_file = BASE_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(404, "index.html missing")
    return FileResponse(index_file)

@app.get("/api/status")
def status():
    return {
        "app": "ClipForge AI",
        "status": "online",
        "features": {
            "video_upload": True,
            "clip_creation": True,
            "ai_highlight_detection": False,
            "youtube_publishing": False,
            "instagram_publishing": False
        }
    }

@app.get("/api/jobs")
def get_jobs():
    return {"jobs": jobs}

@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    allowed = {".mp4", ".mov", ".mkv", ".webm"}
    ext = Path(file.filename or "").suffix.lower()

    if ext not in allowed:
        raise HTTPException(400, "Unsupported video format")

    job_id = str(uuid.uuid4())
    input_path = UPLOAD_DIR / f"{job_id}{ext}"

    with input_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    job = {
        "id": job_id,
        "filename": file.filename,
        "status": "processing",
        "created_at": datetime.now().isoformat(),
        "clips": []
    }
    jobs.append(job)

    try:
        with VideoFileClip(str(input_path)) as video:
            duration = video.duration

            if duration <= 0:
                raise ValueError("Video has no duration")

            # Split into up to 3 segments, each at most 30 seconds
            segment_length = min(30, duration)
            starts = [0]

            if duration > segment_length:
                starts.append(max(0, (duration - segment_length) // 2))

            if duration > segment_length * 2:
                starts.append(max(0, duration - segment_length))

            for i, start in enumerate(starts[:3]):
                end = min(start + segment_length, duration)
                segment = video.subclipped(start, end)

                # Convert to portrait format for Shorts/Reels
                portrait = segment.resized(height=1920)

                if portrait.w >= 1080:
                    x1 = (portrait.w - 1080) / 2
                    portrait = portrait.cropped(
                        x1=x1, y1=0, width=1080, height=1920
                    )

                output_path = CLIP_DIR / f"{job_id}_clip{i+1}.mp4"

                portrait.write_videofile(
                    str(output_path),
                    codec="libx264",
                    audio_codec="aac",
                    fps=30,
                    logger=None
                )

                job["clips"].append(
                    f"/clips/{output_path.name}"
                )

                segment.close()
                portrait.close()

        job["status"] = "completed"

    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)

    return job

@app.get("/clips/{filename}")
def get_clip(filename: str):
    path = CLIP_DIR / filename

    if not path.exists() or path.parent != CLIP_DIR:
        raise HTTPException(404, "Clip not found")

    return FileResponse(path, media_type="video/mp4")

@app.get("/health")
def health():
    return {"status": "healthy"}
                
