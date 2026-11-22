from pathlib import Path
import pytest
from clipforge.links import validate_source_url


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abcdef12345",
    "https://youtu.be/abcdef12345",
    "https://www.youtube.com/shorts/abcdef12345",
    "https://www.youtube.com/live/abcdef12345",
])
def test_youtube_video_links(url):
    assert validate_source_url(url)[0] == "youtube"



@pytest.mark.parametrize("url", [
    "http://www.youtube.com/watch?v=abcdef12345",
    "https://youtube.com/playlist?list=PLabc",
    "https://www.instagram.com/reel/ABC123/",
    "https://www.instagram.com/username/",
    "https://example.com/video.mp4",
    "https://user:pass@youtube.com/watch?v=abcdef12345",
    "javascript:alert(1)",
])
def test_rejects_unsupported_or_unsafe_links(url):
    with pytest.raises(ValueError):
        validate_source_url(url)


def test_rejects_oversized_url():
    with pytest.raises(ValueError, match="too long"):
        validate_source_url("https://youtube.com/watch?v=" + "a" * 2100)


def test_downloader_uses_yt_dlp_without_cookie_access(monkeypatch, tmp_path):
    import sys
    import types
    from clipforge.links import download_public_video

    observed = {}
    class FakeYoutubeDL:
        def __init__(self, options):
            observed.update(options)
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def extract_info(self, url, download=True):
            assert download is True
            assert url.startswith("https://youtu.be/")
            path = Path(observed["outtmpl"].replace("%(ext)s", "mp4"))
            path.write_bytes(b"fake-mp4")
            self.result_path = path
            return {"id": "test", "ext": "mp4"}
        def prepare_filename(self, _info):
            return self.result_path

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    output = download_public_video("https://youtu.be/abcdef12345", "job123", tmp_path)
    assert output.exists() and output.read_bytes() == b"fake-mp4"
    assert observed["noplaylist"] is True
    assert "cookiefile" not in observed and "cookiesfrombrowser" not in observed
