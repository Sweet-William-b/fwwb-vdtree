from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil, floor
from typing import Any

from .config import (
    BASE_ALERT_THRESHOLD,
    BASE_HIGH_ALERT_THRESHOLD,
    MERGE_GAP_SECONDS,
    MIN_EVENT_SECONDS,
    SMOOTH_WINDOW_FRAMES,
)


@dataclass
class Event:
    event_id: str
    start_frame: int
    end_frame: int
    start_sec: float
    end_sec: float
    duration_sec: float
    peak_frame: int
    peak_score: float
    mean_score: float
    risk_level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def quantile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = floor(position)
    upper = ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def moving_average(values: list[float], window_size: int) -> list[float]:
    if not values:
        return []
    if window_size <= 1:
        return values[:]
    radius = window_size // 2
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    smoothed: list[float] = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        smoothed.append((prefix[end] - prefix[start]) / (end - start))
    return smoothed


def _pick_thresholds(scores: list[float]) -> tuple[float, float]:
    peak = max(scores, default=0.0)
    if peak < 0.05:
        return 1.0, 1.0
    mean_score = sum(scores) / len(scores)
    q85 = quantile(scores, 0.85)
    q95 = quantile(scores, 0.95)
    threshold = max(BASE_ALERT_THRESHOLD, q85, mean_score + 0.05)
    threshold = min(threshold, max(BASE_ALERT_THRESHOLD, peak * 0.92))
    high_threshold = max(BASE_HIGH_ALERT_THRESHOLD, q95, threshold + 0.08)
    high_threshold = min(high_threshold, peak)
    if high_threshold < threshold:
        high_threshold = threshold
    return threshold, high_threshold


def _merge_segments(segments: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    if not segments:
        return []
    merged = [segments[0]]
    for start, end in segments[1:]:
        prev_start, prev_end = merged[-1]
        if start - prev_end <= max_gap:
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged


def _fallback_segment(scores: list[float], fps: float, threshold: float) -> list[tuple[int, int]]:
    if not scores:
        return []
    peak_score = max(scores)
    if peak_score < max(0.12, threshold * 0.8):
        return []
    peak_frame = max(range(len(scores)), key=scores.__getitem__)
    floor_score = peak_score * 0.75
    start = peak_frame
    end = peak_frame
    while start > 0 and scores[start - 1] >= floor_score:
        start -= 1
    while end + 1 < len(scores) and scores[end + 1] >= floor_score:
        end += 1
    if start == end:
        spread = max(1, int(fps))
        start = max(0, peak_frame - spread)
        end = min(len(scores) - 1, peak_frame + spread)
    return [(start, end)]


def _risk_level(peak_score: float, high_threshold: float, threshold: float) -> str:
    if peak_score >= high_threshold:
        return "high"
    if peak_score >= threshold + max(0.04, (high_threshold - threshold) / 2):
        return "medium"
    return "review"


def build_events(
    scores: list[float],
    fps: float,
    smooth_window: int = SMOOTH_WINDOW_FRAMES,
    min_event_seconds: float = MIN_EVENT_SECONDS,
    merge_gap_seconds: float = MERGE_GAP_SECONDS,
) -> tuple[list[Event], dict[str, float]]:
    if not scores:
        return [], {"threshold": 1.0, "high_threshold": 1.0, "smoothed_peak": 0.0}
    smoothed = moving_average(scores, smooth_window)
    threshold, high_threshold = _pick_thresholds(smoothed)
    min_event_frames = max(1, int(round(fps * min_event_seconds)))
    merge_gap_frames = max(1, int(round(fps * merge_gap_seconds)))

    raw_segments: list[tuple[int, int]] = []
    start_frame: int | None = None
    for frame_index, score in enumerate(smoothed):
        if score >= threshold and start_frame is None:
            start_frame = frame_index
        elif score < threshold and start_frame is not None:
            raw_segments.append((start_frame, frame_index - 1))
            start_frame = None
    if start_frame is not None:
        raw_segments.append((start_frame, len(smoothed) - 1))

    segments = _merge_segments(raw_segments, merge_gap_frames)
    segments = [segment for segment in segments if segment[1] - segment[0] + 1 >= min_event_frames]

    if not segments:
        segments = _fallback_segment(smoothed, fps, threshold)

    events: list[Event] = []
    for index, (start, end) in enumerate(segments, start=1):
        window = smoothed[start : end + 1]
        if not window:
            continue
        peak_frame = start + max(range(len(window)), key=window.__getitem__)
        peak_score = max(window)
        mean_score = sum(window) / len(window)
        events.append(
            Event(
                event_id=f"evt_{index:04d}",
                start_frame=start,
                end_frame=end,
                start_sec=round(start / fps, 2),
                end_sec=round(end / fps, 2),
                duration_sec=round((end - start + 1) / fps, 2),
                peak_frame=peak_frame,
                peak_score=round(peak_score, 4),
                mean_score=round(mean_score, 4),
                risk_level=_risk_level(peak_score, high_threshold, threshold),
            )
        )

    return events, {
        "threshold": round(threshold, 4),
        "high_threshold": round(high_threshold, 4),
        "smoothed_peak": round(max(smoothed), 4),
    }

