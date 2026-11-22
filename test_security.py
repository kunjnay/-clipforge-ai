from urllib.parse import parse_qs, urlparse

from app import signed_media_url, valid_media_signature


def test_signed_media_link_round_trip():
    url = signed_media_url("example.mp4", 4_102_444_800)
    parsed = urlparse(url)
    args = parse_qs(parsed.query)
    assert valid_media_signature("example.mp4", int(args["expires"][0]), args["sig"][0])
    assert not valid_media_signature("other.mp4", int(args["expires"][0]), args["sig"][0])


def test_path_traversal_is_rejected():
    from clipforge.media import safe_video_name
    import pytest
    with pytest.raises(ValueError):
        safe_video_name("../escape.mp4")
