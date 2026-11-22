"""Offline speech transcription and deterministic highlight selection (no paid AI API)."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


def _value(obj: Any, name: str, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


@lru_cache(maxsize=2)
def _whisper_model(model_size: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Local speech-to-text is not installed. Run: pip install -r requirements.txt") from exc
    device = os.getenv("WHISPER_DEVICE", "cpu").strip().lower()
    compute = os.getenv("WHISPER_COMPUTE_TYPE", "int8" if device == "cpu" else "float16")
    return WhisperModel(model_size, device=device, compute_type=compute, download_root=os.getenv("WHISPER_CACHE_DIR") or None)


def transcribe_video(source: str, job_id: str) -> tuple[list[dict], list[dict]]:
    """Transcribe speech on this computer; the model is downloaded once and cached locally."""
    model_size = os.getenv("WHISPER_MODEL_SIZE", "base").strip() or "base"
    model = _whisper_model(model_size)
    segments_iter, _info = model.transcribe(
        str(source), beam_size=3, word_timestamps=True, vad_filter=True,
        condition_on_previous_text=False,
    )
    segments: list[dict] = []
    words: list[dict] = []
    for segment in segments_iter:
        text = str(_value(segment, "text", "") or "").strip()
        start = float(_value(segment, "start", 0) or 0)
        end = float(_value(segment, "end", 0) or 0)
        if text and end > start:
            segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        for word in (_value(segment, "words", []) or []):
            token = str(_value(word, "word", "") or "").strip()
            w_start = float(_value(word, "start", 0) or 0)
            w_end = float(_value(word, "end", 0) or 0)
            if token and w_end > w_start:
                words.append({"start": round(w_start, 3), "end": round(w_end, 3), "word": token})
    return segments, words


_HOOK_WORDS = {
    "secret", "mistake", "never", "always", "best", "worst", "why", "how", "what",
    "truth", "reason", "amazing", "insane", "important", "finally", "actually", "because",
    "learned", "realize", "surprising", "wrong", "right", "easy", "hard", "stop", "start",
    "ध्यान", "क्यों", "कैसे", "सच", "गलती", "जरूरी", "सीखो", "समझो",
}


def _window_score(segments: list[dict], start: float, end: float, instruction_terms: set[str]) -> float:
    overlap_text: list[str] = []
    speech = 0.0
    questions = 0
    emphatic = 0
    hook_hits = 0
    segment_count = 0
    for segment in segments:
        a, b = float(segment["start"]), float(segment["end"])
        overlap = max(0.0, min(end, b) - max(start, a))
        if overlap <= 0:
            continue
        segment_count += 1
        speech += overlap
        text = str(segment.get("text", ""))
        overlap_text.append(text)
        questions += text.count("?")
        emphatic += text.count("!")
        tokens = {token.lower().strip(".,!?;:()[]{}\"'…") for token in re.findall(r"[\w\u0900-\u097f]+", text)}
        hook_hits += len(tokens & _HOOK_WORDS)
        hook_hits += min(4, len(tokens & instruction_terms)) * 1.2
    duration = max(1.0, end - start)
    density = min(1.0, speech / duration)
    all_text = " ".join(overlap_text)
    unique_tokens = len(set(re.findall(r"[\w\u0900-\u097f]+", all_text.lower())))
    # Prefer dense, self-contained speech with a concrete point or hook. This is an
    # offline heuristic, not a claim of human-level semantic understanding.
    return density * 7.0 + min(8, unique_tokens) * 0.18 + min(6, hook_hits) * 0.38 + questions * 0.45 + emphatic * 0.2 + min(5, segment_count) * 0.12


def choose_highlight(segments: list[dict], duration: float, settings: dict, instructions: str = "") -> dict:
    minimum = max(8, int(settings.get("min_duration", 15)))
    maximum = max(minimum, min(180, int(settings.get("max_duration", 60))))
    target = max(minimum, min(maximum, int(settings.get("target_duration", 45))))
    # A Reel is already short: preserve the complete clip whenever practical.
    source_platform = settings.get("source_platform")
    if source_platform == "instagram" and duration <= maximum:
        start, end = 0.0, duration
    elif duration <= maximum:
        start, end = 0.0, duration
    elif not segments:
        start, end = 0.0, min(target, duration)
    else:
        instruction_terms = {x.lower() for x in re.findall(r"[\w\u0900-\u097f]+", instructions) if len(x) > 3}
        step = 5.0
        candidates = []
        last_start = max(0.0, duration - minimum)
        current = 0.0
        while current <= last_start + 0.001:
            # Keep clips inside the duration and prefer target length.
            length = min(target, maximum, duration - current)
            if length < minimum:
                break
            candidates.append((
                _window_score(segments, current, current + length, instruction_terms),
                current, current + length,
            ))
            current += step
        if not candidates:
            start, end = 0.0, min(target, duration)
        else:
            _, start, end = max(candidates, key=lambda item: (item[0], -item[1]))
    chosen_text = " ".join(
        str(segment.get("text", "")).strip()
        for segment in segments
        if float(segment.get("end", 0)) > start and float(segment.get("start", 0)) < end
    ).strip()
    if chosen_text:
        sentence = re.split(r"(?<=[.!?।])\s+", chosen_text)[0].strip()
        title = re.sub(r"\s+", " ", sentence)[:72].strip(" ,.!?।") or "Selected highlight"
        caption = (chosen_text[:380].strip() + "\n\n#reels #shorts")[:700]
    else:
        title, caption = "Selected highlight", ""
    return {"start": round(start, 2), "end": round(end, 2), "title": title, "caption": caption}


def choose_highlights(
    segments: list[dict],
    duration: float,
    settings: dict,
    instructions: str = "",
    requested_count: int = 30,
) -> list[dict]:
    """Select up to N non-overlapping transcript highlights using only local heuristics.

    The selector deliberately avoids cloud/paid AI calls. It scores fixed-length windows,
    greedily chooses the strongest non-overlapping moments, then restores timeline order.
    A short source can yield fewer clips than requested; the function never duplicates a
    moment merely to satisfy the requested number.
    """
    minimum = max(8, int(settings.get("min_duration", 15)))
    maximum = max(minimum, min(180, int(settings.get("max_duration", 60))))
    target = max(minimum, min(maximum, int(settings.get("target_duration", 45))))
    requested_count = max(1, min(30, int(requested_count)))
    duration = float(duration)
    if duration < minimum:
        return []

    # Use the requested target length where possible. For a short source, one shorter
    # clip is allowed only when it still meets the configured minimum duration.
    window = min(target, duration)
    if duration < window:
        return []

    instruction_terms = {
        x.lower() for x in re.findall(r"[\w\u0900-\u097f]+", instructions)
        if len(x) > 3
    }
    last_start = max(0.0, duration - window)
    step = max(2.0, min(5.0, window / 12.0))
    starts: list[float] = []
    current = 0.0
    while current <= last_start + 0.001:
        starts.append(round(current, 3))
        current += step
    if not starts or abs(starts[-1] - last_start) > 0.25:
        starts.append(round(last_start, 3))

    scored: list[tuple[float, float, float]] = []
    if segments:
        for start in starts:
            end = min(duration, start + window)
            score = _window_score(segments, start, end, instruction_terms)
            scored.append((score, start, end))
    else:
        # With no speech transcript, choose evenly distributed, non-overlapping ranges.
        slots = min(requested_count, int(duration // max(minimum, window)))
        if slots <= 0:
            return []
        if slots == 1:
            starts = [max(0.0, (duration - window) / 2.0)]
        else:
            available = max(0.0, duration - window)
            starts = [available * i / (slots - 1) for i in range(slots)]
        scored = [(0.0, round(start, 3), round(min(duration, start + window), 3)) for start in starts]

    # A tolerance smaller than the window prevents duplicate/near-duplicate moments.
    selected: list[tuple[float, float, float]] = []
    for item in sorted(scored, key=lambda x: (-x[0], x[1])):
        _score, start, end = item
        if all(min(end, chosen_end) - max(start, chosen_start) <= 0.5 for _, chosen_start, chosen_end in selected):
            selected.append(item)
            if len(selected) >= requested_count:
                break

    # If scoring candidates cannot fill the request, add remaining non-overlapping
    # fixed-size windows. This still creates different sections, not duplicate clips.
    if len(selected) < requested_count:
        chronological = sorted(selected, key=lambda x: x[1])
        cursor = 0.0
        while cursor + minimum <= duration + 0.001 and len(selected) < requested_count:
            end = min(duration, cursor + window)
            if end - cursor < minimum:
                break
            if all(min(end, chosen_end) - max(cursor, chosen_start) <= 0.5 for _, chosen_start, chosen_end in chronological):
                score = _window_score(segments, cursor, end, instruction_terms) if segments else 0.0
                item = (score, round(cursor, 3), round(end, 3))
                selected.append(item)
                chronological.append(item)
            cursor += window

    result: list[dict] = []
    for _score, start, end in sorted(selected, key=lambda x: x[1])[:requested_count]:
        chosen_text = " ".join(
            str(segment.get("text", "")).strip()
            for segment in segments
            if float(segment.get("end", 0)) > start and float(segment.get("start", 0)) < end
        ).strip()
        if chosen_text:
            sentence = re.split(r"(?<=[.!?।])\s+", chosen_text)[0].strip()
            title = re.sub(r"\s+", " ", sentence)[:72].strip(" ,.!?।") or "Selected highlight"
            caption = (chosen_text[:380].strip() + "\n\n#reels #shorts")[:700]
        else:
            title, caption = "Selected highlight", "#reels #shorts"
        result.append({
            "start": round(start, 2), "end": round(end, 2),
            "title": title, "caption": caption,
        })
    return result
