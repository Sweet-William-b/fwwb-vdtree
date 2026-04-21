from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import Any

from .config import CAMPUS_CLASS_MAP
from .event_builder import Event


RISK_ORDER = {"low": 0, "review": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class CampusMapping:
    campus_label: str
    risk_level: str
    summary: str


def get_mapping(source_class: str) -> CampusMapping:
    info = CAMPUS_CLASS_MAP.get(source_class, CAMPUS_CLASS_MAP["Normal"])
    return CampusMapping(
        campus_label=info["campus_label"],
        risk_level=info["risk_level"],
        summary=info["summary"],
    )


def parse_interval_key(key: str) -> tuple[int, int]:
    left, right = key.split(",")
    return int(float(left.strip())), int(float(right.strip()))


def _overlap_score(start_a: int, end_a: int, start_b: int, end_b: int) -> tuple[int, int]:
    overlap = max(0, min(end_a, end_b) - max(start_a, start_b) + 1)
    center_a = (start_a + end_a) // 2
    center_b = (start_b + end_b) // 2
    distance = abs(center_a - center_b)
    return overlap, -distance


def pick_best_interval(interval_map: dict[str, Any], start: int, end: int) -> tuple[str | None, Any]:
    best_key = None
    best_value = None
    best_score = (-1, -(10**9))
    for key, value in interval_map.items():
        clip_start, clip_end = parse_interval_key(key)
        score = _overlap_score(start, end, clip_start, clip_end)
        if score > best_score:
            best_key = key
            best_value = value
            best_score = score
    return best_key, best_value


def _strip_think_artifacts(text: str) -> str:
    text = text.replace("<think>", " ").replace("</think>", " ")
    text = re.sub(r"\[[0-9.,\s]+\]\s*$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def summarize_text(text: str, max_sentences: int = 2, max_chars: int = 260) -> str:
    clean = _strip_think_artifacts(text)
    if not clean:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", clean)
    summary = " ".join(part for part in parts[:max_sentences] if part).strip()
    if not summary:
        summary = clean
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rstrip() + "..."
    return summary


def _reasoning_score(value: Any) -> float | None:
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, (int, float)):
            return float(first)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def merge_risk_levels(event_level: str, mapped_level: str) -> str:
    return event_level if RISK_ORDER[event_level] >= RISK_ORDER[mapped_level] else mapped_level


def enrich_events(
    events: list[Event],
    source_class: str,
    captions: dict[str, str],
    reasoning: dict[str, Any],
) -> list[dict[str, Any]]:
    mapping = get_mapping(source_class)
    enriched: list[dict[str, Any]] = []

    for event in events:
        caption_key, caption_value = pick_best_interval(captions, event.start_frame, event.end_frame)
        reason_key, reason_value = pick_best_interval(reasoning, event.start_frame, event.end_frame)
        caption_summary = summarize_text(caption_value or "")
        reason_excerpt = ""
        if isinstance(reason_value, list) and len(reason_value) >= 2 and isinstance(reason_value[1], str):
            reason_excerpt = summarize_text(reason_value[1], max_sentences=1, max_chars=180)
        elif isinstance(reason_value, str):
            reason_excerpt = summarize_text(reason_value, max_sentences=1, max_chars=180)

        if caption_summary:
            reason_text = f"{mapping.campus_label}：{caption_summary}"
        elif reason_excerpt:
            reason_text = f"{mapping.campus_label}：{reason_excerpt}"
        else:
            reason_text = mapping.summary

        event_dict = event.to_dict()
        event_dict.update(
            {
                "source_class": source_class,
                "campus_label": mapping.campus_label,
                "behavior_type": mapping.campus_label,
                "risk_level": merge_risk_levels(event.risk_level, mapping.risk_level),
                "mapping_summary": mapping.summary,
                "caption_interval": caption_key,
                "caption_summary": caption_summary,
                "reason_interval": reason_key,
                "reasoning_score": _reasoning_score(reason_value),
                "reason_excerpt": reason_excerpt,
                "reason_text": reason_text,
                "review_status": "pending",
                "note": "",
                "track_ids": [event.event_id],
                "confidence": event.peak_score,
                "clip_href": None,
                "html_reason_text": escape(reason_text),
            }
        )
        enriched.append(event_dict)

    return enriched
