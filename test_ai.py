from clipforge.ai import choose_highlight


def test_choose_highlight_returns_valid_duration_and_caption():
    segments = [
        {"start": 0, "end": 8, "text": "Welcome to the show. We are talking about today's topic."},
        {"start": 20, "end": 30, "text": "Here is the secret mistake people always make. It is important!"},
        {"start": 30, "end": 42, "text": "The best way is to focus on one useful thing. Why? Because it works."},
        {"start": 65, "end": 80, "text": "Thanks for watching. See you next time."},
    ]
    result = choose_highlight(segments, 100, {"min_duration": 15, "max_duration": 60, "target_duration": 30}, "Find the secret mistake")
    assert 0 <= result["start"] < result["end"] <= 100
    assert 15 <= result["end"] - result["start"] <= 60
    assert result["title"]
    assert "#shorts" in result["caption"]


def test_instagram_short_keeps_full_source_when_under_max():
    result = choose_highlight([], 26.5, {"source_platform": "instagram", "min_duration": 15, "max_duration": 60, "target_duration": 45})
    assert result["start"] == 0
    assert result["end"] == 26.5


def test_short_source_is_not_extended():
    result = choose_highlight([], 12, {"min_duration": 8, "max_duration": 60, "target_duration": 45})
    assert result["end"] == 12


def test_faster_whisper_adapter_uses_local_model(monkeypatch):
    import sys
    import types
    from types import SimpleNamespace
    from clipforge import ai

    class FakeModel:
        def __init__(self, *args, **kwargs):
            assert args[0] == "base"
            assert kwargs["device"] == "cpu"
        def transcribe(self, _source, **kwargs):
            assert kwargs["word_timestamps"] is True
            seg = SimpleNamespace(
                start=1.0, end=2.0, text=" Hello world!",
                words=[SimpleNamespace(start=1.0, end=1.4, word=" Hello"), SimpleNamespace(start=1.4, end=2.0, word=" world!")],
            )
            return iter([seg]), SimpleNamespace(language="en")

    monkeypatch.setenv("WHISPER_MODEL_SIZE", "base")
    monkeypatch.setenv("WHISPER_DEVICE", "cpu")
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    ai._whisper_model.cache_clear()
    try:
        segments, words = ai.transcribe_video("source.mp4", "job123")
    finally:
        ai._whisper_model.cache_clear()
    assert segments == [{"start": 1.0, "end": 2.0, "text": "Hello world!"}]
    assert len(words) == 2


def test_choose_up_to_30_distinct_highlights_from_one_long_source():
    from clipforge.ai import choose_highlights

    settings = {"min_duration": 15, "max_duration": 45, "target_duration": 45}
    clips = choose_highlights([], 45 * 32, settings, "find useful moments", requested_count=30)
    assert len(clips) == 30
    assert all(44.99 <= clip["end"] - clip["start"] <= 45.01 for clip in clips)
    chronological = sorted(clips, key=lambda clip: clip["start"])
    assert all(a["end"] <= b["start"] + 0.001 for a, b in zip(chronological, chronological[1:]))


def test_short_source_produces_fewer_than_requested_without_duplicate_sections():
    from clipforge.ai import choose_highlights

    clips = choose_highlights([], 100, {"min_duration": 15, "max_duration": 45, "target_duration": 45}, requested_count=30)
    assert len(clips) == 2
    assert clips[0]["end"] <= clips[1]["start"] + 0.001


def test_source_shorter_than_minimum_produces_no_clips():
    from clipforge.ai import choose_highlights

    assert choose_highlights([], 7, {"min_duration": 15, "max_duration": 45, "target_duration": 30}, requested_count=5) == []
