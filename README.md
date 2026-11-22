# ClipForge AI (local MVP)

A real local web app for uploading a video, rendering 9:16 MP4 clips with FFmpeg, saving clipping instructions, maintaining a local clip library, and queueing clips for review on a date.


## Run
1. Install Python 3.10+.
2. Install FFmpeg and ensure `ffmpeg` is on your PATH.
3. In this folder run:
   ```bash
   python -m venv .venv
   # Windows: .venv\\Scripts\\activate
   # macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn app:app --reload
   ```
4. Open http://127.0.0.1:8000

## Current real features
- Local video uploads (MP4, MOV, MKV, WebM, AVI)
- FFmpeg clip rendering from a start time and duration
- Vertical 1080x1920 output with blurred background fill
- MP4 download and preview
- Saved instructions, presets, clip library, and review queue (JSON files stored locally)

## Not yet integrated
- AI-assisted moment discovery from video/audio/transcript or stream chat
- Direct YouTube URL ingest and livestream chat capture
- OAuth-authorized automatic YouTube/Instagram posting

These require model/API credentials and platform permissions. UI does not falsely label sample output as analyzed moments.
