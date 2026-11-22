from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .db import CLIP_DIR, TEMP_DIR

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
CAPTION_STYLES = {"bold", "yellow", "neon", "clean"}
CAPTION_POSITIONS = {"top", "middle", "bottom"}
CROP_MODES = {"blur", "fill", "fit"}


def run(command: list[str], timeout: int | None = None) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is not installed. Install FFmpeg and restart ClipForge.") from exc
    if result.returncode:
        message = (result.stderr or result.stdout or "Unknown FFmpeg error")[-2500:]
        raise RuntimeError(message)
    return result.stdout


def probe(path: str | Path) -> dict[str, Any]:
    output = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_type,width,height", "-of", "json", str(path)
    ], timeout=60)
    info = json.loads(output)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise RuntimeError("The uploaded file does not contain a video stream.")
    return {
        "duration": float(info.get("format", {}).get("duration", 0)),
        "size": int(info.get("format", {}).get("size", 0)),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
    }


def extract_audio_chunks(source: str | Path, job_id: str) -> list[tuple[Path, float]]:
    """Create small MP3 chunks so long videos fit the transcription upload limit."""
    folder = TEMP_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    pattern = folder / "audio_%03d.mp3"
    run([
        "ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", "48k", "-f", "segment", "-segment_time", "480",
        "-reset_timestamps", "1", str(pattern)
    ], timeout=60 * 60)
    files = sorted(folder.glob("audio_*.mp3"))
    if not files:
        raise RuntimeError("Could not extract audio from this video. Check that it has an audio track.")
    chunks: list[tuple[Path, float]] = []
    offset = 0.0
    for item in files:
        chunks.append((item, offset))
        duration = probe_audio_duration(item)
        offset += duration
    return chunks


def probe_audio_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(out.strip() or 0)


def ass_time(seconds: float) -> str:
    seconds = max(0, seconds)
    centis = round(seconds * 100)
    hours, rem = divmod(centis, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ").strip()


def caption_groups(words: list[dict], clip_start: float, clip_end: float, max_words: int = 5) -> list[tuple[float, float, str]]:
    chosen = [w for w in words if float(w.get("end", 0)) > clip_start and float(w.get("start", 0)) < clip_end]
    groups: list[tuple[float, float, str]] = []
    pending: list[dict] = []
    for word in chosen:
        pending.append(word)
        raw = " ".join(str(x.get("word", "")).strip() for x in pending).strip()
        terminal = bool(re.search(r"[.!?][\]\)'\"]?$", str(word.get("word", ""))))
        if len(pending) >= max_words or terminal or len(raw) >= 34:
            start = max(0.0, float(pending[0].get("start", clip_start)) - clip_start)
            end = min(clip_end - clip_start, float(pending[-1].get("end", clip_end)) - clip_start)
            if end > start and raw:
                groups.append((start, end, raw))
            pending = []
    if pending:
        raw = " ".join(str(x.get("word", "")).strip() for x in pending).strip()
        start = max(0.0, float(pending[0].get("start", clip_start)) - clip_start)
        end = min(clip_end - clip_start, float(pending[-1].get("end", clip_end)) - clip_start)
        if end > start and raw:
            groups.append((start, end, raw))
    return groups


def create_ass(path: Path, words: list[dict], clip_start: float, clip_end: float, settings: dict, title: str = "") -> None:
    style = settings.get("caption_style", "bold")
    if style not in CAPTION_STYLES:
        style = "bold"
    position = settings.get("caption_position", "bottom")
    if position not in CAPTION_POSITIONS:
        position = "bottom"
    font_size = int(settings.get("caption_size", 66))
    font_size = max(28, min(100, font_size))
    width, height = ASPECTS.get(settings.get("aspect_ratio", "9:16"), ASPECTS["9:16"])
    # Caption sizes are authored against a 1080x1920 vertical canvas.
    # Scale with output height so landscape text does not become oversized.
    scale = height / 1920
    font_size = max(22, round(font_size * scale))
    colors = {
        "bold": ("&H00FFFFFF", "&H00000000", 3),
        "yellow": ("&H0000D7FF", "&H00101010", 3),
        "neon": ("&H00FFF080", "&H00301050", 3),
        "clean": ("&H00FFFFFF", "&H00000000", 1),
    }
    primary, outline, border = colors[style]
    alignment = {"bottom": 2, "middle": 5, "top": 8}[position]
    margin_v = round({"bottom": 150, "middle": 0, "top": 180}[position] * height / 1920)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},{primary},&H000000FF,{outline},&H88000000,1,0,1,{border},1,{alignment},90,90,{margin_v},1
Style: Hook,Arial,{max(34, round(72 * scale))},&H00FFFFFF,&H000000FF,&H00101020,&H99000000,1,0,1,4,2,8,80,80,160,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    if settings.get("hook_overlay") and title.strip():
        hook_end = min(2.8, clip_end - clip_start)
        if hook_end > 0:
            events.append(f"Dialogue: 1,{ass_time(0)},{ass_time(hook_end)},Hook,,0,0,0,,{ass_escape(title[:58])}")
    if settings.get("burn_captions", True) and words:
        max_words = max(2, min(8, int(settings.get("caption_words", 5))))
        for start, end, text in caption_groups(words, clip_start, clip_end, max_words=max_words):
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{ass_escape(text)}")
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def make_video_filter(aspect: str, crop: str) -> str:
    width, height = ASPECTS.get(aspect, ASPECTS["9:16"])
    if crop not in CROP_MODES:
        crop = "blur"
    if crop == "fill":
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    if crop == "fit":
        return f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    return (
        f"split=2[bg][fg];[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=22:10[blur];"
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[front];"
        "[blur][front]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def render_clip(source: str | Path, start: float, end: float, destination: str | Path,
                words: list[dict], settings: dict, title: str = "") -> None:
    duration = end - start
    if duration <= 0:
        raise ValueError("Clip end must be later than clip start.")
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    ass_path = TEMP_DIR / f"{uuid.uuid4().hex}.ass"
    create_ass(ass_path, words, start, end, settings, title)
    aspect = settings.get("aspect_ratio", "9:16")
    crop = settings.get("crop_mode", "blur")
    vf = make_video_filter(aspect, crop)
    # The ASS file is rendered after scaling, so subtitle coordinates match the output canvas.
    subtitle_filter = f"ass={str(ass_path).replace('\\\\', '/').replace(':', '\\:')}"
    if crop == "blur":
        video_filter = vf.replace(";[blur][front]", f";[blur][front]")
        # Keep the two-input graph intact and append subtitles to its final output.
        video_filter += f",{subtitle_filter}"
    else:
        video_filter = f"{vf},{subtitle_filter}"
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
           "-vf", video_filter, "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
    if settings.get("normalize_audio", True):
        cmd += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    cmd.append(str(output))
    try:
        run(cmd, timeout=60 * 30)
    finally:
        ass_path.unlink(missing_ok=True)


def safe_video_name(name: str) -> str:
    if Path(name).name != name or not name.endswith(".mp4"):
        raise ValueError("Invalid video file name")
    return name
