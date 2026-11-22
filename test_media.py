from pathlib import Path
import shutil
import subprocess
import pytest

from clipforge.media import caption_groups, create_ass, make_video_filter, render_clip, safe_video_name


def test_caption_groups_respect_clip_range_and_punctuation():
    words = [
        {"start": 1.0, "end": 1.2, "word": "Hello"}, {"start": 1.2, "end": 1.4, "word": "there,"},
        {"start": 1.4, "end": 1.6, "word": "this"}, {"start": 1.6, "end": 1.8, "word": "is"},
        {"start": 1.8, "end": 2.1, "word": "ClipForge!"}, {"start": 2.2, "end": 2.4, "word": "Outside"},
    ]
    groups = caption_groups(words, 1.0, 2.1, max_words=5)
    assert len(groups) == 1 and groups[0] == (0.0, 1.1, "Hello there, this is ClipForge!")


def test_crop_modes_return_expected_dimensions():
    assert "scale=1080:1920" in make_video_filter("9:16", "fill")
    assert "pad=1080:1080" in make_video_filter("1:1", "fit")
    assert "boxblur" in make_video_filter("16:9", "blur")


def test_ass_subtitle_file_contains_caption_and_output_resolution(tmp_path: Path):
    target = tmp_path / "captions.ass"
    words = [{"start": 10.0, "end": 10.3, "word": "Make"}, {"start": 10.3, "end": 10.6, "word": "it"}, {"start": 10.6, "end": 11.0, "word": "happen."}]
    create_ass(target, words, 10.0, 14.0, {"caption_style": "yellow", "caption_position": "bottom", "caption_size": 66, "burn_captions": True, "hook_overlay": True, "aspect_ratio": "16:9"}, "Let's go")
    content = target.read_text()
    assert "PlayResX: 1920" in content and "PlayResY: 1080" in content
    assert "Hook" in content and "Make it happen." in content


def test_caption_word_count_customisable(tmp_path: Path):
    target = tmp_path / "captions-short.ass"
    words = [{"start": 1.0, "end": 1.2, "word": "One"}, {"start": 1.2, "end": 1.4, "word": "two"}, {"start": 1.4, "end": 1.6, "word": "three"}, {"start": 1.6, "end": 1.8, "word": "four"}]
    create_ass(target, words, 1.0, 2.0, {"burn_captions": True, "caption_words": 2}, "")
    dialogue = [line for line in target.read_text().splitlines() if line.startswith("Dialogue: 0,")]
    assert len(dialogue) == 2 and "One two" in dialogue[0] and "three four" in dialogue[1]


def test_safe_video_name_rejects_traversal():
    assert safe_video_name("safe-file.mp4") == "safe-file.mp4"
    with pytest.raises(ValueError):
        safe_video_name("../unsafe.mp4")


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg not installed in test environment")
def test_real_ffmpeg_vertical_render(tmp_path: Path):
    source = tmp_path / "synthetic.mp4"
    result = subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24", "-f", "lavfi", "-i", "sine=frequency=440:duration=10", "-t", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-1000:]
    from clipforge import db as db_module
    old_temp = db_module.TEMP_DIR
    db_module.TEMP_DIR = tmp_path / "tmp"
    db_module.TEMP_DIR.mkdir(exist_ok=True)
    try:
        output = tmp_path / "rendered.mp4"
        words = [
            {"start": 0.5, "end": 0.8, "word": "This"},
            {"start": 0.8, "end": 1.1, "word": "is"},
            {"start": 1.1, "end": 1.5, "word": "tested."},
        ]
        render_clip(source, 0, 9, output, words, {
            "aspect_ratio": "9:16", "crop_mode": "fit", "burn_captions": True,
            "caption_style": "bold", "caption_position": "bottom", "caption_size": 60,
            "caption_words": 2, "hook_overlay": True, "normalize_audio": True,
        }, "Caption test")
        assert output.exists() and output.stat().st_size > 1000
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "json", str(output)], capture_output=True, text=True)
        assert probe.returncode == 0
        import json
        streams = json.loads(probe.stdout)["streams"]
        assert any(s.get("codec_type") == "video" and s.get("width") == 1080 and s.get("height") == 1920 for s in streams)
        assert any(s.get("codec_type") == "audio" for s in streams)
    finally:
        db_module.TEMP_DIR = old_temp
