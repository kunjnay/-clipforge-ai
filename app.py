
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from datetime import datetime
from uuid import uuid4
from urllib.parse import urlparse

app = FastAPI(title="ClipForge AI")

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

# Temporary job storage (resets when the server restarts)
jobs = []

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(INDEX_FILE)

@app.get("/api/status")
def status():
    return {
        "app": "ClipForge AI",
        "status": "online",
        "version": "1.0",
        "features": {
            "link_input": True,
            "job_queue": True,
            "ai_clipping": False,
            "youtube_publishing": False,
            "instagram_publishing": False
        }
    }

@app.get("/api/jobs")
def get_jobs():
    return {"jobs": jobs}

@app.post("/api/jobs")
def create_job(data: dict):
    video_url = str(data.get("url", "")).strip()

    if not video_url:
        raise HTTPException(status_code=400, detail="Video URL is required")

    parsed = urlparse(video_url)

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    if not any(host in parsed.netloc.lower() for host in
               ("youtube.com", "youtu.be")):
        raise HTTPException(status_code=400, detail="Please use a YouTube URL")

    job = {
        "id": str(uuid4()),
        "url": video_url,
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "schedule": data.get("schedule"),
        "platforms": data.get("platforms", []),
    }

    jobs.append(job)

    return {
        "success": True,
        "message": "Job added to queue",
        "job": job
    }

@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    for index, job in enumerate(jobs):
        if job["id"] == job_id:
            jobs.pop(index)
            return {"success": True, "message": "Job deleted"}

    raise HTTPException(status_code=404, detail="Job not found")

@app.get("/health")
def health():
    return {"status": "healthy"}
    
