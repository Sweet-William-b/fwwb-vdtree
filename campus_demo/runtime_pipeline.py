from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2

from .config import HISTORY_PATH, OUTPUT_ROOT, UPLOADS_ROOT
from .exporter import (
    build_history_index,
    build_report_bundle,
    build_report_html,
    build_score_svg,
    ensure_output_root,
    export_clips,
    report_directory,
    write_current_analysis,
    write_event_exports,
    write_json,
)
from .history import append_history, read_history
from .result_loader import get_store
from .vadtree_backend import VADTreePipelineResult, VADTreeRuntimeAdapter

SEGMENT_FRAMES = 5
WINDOW_FRAMES = 10
EVENT_THRESHOLD = 0.50
HIGH_EVENT_THRESHOLD = 0.75
RTSP_RECONNECT_ATTEMPTS = 3
RTSP_RECONNECT_DELAY = 2.0
RTSP_CHUNK_SECONDS = 4.0
DEFAULT_FPS = 25.0

ProgressCallback = Callable[[dict[str, Any]], None]


class AnalysisCancelledError(RuntimeError):
    pass


@dataclass
class WindowResult:
    start_frame: int
    end_frame: int
    start_sec: float
    end_sec: float
    confidence: float
    behavior_type: str
    reason_text: str
    score_max: float
    score_mean: float
    caption_interval: str | None
    reason_interval: str | None


@dataclass
class RuntimeAnalysisResult:
    source_type: str
    dataset_name: str
    dataset_display_name: str
    source_label: str
    video_name: str
    video_stem: str
    video_path: Path | None
    source_class: str
    source_fps: float
    processing_fps: float
    total_frames: int
    processed_segments: int
    analyzed_windows: int
    thresholds: dict[str, float]
    frame_scores: list[float]
    window_results: list[dict[str, Any]]
    events: list[dict[str, Any]]
    summary: dict[str, Any]
    analysis: dict[str, Any]


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_float(value: Any, fallback: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(numeric) or numeric <= 0:
        return fallback
    return numeric


def _processing_fps(frame_count: int, started_at: float) -> float:
    elapsed = max(time.perf_counter() - started_at, 1e-6)
    return round(frame_count / elapsed, 2)


def _normalize_stream_source(rtsp_url: str) -> tuple[str | int, bool]:
    candidate = rtsp_url.strip()
    if candidate.startswith("file://"):
        path = Path(candidate[7:])
        if path.exists():
            return str(path), True
    path = Path(candidate)
    if path.exists():
        return str(path), True
    return candidate, False


def _guess_behavior_from_text(text: str) -> str | None:
    lowered = text.lower()
    keyword_map = (
        ("fight", "打架斗殴"),
        ("fighting", "打架斗殴"),
        ("assault", "攻击行为"),
        ("abuse", "攻击行为"),
        ("robbery", "可疑抢夺"),
        ("burglary", "可疑闯入"),
        ("steal", "可疑盗窃"),
        ("shoplift", "可疑盗窃"),
        ("vand", "破坏公共设施"),
        ("fall", "人员跌倒"),
        ("fire", "火情风险"),
        ("smoke", "火情风险"),
        ("explosion", "爆炸风险"),
        ("water", "涉水风险"),
        ("traffic", "交通风险"),
        ("crash", "交通风险"),
    )
    for keyword, label in keyword_map:
        if keyword in lowered:
            return label
    return None


def _risk_level_from_confidence(confidence: float) -> str:
    return "high" if confidence >= HIGH_EVENT_THRESHOLD else "medium"


def _parse_interval_key(key: Any) -> tuple[int, int] | None:
    matches = re.findall(r"-?\d+(?:\.\d+)?", str(key))
    if len(matches) < 2:
        return None
    start = int(float(matches[0]))
    end = int(float(matches[1]))
    if end < start:
        start, end = end, start
    return start, end


def _normalize_reason_value(value: Any) -> str:
    if isinstance(value, list):
        for item in reversed(value):
            if isinstance(item, str) and item.strip():
                return item.strip()
        return " ".join(str(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        if "reason_text" in value:
            return str(value["reason_text"]).strip()
        return " ".join(str(item).strip() for item in value.values() if str(item).strip())
    return str(value or "").strip()


def _select_interval_entry(
    interval_map: dict[str, Any],
    start_frame: int,
    end_frame: int,
) -> tuple[str | None, str]:
    best_key: str | None = None
    best_value = ""
    best_overlap = -1
    best_span = math.inf
    for key, value in interval_map.items():
        parsed = _parse_interval_key(key)
        if parsed is None:
            continue
        interval_start, interval_end = parsed
        overlap = min(end_frame, interval_end) - max(start_frame, interval_start) + 1
        if overlap <= 0:
            continue
        span = interval_end - interval_start
        if overlap > best_overlap or (overlap == best_overlap and span < best_span):
            best_key = str(key)
            best_value = _normalize_reason_value(value)
            best_overlap = overlap
            best_span = span
    return best_key, best_value


def _build_reason_text(
    *,
    start_frame: int,
    end_frame: int,
    score_max: float,
    score_mean: float,
    captions: dict[str, Any],
    reasoning: dict[str, Any],
) -> tuple[str, str | None, str | None]:
    caption_key, caption_text = _select_interval_entry(captions, start_frame, end_frame)
    reason_key, reason_text = _select_interval_entry(reasoning, start_frame, end_frame)
    parts = []
    if reason_text:
        parts.append(reason_text)
    if caption_text:
        parts.append(f"Summary: {caption_text}")
    parts.append(f"window max={score_max:.3f}, mean={score_mean:.3f}")
    return " | ".join(parts), caption_key, reason_key


def _behavior_from_window(
    *,
    confidence: float,
    preferred_behavior: str | None,
    reason_text: str,
) -> str:
    if confidence < EVENT_THRESHOLD:
        return "正常行为"
    inferred = _guess_behavior_from_text(reason_text)
    return inferred or preferred_behavior or "异常行为"


class EventAggregator:
    def __init__(self, fps: float):
        self.fps = max(fps, 1.0)
        self._merge_gap_frames = SEGMENT_FRAMES
        self._events: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._next_event_index = 1

    def ingest(self, result: WindowResult) -> None:
        if result.confidence < EVENT_THRESHOLD:
            return

        event = self._window_to_event(result)
        if (
            self._current
            and self._current["behavior_type"] == event["behavior_type"]
            and event["start_frame"] <= self._current["end_frame"] + self._merge_gap_frames
        ):
            self._current["end_frame"] = event["end_frame"]
            self._current["end_sec"] = event["end_sec"]
            self._current["_sum_confidence"] += event["confidence"]
            self._current["_window_count"] += 1
            self._current["mean_score"] = round(
                self._current["_sum_confidence"] / max(self._current["_window_count"], 1),
                4,
            )
            if event["confidence"] >= self._current["confidence"]:
                self._current["confidence"] = event["confidence"]
                self._current["peak_score"] = event["peak_score"]
                self._current["reason_text"] = event["reason_text"]
            self._current["risk_level"] = _risk_level_from_confidence(self._current["confidence"])
            self._current["duration_sec"] = round(
                (self._current["end_frame"] - self._current["start_frame"] + 1) / self.fps,
                2,
            )
            return

        if self._current is not None:
            self._events.append(self._finalize_event(self._current))
        self._current = event

    def snapshot(self) -> list[dict[str, Any]]:
        events = [dict(item) for item in self._events]
        if self._current is not None:
            events.append(dict(self._finalize_event(self._current, in_place=False)))
        return events

    def finalize(self) -> list[dict[str, Any]]:
        if self._current is not None:
            self._events.append(self._finalize_event(self._current))
            self._current = None
        return [dict(item) for item in self._events]

    def _window_to_event(self, result: WindowResult) -> dict[str, Any]:
        event_id = f"evt_{self._next_event_index:04d}"
        self._next_event_index += 1
        duration_sec = round((result.end_frame - result.start_frame + 1) / self.fps, 2)
        confidence = round(result.confidence, 4)
        return {
            "event_id": event_id,
            "behavior_type": result.behavior_type,
            "campus_label": result.behavior_type,
            "risk_level": _risk_level_from_confidence(confidence),
            "track_ids": [event_id],
            "start_sec": result.start_sec,
            "end_sec": result.end_sec,
            "confidence": confidence,
            "review_status": "pending",
            "note": "",
            "clip_href": None,
            "reason_text": result.reason_text,
            "source_class": result.behavior_type,
            "peak_score": round(result.score_max, 4),
            "mean_score": round(result.score_mean, 4),
            "duration_sec": duration_sec,
            "start_frame": result.start_frame,
            "end_frame": result.end_frame,
            "_sum_confidence": confidence,
            "_window_count": 1,
        }

    def _finalize_event(self, event: dict[str, Any], in_place: bool = True) -> dict[str, Any]:
        target = event if in_place else dict(event)
        target["track_ids"] = [target["event_id"]]
        target["duration_sec"] = round((target["end_frame"] - target["start_frame"] + 1) / self.fps, 2)
        target.pop("_sum_confidence", None)
        target.pop("_window_count", None)
        return target


def _window_starts(total_frames: int) -> list[int]:
    if total_frames <= 0:
        return []
    if total_frames <= WINDOW_FRAMES:
        return [0]
    starts = list(range(0, total_frames - WINDOW_FRAMES + 1, SEGMENT_FRAMES))
    last_start = total_frames - WINDOW_FRAMES
    if not starts or starts[-1] != last_start:
        starts.append(last_start)
    return starts


def _analyze_frame_scores(
    frame_scores: list[float],
    *,
    fps: float,
    preferred_behavior: str | None,
    captions: dict[str, Any],
    reasoning: dict[str, Any],
) -> tuple[list[WindowResult], list[dict[str, Any]]]:
    aggregator = EventAggregator(fps)
    windows: list[WindowResult] = []
    total_frames = len(frame_scores)

    for start_frame in _window_starts(total_frames):
        end_frame = min(total_frames - 1, start_frame + WINDOW_FRAMES - 1)
        window_scores = [float(item) for item in frame_scores[start_frame : end_frame + 1]]
        if not window_scores:
            continue
        score_max = max(window_scores)
        score_mean = sum(window_scores) / len(window_scores)
        reason_text, caption_key, reason_key = _build_reason_text(
            start_frame=start_frame,
            end_frame=end_frame,
            score_max=score_max,
            score_mean=score_mean,
            captions=captions,
            reasoning=reasoning,
        )
        confidence = round(score_max, 4)
        behavior_type = _behavior_from_window(
            confidence=confidence,
            preferred_behavior=preferred_behavior,
            reason_text=reason_text,
        )
        result = WindowResult(
            start_frame=start_frame,
            end_frame=end_frame,
            start_sec=round(start_frame / max(fps, 1.0), 2),
            end_sec=round((end_frame + 1) / max(fps, 1.0), 2),
            confidence=confidence,
            behavior_type=behavior_type,
            reason_text=reason_text,
            score_max=round(score_max, 4),
            score_mean=round(score_mean, 4),
            caption_interval=caption_key,
            reason_interval=reason_key,
        )
        windows.append(result)
        aggregator.ingest(result)

    return windows, aggregator.finalize()


def _build_runtime_payload(
    *,
    backend_result: VADTreePipelineResult,
    source_type: str,
    source_label: str,
    video_name: str,
    processing_fps: float,
    extra_summary: dict[str, Any] | None = None,
) -> RuntimeAnalysisResult:
    preferred_behavior = _guess_behavior_from_text(f"{source_label} {video_name}")
    window_results, events = _analyze_frame_scores(
        backend_result.frame_scores,
        fps=backend_result.source_fps,
        preferred_behavior=preferred_behavior,
        captions=backend_result.captions,
        reasoning=backend_result.reasoning,
    )
    processed_segments = math.ceil(max(backend_result.total_frames, 0) / SEGMENT_FRAMES)
    primary_behavior = preferred_behavior or "正常行为"
    if events:
        primary_behavior = Counter(event["behavior_type"] for event in events).most_common(1)[0][0]
        headline = (
            f"系统已基于真实 VADTree 帧级分数完成 5 帧分段、10 帧窗口聚合，共识别到 "
            f"{len(events)} 段 {primary_behavior} 相关异常。"
        )
    else:
        headline = "系统已基于真实 VADTree 帧级分数完成 5 帧分段、10 帧窗口聚合，未发现达到告警阈值的异常时段。"

    thresholds = {"threshold": EVENT_THRESHOLD, "high_threshold": HIGH_EVENT_THRESHOLD}
    summary = {
        "dataset_name": backend_result.dataset_name,
        "dataset_display_name": backend_result.dataset_display_name,
        "video_name": video_name,
        "video_stem": Path(video_name).stem,
        "video_path": str(backend_result.video_path),
        "source_class": backend_result.source_class,
        "campus_label": primary_behavior,
        "headline": headline,
        "event_count": len(events),
        "peak_score": round(max(backend_result.frame_scores), 4) if backend_result.frame_scores else 0.0,
        "mean_score": round(sum(backend_result.frame_scores) / len(backend_result.frame_scores), 4)
        if backend_result.frame_scores
        else 0.0,
        "threshold": EVENT_THRESHOLD,
        "high_threshold": HIGH_EVENT_THRESHOLD,
        "fps": round(backend_result.source_fps, 2),
        "segment_frames": SEGMENT_FRAMES,
        "window_frames": WINDOW_FRAMES,
        "processed_segments": processed_segments,
        "analyzed_windows": len(window_results),
        "processing_fps": processing_fps,
        "source_type": source_type,
        "source_label": source_label,
        "pipeline_backend": "vadtree",
        "generated_at": _iso_now(),
    }
    if extra_summary:
        summary.update(extra_summary)

    analysis = {
        "summary": summary,
        "events": events,
        "metrics": backend_result.metrics,
        "thresholds": thresholds,
        "frame_scores": [round(float(item), 4) for item in backend_result.frame_scores],
        "window_results": [asdict(item) for item in window_results],
        "runtime": {
            "segment_frames": SEGMENT_FRAMES,
            "window_frames": WINDOW_FRAMES,
            "processed_segments": processed_segments,
            "analyzed_windows": len(window_results),
            "source_fps": round(backend_result.source_fps, 2),
            "processing_fps": processing_fps,
            "total_frames": backend_result.total_frames,
        },
        "backend": {
            "work_dir": str(backend_result.work_dir),
            **backend_result.artifacts,
        },
    }
    return RuntimeAnalysisResult(
        source_type=source_type,
        dataset_name=backend_result.dataset_name,
        dataset_display_name=backend_result.dataset_display_name,
        source_label=source_label,
        video_name=video_name,
        video_stem=Path(video_name).stem,
        video_path=backend_result.video_path,
        source_class=backend_result.source_class,
        source_fps=round(backend_result.source_fps, 2),
        processing_fps=processing_fps,
        total_frames=backend_result.total_frames,
        processed_segments=processed_segments,
        analyzed_windows=len(window_results),
        thresholds=thresholds,
        frame_scores=[round(float(item), 4) for item in backend_result.frame_scores],
        window_results=[asdict(item) for item in window_results],
        events=events,
        summary=summary,
        analysis=analysis,
    )


def _emit_progress(
    callback: ProgressCallback | None,
    *,
    status: str,
    progress: float,
    progress_mode: str,
    stream_state: str | None,
    total_frames: int,
    current_frame: int,
    source_fps: float,
    started_at: float,
    processed_segments: int,
    analyzed_windows: int,
    buffered_segments: int,
    events: list[dict[str, Any]],
    preview_path: Path | None,
    latency_ms: float,
) -> None:
    if callback is None:
        return
    callback(
        {
            "status": status,
            "progress": round(progress, 4),
            "progress_mode": progress_mode,
            "stream_state": stream_state,
            "total_frames": total_frames,
            "current_sec": round(current_frame / max(source_fps, 1.0), 2),
            "source_fps": round(source_fps, 2),
            "processing_fps": _processing_fps(max(current_frame, analyzed_windows), started_at),
            "current_fps": _processing_fps(max(current_frame, analyzed_windows), started_at),
            "latency_ms": round(latency_ms, 2),
            "processed_segments": processed_segments,
            "analyzed_windows": analyzed_windows,
            "buffered_segments": buffered_segments,
            "event_count": len(events),
            "events": events,
            "latest_alerts": events[-5:],
            "preview_path": preview_path,
        }
    )


def _shift_interval_map(interval_map: dict[str, Any], frame_offset: int) -> dict[str, Any]:
    shifted: dict[str, Any] = {}
    for key, value in interval_map.items():
        parsed = _parse_interval_key(key)
        if parsed is None:
            shifted[str(key)] = value
            continue
        start_frame, end_frame = parsed
        shifted[f"{start_frame + frame_offset}, {end_frame + frame_offset}"] = value
    return shifted


def _merge_chunk_scores(target: list[float], source: list[float], frame_offset: int) -> None:
    if len(target) < frame_offset:
        target.extend([0.0] * (frame_offset - len(target)))
    if len(target) < frame_offset + len(source):
        target.extend([0.0] * (frame_offset + len(source) - len(target)))
    for index, score in enumerate(source):
        target[frame_offset + index] = max(target[frame_offset + index], float(score))


def _open_writer(path: Path, fps: float, frame: Any) -> cv2.VideoWriter:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = frame.shape[:2]
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        max(fps, 1.0),
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Failed to create local recording file: {path}")
    return writer


def run_upload_analysis(
    *,
    source_path: Path,
    dataset_name: str,
    source_label: str,
    video_name: str,
    cancel_flag: Any | None = None,
    progress_callback: ProgressCallback | None = None,
    job_id: str | None = None,
) -> RuntimeAnalysisResult:
    if cancel_flag is not None and cancel_flag.is_set():
        raise AnalysisCancelledError("任务已取消。")

    capture = cv2.VideoCapture(str(source_path))
    if not capture.isOpened():
        raise RuntimeError(f"无法打开上传视频: {source_path}")
    try:
        total_frames_hint = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        source_fps = _safe_float(capture.get(cv2.CAP_PROP_FPS), DEFAULT_FPS)
    finally:
        capture.release()

    adapter = VADTreeRuntimeAdapter(dataset_name, job_id or f"upload-{Path(video_name).stem}")
    started_at = time.perf_counter()

    def stage_callback(stage: str, progress: float) -> None:
        if cancel_flag is not None and cancel_flag.is_set():
            raise AnalysisCancelledError("任务已取消。")
        current_frame = int((total_frames_hint or 0) * progress)
        processed_segments = math.ceil(max(current_frame, 0) / SEGMENT_FRAMES)
        analyzed_windows = max(0, math.ceil(max(current_frame - WINDOW_FRAMES + 1, 0) / SEGMENT_FRAMES))
        _emit_progress(
            progress_callback,
            status="running",
            progress=progress,
            progress_mode="determinate",
            stream_state=None,
            total_frames=total_frames_hint,
            current_frame=current_frame,
            source_fps=source_fps,
            started_at=started_at,
            processed_segments=processed_segments,
            analyzed_windows=analyzed_windows,
            buffered_segments=0,
            events=[],
            preview_path=source_path,
            latency_ms=0.0,
        )

    backend_result = adapter.analyze_video(
        source_path=source_path,
        source_label=source_label,
        video_name=video_name,
        source_type="upload",
        work_suffix="full",
        stage_callback=stage_callback,
    )
    processing_fps = _processing_fps(max(backend_result.total_frames, 1), started_at)
    return _build_runtime_payload(
        backend_result=backend_result,
        source_type="upload",
        source_label=source_label,
        video_name=str(video_name or source_path.name),
        processing_fps=processing_fps,
    )


def run_rtsp_analysis(
    *,
    rtsp_url: str,
    dataset_name: str,
    recording_name: str,
    cancel_flag: Any | None = None,
    progress_callback: ProgressCallback | None = None,
    job_id: str | None = None,
) -> RuntimeAnalysisResult:
    ensure_output_root(OUTPUT_ROOT)
    UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)

    normalized_source, is_file_source = _normalize_stream_source(rtsp_url)
    recording_path = UPLOADS_ROOT / recording_name
    adapter = VADTreeRuntimeAdapter(dataset_name, job_id or f"rtsp-{Path(recording_name).stem}")
    started_at = time.perf_counter()

    capture = cv2.VideoCapture(normalized_source)
    if not capture.isOpened():
        raise RuntimeError(f"无法连接 RTSP 源: {rtsp_url}")

    source_fps = _safe_float(capture.get(cv2.CAP_PROP_FPS), DEFAULT_FPS)
    adapter.validate()

    recording_writer: cv2.VideoWriter | None = None
    chunk_writer: cv2.VideoWriter | None = None
    chunk_path: Path | None = None
    chunk_index = 0
    chunk_start_frame = 0
    chunk_frame_count = 0
    chunk_frame_target = max(int(source_fps * RTSP_CHUNK_SECONDS), WINDOW_FRAMES)
    stream_state = "connecting"
    reconnect_attempts = 0
    total_frames = 0
    stopped_by_user = False
    latest_latency_ms = 0.0
    global_scores: list[float] = []
    global_captions: dict[str, Any] = {}
    global_reasoning: dict[str, Any] = {}
    current_windows: list[WindowResult] = []
    current_events: list[dict[str, Any]] = []
    preferred_behavior = _guess_behavior_from_text(rtsp_url)

    def emit_snapshot() -> None:
        _emit_progress(
            progress_callback,
            status="running",
            progress=0.0,
            progress_mode="indeterminate",
            stream_state=stream_state,
            total_frames=0,
            current_frame=total_frames,
            source_fps=source_fps,
            started_at=started_at,
            processed_segments=math.ceil(max(total_frames, 0) / SEGMENT_FRAMES),
            analyzed_windows=len(current_windows),
            buffered_segments=math.ceil(max(chunk_frame_count, 0) / SEGMENT_FRAMES),
            events=current_events,
            preview_path=recording_path if recording_path.exists() else None,
            latency_ms=latest_latency_ms,
        )

    def analyze_chunk() -> None:
        nonlocal chunk_index, chunk_start_frame, chunk_frame_count, chunk_writer, chunk_path
        nonlocal current_windows, current_events, latest_latency_ms
        if chunk_path is None or chunk_frame_count <= 0:
            return
        if chunk_writer is not None:
            chunk_writer.release()
            chunk_writer = None
        window_started = time.perf_counter()
        chunk_result = adapter.analyze_video(
            source_path=chunk_path,
            source_label=rtsp_url,
            video_name=chunk_path.name,
            source_type="rtsp",
            work_suffix=f"chunk-{chunk_index:03d}",
            stage_callback=None,
        )
        _merge_chunk_scores(global_scores, chunk_result.frame_scores, chunk_start_frame)
        global_captions.update(_shift_interval_map(chunk_result.captions, chunk_start_frame))
        global_reasoning.update(_shift_interval_map(chunk_result.reasoning, chunk_start_frame))
        current_windows, current_events = _analyze_frame_scores(
            global_scores,
            fps=source_fps,
            preferred_behavior=preferred_behavior,
            captions=global_captions,
            reasoning=global_reasoning,
        )
        latest_latency_ms = (time.perf_counter() - window_started) * 1000.0
        chunk_index += 1
        chunk_start_frame = total_frames
        chunk_frame_count = 0
        chunk_path = None

    _emit_progress(
        progress_callback,
        status="queued",
        progress=0.0,
        progress_mode="indeterminate",
        stream_state=stream_state,
        total_frames=0,
        current_frame=0,
        source_fps=source_fps,
        started_at=started_at,
        processed_segments=0,
        analyzed_windows=0,
        buffered_segments=0,
        events=[],
        preview_path=None,
        latency_ms=0.0,
    )

    try:
        while True:
            if cancel_flag is not None and cancel_flag.is_set():
                stopped_by_user = True
                break

            ok, frame = capture.read()
            if not ok:
                if is_file_source:
                    break
                reconnect_attempts += 1
                if reconnect_attempts > RTSP_RECONNECT_ATTEMPTS:
                    raise RuntimeError("RTSP 断流且重连失败。")
                stream_state = "reconnecting"
                emit_snapshot()
                capture.release()
                time.sleep(RTSP_RECONNECT_DELAY)
                capture = cv2.VideoCapture(normalized_source)
                if capture.isOpened():
                    source_fps = _safe_float(capture.get(cv2.CAP_PROP_FPS), source_fps)
                    stream_state = "buffering" if total_frames < WINDOW_FRAMES else "running"
                    continue
                continue

            reconnect_attempts = 0
            if recording_writer is None:
                recording_writer = _open_writer(recording_path, source_fps, frame)
            if chunk_writer is None:
                chunk_path = UPLOADS_ROOT / f"{Path(recording_name).stem}_chunk_{chunk_index:03d}.mp4"
                chunk_writer = _open_writer(chunk_path, source_fps, frame)

            recording_writer.write(frame)
            chunk_writer.write(frame)
            total_frames += 1
            chunk_frame_count += 1
            stream_state = "buffering" if total_frames < WINDOW_FRAMES else "running"

            if total_frames % SEGMENT_FRAMES == 0 or total_frames <= WINDOW_FRAMES:
                emit_snapshot()

            if chunk_frame_count >= chunk_frame_target:
                analyze_chunk()
                emit_snapshot()
    finally:
        capture.release()
        if recording_writer is not None:
            recording_writer.release()
        if chunk_writer is not None:
            chunk_writer.release()

    if total_frames <= 0:
        if stopped_by_user:
            raise AnalysisCancelledError("RTSP 已停止，但未采集到有效视频帧。")
        raise RuntimeError("未从 RTSP 源读取到有效视频帧。")

    try:
        final_backend = adapter.analyze_video(
            source_path=recording_path if recording_path.exists() else Path(str(normalized_source)),
            source_label=rtsp_url,
            video_name=recording_path.name,
            source_type="rtsp",
            work_suffix="final",
            stage_callback=None,
        )
    except Exception as exc:
        if stopped_by_user:
            raise AnalysisCancelledError(f"RTSP 已停止，但未能生成最终报告: {exc}") from exc
        raise

    processing_fps = _processing_fps(max(total_frames, 1), started_at)
    return _build_runtime_payload(
        backend_result=final_backend,
        source_type="rtsp",
        source_label=rtsp_url,
        video_name=recording_path.name,
        processing_fps=processing_fps,
        extra_summary={"stream_state": "stopped" if stopped_by_user else "completed"},
    )


def build_runtime_report(
    result: RuntimeAnalysisResult,
    *,
    report_stem: str,
    history_record_extra: dict[str, Any] | None = None,
) -> Path:
    ensure_output_root(OUTPUT_ROOT)
    report_dir = report_directory(result.dataset_name, report_stem, OUTPUT_ROOT)
    report_dir.mkdir(parents=True, exist_ok=True)

    clip_manifest, clip_mode = export_clips(report_dir, result.video_path, result.events)
    analysis = dict(result.analysis)
    analysis["clip_manifest"] = clip_manifest
    analysis["summary"] = dict(result.summary)
    analysis["summary"]["clip_mode"] = clip_mode

    score_source = result.frame_scores or [0.0]
    score_svg = build_score_svg(
        score_source,
        result.events,
        result.thresholds["threshold"],
        result.thresholds["high_threshold"],
    )
    (report_dir / "score.svg").write_text(score_svg, encoding="utf-8")
    write_json(report_dir / "analysis.json", analysis)
    write_event_exports(report_dir, result.events, current=False)
    write_current_analysis(report_dir, analysis, result.events)
    write_event_exports(report_dir, result.events, current=True)
    write_json(
        report_dir / "report_meta.json",
        {
            "report_id": report_dir.name,
            "dataset_name": result.dataset_name,
            "video_name": result.video_name,
            "generated_at": _iso_now(),
            **(history_record_extra or {}),
        },
    )
    (report_dir / "index.html").write_text(
        build_report_html(report_dir, analysis["summary"], result.events, clip_manifest, clip_mode),
        encoding="utf-8",
    )
    build_report_bundle(report_dir)

    history_record = {
        "dataset_name": result.dataset_name,
        "dataset_display_name": result.dataset_display_name,
        "video_name": result.video_name,
        "source_class": result.source_class,
        "campus_label": analysis["summary"]["campus_label"],
        "event_count": len(result.events),
        "peak_score": analysis["summary"]["peak_score"],
        "generated_at": _iso_now(),
        "report_href": f"reports/{report_dir.name}/index.html",
    }
    history_record.update(history_record_extra or {})
    append_history(HISTORY_PATH, history_record)
    build_history_index(OUTPUT_ROOT, read_history(HISTORY_PATH))
    return report_dir
