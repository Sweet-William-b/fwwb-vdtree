from __future__ import annotations

import argparse
import json
import mimetypes
import re
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

if __package__ in {None, ""}:
    import sys

    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from campus_demo.config import HISTORY_PATH, JOB_HISTORY_PATH, OUTPUT_ROOT, REPO_ROOT, UPLOADS_ROOT
    from campus_demo.event_builder import build_events
    from campus_demo.exporter import (
        build_history_index,
        build_report_bundle,
        build_report_html,
        build_score_svg,
        ensure_output_root,
        export_clips,
        load_report_events,
        read_json,
        report_directory,
        sanitize_for_json,
        write_current_analysis,
        write_event_exports,
        write_json,
    )
    from campus_demo.history import append_history, read_history
    from campus_demo.mapper import enrich_events, get_mapping
    from campus_demo.result_loader import available_datasets, get_store
    from campus_demo.runtime_pipeline import (
        AnalysisCancelledError,
        SEGMENT_FRAMES,
        WINDOW_FRAMES,
        build_runtime_report,
        run_rtsp_analysis,
        run_upload_analysis,
    )
else:
    from .config import HISTORY_PATH, JOB_HISTORY_PATH, OUTPUT_ROOT, REPO_ROOT, UPLOADS_ROOT
    from .event_builder import build_events
    from .exporter import (
        build_history_index,
        build_report_bundle,
        build_report_html,
        build_score_svg,
        ensure_output_root,
        export_clips,
        load_report_events,
        read_json,
        report_directory,
        sanitize_for_json,
        write_current_analysis,
        write_event_exports,
        write_json,
    )
    from .history import append_history, read_history
    from .mapper import enrich_events, get_mapping
    from .result_loader import available_datasets, get_store
    from .runtime_pipeline import (
        AnalysisCancelledError,
        SEGMENT_FRAMES,
        WINDOW_FRAMES,
        build_runtime_report,
        run_rtsp_analysis,
        run_upload_analysis,
    )


CONSOLE_HTML_PATH = Path(__file__).with_name("console.html")
RISK_LEVELS = {"low", "review", "medium", "high"}
REVIEW_STATUSES = {"pending", "confirmed", "false_positive"}
TERMINAL_JOB_STATUSES = {"failed", "completed", "cancelled"}
REVIEWABLE_JOB_STATUSES = {"reviewable", "completed"}

RUNTIME_LOCK = threading.Lock()
JOB_STORE: dict[str, dict[str, Any]] = {}
JOB_CANCEL_FLAGS: dict[str, threading.Event] = {}
SOURCE_STORE: dict[str, dict[str, Any]] = {}
BOOTSTRAPPED_HISTORY = False


class JobCancelled(RuntimeError):
    pass


class ApiError(RuntimeError):
    status_code = 500

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class ApiBadRequest(ApiError):
    status_code = 400


class ApiNotFound(ApiError):
    status_code = 404


class ApiInternalError(ApiError):
    status_code = 500


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sanitize_filename(filename: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", Path(filename).name).strip("._")
    return cleaned or "upload.mp4"


def _served_href_for_path(path: Path | str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path).resolve()
    try:
        relative = candidate.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return None
    return "/" + quote(relative.as_posix(), safe="/:#?=&")


def _report_href_from_dir(report_dir: Path) -> str:
    return f"reports/{report_dir.name}/index.html"


def _report_id_from_href(report_href: str) -> str:
    parts = Path(report_href).parts
    if len(parts) < 2:
        raise ValueError(f"Invalid report href: {report_href}")
    return parts[-2]


def _report_dir_from_id(report_id: str) -> Path:
    if "/" in report_id or "\\" in report_id or ".." in report_id:
        raise ValueError("Invalid report id.")
    report_dir = OUTPUT_ROOT / "reports" / report_id
    if not report_dir.exists():
        raise FileNotFoundError(f"Report '{report_id}' does not exist.")
    return report_dir


def _normalize_risk_level(value: Any) -> str:
    normalized = str(value or "review").strip().lower()
    return normalized if normalized in RISK_LEVELS else "review"


def _normalize_review_status(value: Any) -> str:
    normalized = str(value or "pending").strip().lower()
    return normalized if normalized in REVIEW_STATUSES else "pending"


def _normalize_track_ids(value: Any, event_id: str) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    else:
        items = []
    return items or [event_id]


def _load_clip_lookup(report_dir: Path) -> dict[str, str | None]:
    manifest_path = report_dir / "clips_manifest.json"
    if not manifest_path.exists():
        return {}
    lookup: dict[str, str | None] = {}
    for item in read_json(manifest_path):
        event_id = item.get("event_id")
        if not event_id:
            continue
        href = None
        clip_path = item.get("clip_path")
        if clip_path:
            href = _served_href_for_path(clip_path)
        if not href and item.get("source_video"):
            source_href = _served_href_for_path(item["source_video"])
            if source_href:
                href = f"{source_href}#t={item.get('start_sec', 0)},{item.get('end_sec', 0)}"
        lookup[str(event_id)] = href
    return lookup


def _alert_event_from_raw(event: dict[str, Any], clip_lookup: dict[str, str | None] | None = None) -> dict[str, Any]:
    event_id = str(event.get("event_id"))
    behavior_type = str(event.get("behavior_type") or event.get("campus_label") or event.get("source_class") or "待确认")
    note = str(event.get("note") or "")
    clip_href = event.get("clip_href")
    if not clip_href and clip_lookup:
        clip_href = clip_lookup.get(event_id)
    return {
        "event_id": event_id,
        "behavior_type": behavior_type,
        "risk_level": _normalize_risk_level(event.get("risk_level")),
        "track_ids": _normalize_track_ids(event.get("track_ids"), event_id),
        "start_sec": float(event.get("start_sec", 0.0) or 0.0),
        "end_sec": float(event.get("end_sec", 0.0) or 0.0),
        "confidence": float(event.get("confidence", event.get("peak_score", 0.0)) or 0.0),
        "review_status": _normalize_review_status(event.get("review_status")),
        "note": note,
        "clip_href": clip_href,
        "reason_text": str(event.get("reason_text") or ""),
        "source_class": event.get("source_class"),
        "campus_label": str(event.get("campus_label") or behavior_type),
        "peak_score": event.get("peak_score"),
        "mean_score": event.get("mean_score"),
        "duration_sec": event.get("duration_sec"),
        "start_frame": event.get("start_frame"),
        "end_frame": event.get("end_frame"),
        "last_edited_at": event.get("last_edited_at"),
        "is_edited": bool(event.get("last_edited_at")),
    }


def _job_public_view(job: dict[str, Any]) -> dict[str, Any]:
    processing_fps = job.get("processing_fps", job.get("current_fps", 0.0))
    payload = {
        "job_id": job["job_id"],
        "source_type": job.get("source_type"),
        "source_label": job.get("source_label"),
        "source_id": job.get("source_id"),
        "dataset_name": job.get("dataset_name"),
        "dataset_display_name": job.get("dataset_display_name"),
        "video_name": job.get("video_name"),
        "status": job.get("status"),
        "progress_mode": job.get("progress_mode", "determinate"),
        "progress": job.get("progress", 0.0),
        "stream_state": job.get("stream_state"),
        "segment_frames": job.get("segment_frames", SEGMENT_FRAMES),
        "window_frames": job.get("window_frames", WINDOW_FRAMES),
        "processed_segments": job.get("processed_segments", 0),
        "analyzed_windows": job.get("analyzed_windows", 0),
        "buffered_segments": job.get("buffered_segments", 0),
        "source_fps": job.get("source_fps", 0.0),
        "processing_fps": processing_fps,
        "current_fps": processing_fps,
        "latency_ms": job.get("latency_ms", 0.0),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "output_dir": job.get("output_dir"),
        "report_id": job.get("report_id"),
        "report_href": job.get("report_href"),
        "preview_href": job.get("preview_href"),
        "event_count": job.get("event_count", 0),
        "current_sec": job.get("current_sec", 0.0),
        "total_frames": job.get("total_frames", 0),
        "error": job.get("error"),
        "summary": job.get("summary", {}),
        "latest_alerts": deepcopy(job.get("latest_alerts", [])),
        "exports": deepcopy(job.get("exports", [])),
    }
    return sanitize_for_json(payload)


def _record_job_history(job: dict[str, Any]) -> None:
    append_history(JOB_HISTORY_PATH, _job_public_view(job))


def _latest_report_history() -> list[dict[str, Any]]:
    latest_by_report: dict[str, dict[str, Any]] = {}
    for record in read_history(HISTORY_PATH):
        report_href = record.get("report_href")
        if not report_href:
            continue
        existing = latest_by_report.get(report_href)
        if existing is None or record.get("generated_at", "") >= existing.get("generated_at", ""):
            latest_by_report[report_href] = record
    return sorted(latest_by_report.values(), key=lambda item: item.get("generated_at", ""), reverse=True)


def _seed_runtime_from_history() -> None:
    global BOOTSTRAPPED_HISTORY
    with RUNTIME_LOCK:
        if BOOTSTRAPPED_HISTORY:
            return

        known_report_ids = set()
        for record in read_history(JOB_HISTORY_PATH):
            job_id = record.get("job_id")
            if not job_id:
                continue
            JOB_STORE[job_id] = deepcopy(record)
            if record.get("report_id"):
                known_report_ids.add(record["report_id"])

        for record in _latest_report_history():
            report_href = record["report_href"]
            report_id = _report_id_from_href(report_href)
            if report_id in known_report_ids:
                continue
            report_dir = _report_dir_from_id(report_id)
            preview_href = None
            summary = {}
            analysis_path = report_dir / "analysis.current.json"
            if analysis_path.exists():
                summary = read_json(analysis_path).get("summary", {})
                preview_href = _served_href_for_path(summary.get("video_path"))
            JOB_STORE[f"hist-{report_id}"] = {
                "job_id": f"hist-{report_id}",
                "source_type": "history",
                "source_label": record.get("video_name"),
                "dataset_name": record.get("dataset_name"),
                "dataset_display_name": record.get("dataset_display_name"),
                "video_name": record.get("video_name"),
                "status": "completed",
                "progress_mode": "determinate",
                "progress": 1.0,
                "stream_state": None,
                "segment_frames": summary.get("segment_frames", SEGMENT_FRAMES),
                "window_frames": summary.get("window_frames", WINDOW_FRAMES),
                "processed_segments": summary.get("processed_segments", 0),
                "analyzed_windows": summary.get("analyzed_windows", 0),
                "buffered_segments": 0,
                "source_fps": summary.get("fps", 0.0),
                "processing_fps": summary.get("processing_fps", 0.0),
                "current_fps": 0.0,
                "latency_ms": 0.0,
                "created_at": record.get("generated_at"),
                "updated_at": record.get("generated_at"),
                "output_dir": str(report_dir),
                "report_id": report_id,
                "report_href": report_href,
                "preview_href": preview_href,
                "event_count": record.get("event_count", 0),
                "current_sec": 0.0,
                "total_frames": 0,
                "error": None,
                "summary": summary,
                "latest_alerts": [],
                "exports": _report_artifacts(report_dir),
            }
        BOOTSTRAPPED_HISTORY = True


def _set_job_state(job_id: str, **updates: Any) -> dict[str, Any]:
    with RUNTIME_LOCK:
        job = JOB_STORE[job_id]
        job.update(updates)
        job["updated_at"] = iso_now()
        return deepcopy(job)


def _get_job(job_id: str) -> dict[str, Any]:
    _seed_runtime_from_history()
    with RUNTIME_LOCK:
        if job_id not in JOB_STORE:
            raise FileNotFoundError(f"Job '{job_id}' not found.")
        return deepcopy(JOB_STORE[job_id])


def _list_history_jobs() -> list[dict[str, Any]]:
    latest_by_job: dict[str, dict[str, Any]] = {}
    for record in read_history(JOB_HISTORY_PATH):
        job_id = record.get("job_id")
        if not job_id:
            continue
        existing = latest_by_job.get(job_id)
        if existing is None or record.get("updated_at", "") >= existing.get("updated_at", ""):
            latest_by_job[job_id] = record

    known_report_ids = {
        str(record.get("report_id"))
        for record in latest_by_job.values()
        if record.get("report_id")
    }
    for record in _latest_report_history():
        report_href = record.get("report_href")
        if not report_href:
            continue
        report_id = _report_id_from_href(report_href)
        if report_id in known_report_ids:
            continue
        report_dir = _report_dir_from_id(report_id)
        preview_href = None
        summary = {}
        analysis_path = report_dir / "analysis.current.json"
        if analysis_path.exists():
            summary = read_json(analysis_path).get("summary", {})
            preview_href = _served_href_for_path(summary.get("video_path"))
        latest_by_job[f"hist-{report_id}"] = {
            "job_id": f"hist-{report_id}",
            "source_type": "history",
            "source_label": record.get("video_name"),
            "dataset_name": record.get("dataset_name"),
            "dataset_display_name": record.get("dataset_display_name"),
            "video_name": record.get("video_name"),
            "status": "completed",
            "progress_mode": "determinate",
            "progress": 1.0,
            "stream_state": None,
            "segment_frames": summary.get("segment_frames", SEGMENT_FRAMES),
            "window_frames": summary.get("window_frames", WINDOW_FRAMES),
            "processed_segments": summary.get("processed_segments", 0),
            "analyzed_windows": summary.get("analyzed_windows", 0),
            "buffered_segments": 0,
            "source_fps": summary.get("fps", 0.0),
            "processing_fps": summary.get("processing_fps", 0.0),
            "current_fps": 0.0,
            "latency_ms": 0.0,
            "created_at": record.get("generated_at"),
            "updated_at": record.get("generated_at"),
            "output_dir": str(report_dir),
            "report_id": report_id,
            "report_href": report_href,
            "preview_href": preview_href,
            "event_count": record.get("event_count", 0),
            "current_sec": 0.0,
            "total_frames": 0,
            "error": None,
            "summary": summary,
            "latest_alerts": [],
            "exports": _report_artifacts(report_dir),
        }

    jobs = [_job_public_view(job) for job in latest_by_job.values()]
    return sorted(jobs, key=lambda item: item.get("created_at", ""), reverse=True)


def _create_uploaded_source(filename: str, payload: bytes, dataset_hint: str | None = None) -> dict[str, Any]:
    if not payload:
        raise ValueError("Uploaded file is empty.")
    ensure_output_root(OUTPUT_ROOT)
    UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    source_id = f"src_{uuid.uuid4().hex[:10]}"
    stored_name = f"{source_id}_{_sanitize_filename(filename)}"
    file_path = UPLOADS_ROOT / stored_name
    file_path.write_bytes(payload)

    source = {
        "source_id": source_id,
        "source_type": "upload",
        "label": filename,
        "filename": Path(filename).name,
        "file_path": str(file_path),
        "dataset_hint": (dataset_hint or "").strip().lower() or None,
        "created_at": iso_now(),
        "media_href": _served_href_for_path(file_path),
    }
    with RUNTIME_LOCK:
        SOURCE_STORE[source_id] = source
    return source


def _report_artifacts(report_dir: Path) -> list[dict[str, Any]]:
    clips_dir = report_dir / "clips"
    artifacts = [
        {
            "kind": "json",
            "label": "事件 JSON",
            "path": str(report_dir / "events.current.json"),
            "href": f"/campus_demo_outputs/reports/{report_dir.name}/events.current.json",
        },
        {
            "kind": "csv",
            "label": "事件 CSV",
            "path": str(report_dir / "events.current.csv"),
            "href": f"/campus_demo_outputs/reports/{report_dir.name}/events.current.csv",
        },
        {
            "kind": "analysis",
            "label": "analysis.current.json",
            "path": str(report_dir / "analysis.current.json"),
            "href": f"/campus_demo_outputs/reports/{report_dir.name}/analysis.current.json",
        },
        {
            "kind": "clips",
            "label": "异常切片清单",
            "path": str(report_dir / "clips_manifest.json"),
            "href": f"/campus_demo_outputs/reports/{report_dir.name}/clips_manifest.json",
        },
        {
            "kind": "zip",
            "label": "报告 ZIP",
            "path": str(report_dir / "report_bundle.zip"),
            "href": f"/campus_demo_outputs/reports/{report_dir.name}/report_bundle.zip",
        },
    ]
    if clips_dir.exists():
        for clip_path in sorted(clips_dir.glob("*.mp4"))[:5]:
            artifacts.append(
                {
                    "kind": "clip_file",
                    "label": clip_path.name,
                    "path": str(clip_path),
                    "href": f"/campus_demo_outputs/reports/{report_dir.name}/clips/{clip_path.name}",
                }
            )
    return artifacts


def _load_job_events(job_id: str, version: str = "current") -> list[dict[str, Any]]:
    job = _get_job(job_id)
    report_id = job.get("report_id")
    if report_id:
        report_dir = _report_dir_from_id(report_id)
        clip_lookup = _load_clip_lookup(report_dir)
        if version == "original":
            raw_events = load_report_events(report_dir, prefer_current=False)
        else:
            raw_events = load_report_events(report_dir, prefer_current=True)
        return [_alert_event_from_raw(event, clip_lookup) for event in raw_events]
    return deepcopy(job.get("events_cache", job.get("latest_alerts", [])))


def _save_job_completed_if_reviewed(job_id: str) -> None:
    job = _get_job(job_id)
    if job.get("status") == "reviewable":
        updated = _set_job_state(job_id, status="completed")
        _record_job_history(updated)


def _runtime_report_stem(job: dict[str, Any], video_name: str) -> str:
    stem = Path(video_name or job["job_id"]).stem or job["job_id"]
    return f"{job.get('source_type', 'job')}-{job['job_id']}-{stem}"


def _apply_runtime_progress(job_id: str, snapshot: dict[str, Any]) -> None:
    events = [_alert_event_from_raw(event) for event in snapshot.get("events", [])]
    updates = {
        "status": snapshot.get("status", "running"),
        "progress_mode": snapshot.get("progress_mode", "determinate"),
        "progress": snapshot.get("progress", 0.0),
        "stream_state": snapshot.get("stream_state"),
        "total_frames": snapshot.get("total_frames", 0),
        "current_sec": snapshot.get("current_sec", 0.0),
        "source_fps": snapshot.get("source_fps", 0.0),
        "processing_fps": snapshot.get("processing_fps", 0.0),
        "current_fps": snapshot.get("current_fps", 0.0),
        "latency_ms": snapshot.get("latency_ms", 0.0),
        "processed_segments": snapshot.get("processed_segments", 0),
        "analyzed_windows": snapshot.get("analyzed_windows", 0),
        "buffered_segments": snapshot.get("buffered_segments", 0),
        "event_count": len(events),
        "events_cache": events,
        "latest_alerts": events[-5:],
    }
    preview_path = snapshot.get("preview_path")
    if preview_path:
        preview_href = _served_href_for_path(preview_path)
        if preview_href:
            updates["preview_href"] = preview_href
    _set_job_state(job_id, **updates)


def _finalize_runtime_job(job_id: str, job: dict[str, Any], result: Any) -> dict[str, Any]:
    report_dir = build_runtime_report(
        result,
        report_stem=_runtime_report_stem(job, result.video_name),
        history_record_extra={
            "job_id": job_id,
            "source_type": job.get("source_type"),
            "source_label": job.get("source_label"),
        },
    )
    report_clip_lookup = _load_clip_lookup(report_dir)
    current_events = [
        _alert_event_from_raw(event, report_clip_lookup)
        for event in load_report_events(report_dir, prefer_current=True)
    ]
    summary = read_json(report_dir / "analysis.current.json").get("summary", {})
    final_preview_href = _served_href_for_path(result.video_path) or job.get("preview_href")
    final_stream_state = "stopped" if job.get("source_type") == "rtsp" else None
    completed_job = _set_job_state(
        job_id,
        status="reviewable",
        progress_mode="indeterminate" if job.get("source_type") == "rtsp" else "determinate",
        progress=1.0,
        stream_state=final_stream_state,
        total_frames=result.total_frames,
        source_fps=result.source_fps,
        processing_fps=result.processing_fps,
        current_fps=result.processing_fps,
        current_sec=round(result.total_frames / max(result.source_fps, 1.0), 2),
        processed_segments=result.processed_segments,
        analyzed_windows=result.analyzed_windows,
        buffered_segments=0,
        event_count=len(current_events),
        events_cache=current_events,
        latest_alerts=current_events[-5:],
        output_dir=str(report_dir),
        report_id=report_dir.name,
        report_href=_report_href_from_dir(report_dir),
        exports=_report_artifacts(report_dir),
        summary=summary,
        preview_href=final_preview_href,
        error=None,
    )
    _record_job_history(completed_job)
    return completed_job


def prepare_analysis(
    dataset_name: str,
    video_name: str,
    *,
    video_path_override: Path | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    ensure_output_root(OUTPUT_ROOT)
    store = get_store(dataset_name)
    video = store.load_video(video_name)
    if video_path_override is not None:
        video.video_path = video_path_override

    events, thresholds = build_events(video.scores, video.fps)
    enriched_events = enrich_events(events, video.source_class, video.captions, video.reasoning)
    mapping = get_mapping(video.source_class)

    summary = {
        "dataset_name": video.dataset_name,
        "dataset_display_name": video.dataset_display_name,
        "video_name": video.video_name,
        "video_stem": video.video_stem,
        "video_path": str(video.video_path) if video.video_path else None,
        "source_class": video.source_class,
        "campus_label": mapping.campus_label,
        "headline": mapping.summary,
        "event_count": len(enriched_events),
        "peak_score": max(video.scores) if video.scores else 0.0,
        "mean_score": round(sum(video.scores) / len(video.scores), 4) if video.scores else 0.0,
        "threshold": thresholds["threshold"],
        "high_threshold": thresholds["high_threshold"],
        "roc_auc": video.metrics.get("roc_auc"),
        "pr_auc": video.metrics.get("pr_auc"),
        "pos_mean": video.metrics.get("1_mean"),
        "neg_mean": video.metrics.get("0_mean"),
        "fps": video.fps,
    }
    analysis = {
        "summary": summary,
        "events": enriched_events,
        "metrics": video.metrics,
        "thresholds": thresholds,
    }
    return video, summary, analysis, enriched_events


def build_report(
    dataset_name: str,
    video_name: str,
    output_root: Path = OUTPUT_ROOT,
    *,
    video_path_override: Path | None = None,
    history_record_extra: dict[str, Any] | None = None,
) -> Path:
    video, summary, analysis, enriched_events = prepare_analysis(
        dataset_name,
        video_name,
        video_path_override=video_path_override,
    )
    report_dir = report_directory(video.dataset_name, video.video_stem, output_root)
    report_dir.mkdir(parents=True, exist_ok=True)

    clip_manifest, clip_mode = export_clips(report_dir, video.video_path, enriched_events)
    summary["clip_mode"] = clip_mode
    analysis["clip_manifest"] = clip_manifest

    score_svg = build_score_svg(
        video.scores,
        enriched_events,
        analysis["thresholds"]["threshold"],
        analysis["thresholds"]["high_threshold"],
    )
    (report_dir / "score.svg").write_text(score_svg, encoding="utf-8")
    write_json(report_dir / "analysis.json", analysis)
    write_event_exports(report_dir, enriched_events, current=False)
    write_current_analysis(report_dir, analysis, enriched_events)
    write_event_exports(report_dir, enriched_events, current=True)
    write_json(
        report_dir / "report_meta.json",
        {
            "report_id": report_dir.name,
            "dataset_name": video.dataset_name,
            "video_name": video.video_name,
            "generated_at": iso_now(),
            **(history_record_extra or {}),
        },
    )
    (report_dir / "index.html").write_text(
        build_report_html(report_dir, summary, enriched_events, clip_manifest, clip_mode),
        encoding="utf-8",
    )
    build_report_bundle(report_dir)

    history_record = {
        "dataset_name": video.dataset_name,
        "dataset_display_name": video.dataset_display_name,
        "video_name": video.video_name,
        "source_class": video.source_class,
        "campus_label": summary["campus_label"],
        "event_count": len(enriched_events),
        "peak_score": summary["peak_score"],
        "generated_at": iso_now(),
        "report_href": _report_href_from_dir(report_dir),
    }
    history_record.update(history_record_extra or {})
    append_history(HISTORY_PATH, history_record)
    build_history_index(output_root, read_history(HISTORY_PATH))
    return report_dir


def save_report_events(report_id: str, edited_events: list[dict[str, Any]]) -> Path:
    report_dir = _report_dir_from_id(report_id)
    original_events = load_report_events(report_dir, prefer_current=True)
    original_by_id = {event["event_id"]: dict(event) for event in original_events}

    merged_events: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    for event in edited_events:
        event_id = event.get("event_id")
        if event_id not in original_by_id:
            continue
        seen_event_ids.add(str(event_id))
        merged = dict(original_by_id[event_id])
        behavior_type = event.get("behavior_type") or event.get("campus_label")
        if behavior_type is not None:
            behavior_text = str(behavior_type).strip() or merged.get("behavior_type") or merged.get("campus_label")
            merged["behavior_type"] = behavior_text
            merged["campus_label"] = behavior_text
        if "risk_level" in event and event["risk_level"] is not None:
            merged["risk_level"] = _normalize_risk_level(event["risk_level"])
        if "reason_text" in event and event["reason_text"] is not None:
            merged["reason_text"] = str(event["reason_text"]).strip()
        if "note" in event and event["note"] is not None:
            merged["note"] = str(event["note"]).strip()
        if "review_status" in event and event["review_status"] is not None:
            merged["review_status"] = _normalize_review_status(event["review_status"])
        if "track_ids" in event and event["track_ids"] is not None:
            merged["track_ids"] = _normalize_track_ids(event["track_ids"], merged["event_id"])
        merged["last_edited_at"] = iso_now()
        merged_events.append(merged)

    for event_id, original_event in original_by_id.items():
        if str(event_id) not in seen_event_ids:
            merged_events.append(dict(original_event))

    if not merged_events:
        raise ValueError("No valid event payload was provided.")

    analysis = read_json(report_dir / "analysis.json")
    write_event_exports(report_dir, merged_events, current=True)
    write_current_analysis(report_dir, analysis, merged_events)
    build_report_bundle(report_dir)
    return report_dir


def command_list(dataset_name: str) -> None:
    store = get_store(dataset_name)
    samples = set(store.default_samples())
    for name in store.available_videos():
        marker = "*" if name in samples else " "
        print(f"{marker} {name}")


def command_build_samples(dataset_name: str) -> None:
    store = get_store(dataset_name)
    for video_name in store.default_samples():
        report_dir = build_report(dataset_name, video_name)
        print(report_dir)


def _console_html() -> str:
    return CONSOLE_HTML_PATH.read_text(encoding="utf-8")


def _create_job(payload: dict[str, Any]) -> dict[str, Any]:
    _seed_runtime_from_history()

    source_type = str(payload.get("source_type") or "upload").strip().lower()
    dataset_name = str(payload.get("dataset") or "ucf").strip().lower()
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    source_label = ""
    video_name = payload.get("video")
    preview_href = None
    source_id = payload.get("source_id")
    source_file_path = None
    rtsp_url = None

    if source_type == "sample":
        if not video_name:
            raise ValueError("Missing sample video name.")
        source_label = str(video_name)
    elif source_type == "upload":
        if not source_id:
            raise ValueError("Missing uploaded source id.")
        with RUNTIME_LOCK:
            source = SOURCE_STORE.get(source_id)
        if source is None:
            raise FileNotFoundError("Uploaded source does not exist.")
        source_label = source["label"]
        preview_href = source.get("media_href")
        source_file_path = Path(source["file_path"])
        video_name = str(video_name or source["filename"])
        dataset_name = str(payload.get("dataset") or source.get("dataset_hint") or "ucf").strip().lower()
    elif source_type == "rtsp":
        source_label = str(payload.get("rtsp_url") or "").strip()
        rtsp_url = source_label
        video_name = str(video_name or f"rtsp-{job_id}.mp4")
        if not source_label:
            raise ValueError("RTSP 地址不能为空。")
    else:
        raise ValueError(f"Unsupported source type '{source_type}'.")

    store = get_store(dataset_name)
    job = {
        "job_id": job_id,
        "source_type": source_type,
        "source_label": source_label,
        "source_id": source_id,
        "dataset_name": dataset_name,
        "dataset_display_name": store.config.display_name,
        "video_name": video_name,
        "status": "queued",
        "progress_mode": "indeterminate" if source_type == "rtsp" else "determinate",
        "progress": 0.0,
        "stream_state": "connecting" if source_type == "rtsp" else None,
        "segment_frames": SEGMENT_FRAMES,
        "window_frames": WINDOW_FRAMES,
        "processed_segments": 0,
        "analyzed_windows": 0,
        "buffered_segments": 0,
        "source_fps": 0.0,
        "processing_fps": 0.0,
        "current_fps": 0.0,
        "latency_ms": 0.0,
        "created_at": iso_now(),
        "updated_at": iso_now(),
        "output_dir": None,
        "report_id": None,
        "report_href": None,
        "preview_href": preview_href,
        "event_count": 0,
        "current_sec": 0.0,
        "total_frames": 0,
        "error": None,
        "summary": {},
        "latest_alerts": [],
        "events_cache": [],
        "exports": [],
        "_source_file_path": str(source_file_path) if source_file_path else None,
        "_rtsp_url": rtsp_url,
    }
    with RUNTIME_LOCK:
        JOB_STORE[job_id] = job
        JOB_CANCEL_FLAGS[job_id] = threading.Event()
    return deepcopy(job)


def _run_analysis_job(job_id: str) -> None:
    cancel_flag = JOB_CANCEL_FLAGS[job_id]
    job = _get_job(job_id)
    source_type = job["source_type"]
    dataset_name = job["dataset_name"]
    video_name = job.get("video_name")
    source_file_path = Path(job["_source_file_path"]) if job.get("_source_file_path") else None

    try:
        time.sleep(0.15)
        if source_type == "upload":
            if source_file_path is None:
                raise RuntimeError("Uploaded source file is missing.")
            _set_job_state(job_id, status="running", progress_mode="determinate")
            result = run_upload_analysis(
                source_path=source_file_path,
                dataset_name=dataset_name,
                source_label=str(job.get("source_label") or video_name or ""),
                video_name=str(video_name or source_file_path.name),
                cancel_flag=cancel_flag,
                progress_callback=lambda snapshot: _apply_runtime_progress(job_id, snapshot),
                job_id=job_id,
            )
            _finalize_runtime_job(job_id, job, result)
            return

        if source_type == "rtsp":
            _set_job_state(job_id, status="running", progress_mode="indeterminate", stream_state="connecting")
            result = run_rtsp_analysis(
                rtsp_url=str(job.get("_rtsp_url") or job.get("source_label") or ""),
                dataset_name=dataset_name,
                recording_name=str(video_name or f"rtsp-{job_id}.mp4"),
                cancel_flag=cancel_flag,
                progress_callback=lambda snapshot: _apply_runtime_progress(job_id, snapshot),
                job_id=job_id,
            )
            _finalize_runtime_job(job_id, job, result)
            return
        if cancel_flag.is_set():
            raise JobCancelled("任务已取消。")
        if source_type == "rtsp":
            raise RuntimeError("RTSP 输入接口已预留，但当前比赛演示环境尚未接入实时流推理。")

        video, summary, _analysis, enriched_events = prepare_analysis(
            dataset_name,
            str(video_name),
            video_path_override=source_file_path,
        )
        preview_href = _served_href_for_path(video.video_path)
        total_frames = len(video.scores)
        total_steps = max(8, min(20, total_frames // max(1, int(video.fps * 3))))
        normalized_events = [_alert_event_from_raw(event) for event in enriched_events]
        _set_job_state(
            job_id,
            status="running",
            summary=summary,
            total_frames=total_frames,
            preview_href=preview_href,
        )

        for step in range(1, total_steps + 1):
            if cancel_flag.is_set():
                raise JobCancelled("任务已取消。")
            progress = round(min(0.96, step / total_steps), 4)
            current_frame = min(total_frames - 1, int(total_frames * progress)) if total_frames else 0
            current_sec = round(current_frame / max(video.fps, 1.0), 2)
            visible_events = [event for event in normalized_events if (event.get("start_frame") or 0) <= current_frame]
            _set_job_state(
                job_id,
                status="running",
                progress=progress,
                current_fps=round(video.fps * 0.82, 2),
                latency_ms=round(120 + 25 * (step % 4), 2),
                current_sec=current_sec,
                event_count=len(visible_events),
                events_cache=visible_events,
                latest_alerts=visible_events[-5:],
            )
            time.sleep(0.35)

        report_dir = build_report(
            dataset_name,
            video.video_name,
            video_path_override=source_file_path,
            history_record_extra={
                "job_id": job_id,
                "source_type": source_type,
                "source_label": job.get("source_label"),
            },
        )
        report_clip_lookup = _load_clip_lookup(report_dir)
        current_events = [
            _alert_event_from_raw(event, report_clip_lookup)
            for event in load_report_events(report_dir, prefer_current=True)
        ]
        completed_job = _set_job_state(
            job_id,
            status="reviewable",
            progress=1.0,
            current_fps=round(video.fps * 0.82, 2),
            latency_ms=98.0,
            current_sec=round((total_frames - 1) / max(video.fps, 1.0), 2) if total_frames else 0.0,
            event_count=len(current_events),
            events_cache=current_events,
            latest_alerts=current_events[-5:],
            output_dir=str(report_dir),
            report_id=report_dir.name,
            report_href=_report_href_from_dir(report_dir),
            exports=_report_artifacts(report_dir),
            summary=read_json(report_dir / "analysis.current.json").get("summary", {}),
        )
        _record_job_history(completed_job)
    except JobCancelled as exc:
        cancelled_job = _set_job_state(job_id, status="cancelled", error=str(exc))
        _record_job_history(cancelled_job)
    except AnalysisCancelledError as exc:
        cancelled_job = _set_job_state(
            job_id,
            status="cancelled",
            stream_state="stopped" if source_type == "rtsp" else job.get("stream_state"),
            error=str(exc),
        )
        _record_job_history(cancelled_job)
    except Exception as exc:
        failed_job = _set_job_state(
            job_id,
            status="failed",
            stream_state="failed" if source_type == "rtsp" else job.get("stream_state"),
            error=str(exc),
        )
        _record_job_history(failed_job)


def _launch_job(payload: dict[str, Any]) -> dict[str, Any]:
    job = _create_job(payload)
    thread = threading.Thread(target=_run_analysis_job, args=(job["job_id"],), daemon=True)
    thread.start()
    return _job_public_view(job)


def _export_job(job_id: str, kinds: list[str]) -> dict[str, Any]:
    job = _get_job(job_id)
    report_id = job.get("report_id")
    if not report_id:
        raise ValueError("当前任务尚未生成可导出的报告。")

    report_dir = _report_dir_from_id(report_id)
    current_events = load_report_events(report_dir, prefer_current=True)
    analysis = read_json(report_dir / "analysis.json")

    if any(kind in {"json", "csv", "all"} for kind in kinds):
        write_event_exports(report_dir, current_events, current=True)
    if any(kind in {"clips", "zip", "all"} for kind in kinds):
        video_path = analysis.get("summary", {}).get("video_path")
        export_clips(report_dir, Path(video_path) if video_path and Path(video_path).exists() else None, current_events)
    if any(kind in {"zip", "all"} for kind in kinds):
        write_current_analysis(report_dir, analysis, current_events)
        build_report_bundle(report_dir)

    artifacts = _report_artifacts(report_dir)
    updated_events = [
        _alert_event_from_raw(event, _load_clip_lookup(report_dir))
        for event in load_report_events(report_dir, prefer_current=True)
    ]
    updated_job = _set_job_state(job_id, exports=artifacts, events_cache=updated_events, latest_alerts=updated_events[-5:])
    _save_job_completed_if_reviewed(job_id)
    return {
        "job": _job_public_view(updated_job),
        "artifacts": artifacts,
    }


def _patch_event(event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = payload.get("job_id")
    if not job_id:
        raise ValueError("Missing job_id in event patch payload.")
    job = _get_job(str(job_id))
    report_id = job.get("report_id")
    if not report_id:
        raise ValueError("当前任务尚未进入可编辑状态。")
    report_dir = _report_dir_from_id(report_id)

    current_events = load_report_events(report_dir, prefer_current=True)
    edited_events: list[dict[str, Any]] = []
    found = False
    for event in current_events:
        if event.get("event_id") != event_id:
            edited_events.append(event)
            continue
        found = True
        merged = dict(event)
        if "behavior_type" in payload or "campus_label" in payload:
            behavior_type = payload.get("behavior_type") or payload.get("campus_label")
            merged["behavior_type"] = str(behavior_type).strip()
            merged["campus_label"] = merged["behavior_type"]
        if "risk_level" in payload:
            merged["risk_level"] = _normalize_risk_level(payload["risk_level"])
        if "note" in payload:
            merged["note"] = str(payload.get("note") or "").strip()
        if "review_status" in payload:
            merged["review_status"] = _normalize_review_status(payload["review_status"])
        if "track_ids" in payload:
            merged["track_ids"] = _normalize_track_ids(payload["track_ids"], event_id)
        edited_events.append(merged)

    if not found:
        raise KeyError(f"Event '{event_id}' not found.")

    save_report_events(report_id, edited_events)
    clip_lookup = _load_clip_lookup(report_dir)
    updated_events = [_alert_event_from_raw(event, clip_lookup) for event in load_report_events(report_dir, prefer_current=True)]
    updated_job = _set_job_state(
        job_id,
        events_cache=updated_events,
        latest_alerts=updated_events[-5:],
        event_count=len(updated_events),
        exports=_report_artifacts(report_dir),
    )
    _save_job_completed_if_reviewed(job_id)
    _record_job_history(updated_job)
    matched = next(event for event in updated_events if event["event_id"] == event_id)
    return {"job": _job_public_view(updated_job), "event": matched}


def _train_stub(recipe_id: str) -> dict[str, Any]:
    train_id = f"train_{uuid.uuid4().hex[:8]}"
    metrics_path = OUTPUT_ROOT / "history" / f"{train_id}_metrics.json"
    payload = {
        "train_id": train_id,
        "recipe_id": recipe_id,
        "status": "completed",
        "model_alias": "demo-campusguard-baseline",
        "metrics_path": str(metrics_path),
    }
    write_json(
        metrics_path,
        {
            **payload,
            "message": "比赛演示环境返回的是轻量占位产物，用于串联前端流程。",
        },
    )
    return payload


def _eval_stub(dataset: str) -> dict[str, Any]:
    return {
        "eval_id": f"eval_{uuid.uuid4().hex[:8]}",
        "dataset": dataset,
        "status": "completed",
        "message": "当前为比赛演示环境的轻量评测接口占位实现。",
    }


def _status_for_exception(exc: Exception) -> int:
    if isinstance(exc, ApiError):
        return exc.status_code
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return 400
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return 404
    return 500


class CampusDemoHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, directory=str(REPO_ROOT), **kwargs)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(sanitize_for_json(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, body: str, content_type: str = "text/html; charset=utf-8", status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length else b"{}"
        text = raw.decode("utf-8") if raw else "{}"
        return json.loads(text or "{}")

    def _read_binary_body(self) -> bytes:
        content_length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(content_length) if content_length else b""

    def _health_payload(self) -> dict[str, Any]:
        _seed_runtime_from_history()
        with RUNTIME_LOCK:
            job_count = len(JOB_STORE)
        return {
            "ok": True,
            "status": "healthy",
            "output_root": str(OUTPUT_ROOT),
            "job_count": job_count,
            "dataset_count": len(available_datasets()),
            "server_time": iso_now(),
        }

    def _handle_api_get(self, parsed: Any) -> bool:
        path = parsed.path
        if path == "/campus_demo/api/datasets":
            payload = [
                {"name": dataset_name, "display_name": get_store(dataset_name).config.display_name}
                for dataset_name in available_datasets()
            ]
            self._send_json(payload)
            return True

        if path == "/campus_demo/api/videos":
            dataset = parse_qs(parsed.query).get("dataset", ["ucf"])[0]
            store = get_store(dataset)
            self._send_json(
                {
                    "dataset": dataset,
                    "dataset_display_name": store.config.display_name,
                    "videos": store.list_videos(),
                    "default_samples": store.default_samples(),
                }
            )
            return True

        if path == "/campus_demo/api/history":
            self._send_json(_list_history_jobs())
            return True

        if path == "/campus_demo/api/health":
            self._send_json(self._health_payload())
            return True

        if path == "/campus_demo/api/report-state":
            report_id = parse_qs(parsed.query).get("report_id", [""])[0]
            report_dir = _report_dir_from_id(report_id)
            clip_lookup = _load_clip_lookup(report_dir)
            analysis_path = report_dir / "analysis.current.json"
            if not analysis_path.exists():
                analysis_path = report_dir / "analysis.json"
            self._send_json(
                {
                    "summary": read_json(analysis_path).get("summary", {}),
                    "events": [_alert_event_from_raw(event, clip_lookup) for event in load_report_events(report_dir, prefer_current=True)],
                    "events_original": [_alert_event_from_raw(event, clip_lookup) for event in load_report_events(report_dir, prefer_current=False)],
                    "exports": _report_artifacts(report_dir),
                }
            )
            return True

        job_match = re.fullmatch(r"/campus_demo/api/jobs/([^/]+)", path)
        if job_match:
            self._send_json({"job": _job_public_view(_get_job(job_match.group(1)))})
            return True

        events_match = re.fullmatch(r"/campus_demo/api/jobs/([^/]+)/events", path)
        if events_match:
            version = parse_qs(parsed.query).get("version", ["current"])[0]
            self._send_json(_load_job_events(events_match.group(1), version=version))
            return True

        stream_match = re.fullmatch(r"/campus_demo/api/jobs/([^/]+)/stream", path)
        if stream_match:
            self._serve_job_stream(stream_match.group(1))
            return True

        train_match = re.fullmatch(r"/campus_demo/api/train/([^/]+)", path)
        if train_match:
            metrics_path = OUTPUT_ROOT / "history" / f"{train_match.group(1)}_metrics.json"
            if not metrics_path.exists():
                raise FileNotFoundError(f"Train job '{train_match.group(1)}' does not exist.")
            self._send_json(read_json(metrics_path))
            return True

        return False

    def _handle_api_post(self, parsed: Any) -> bool:
        path = parsed.path
        if path == "/campus_demo/api/sources/upload":
            filename = self.headers.get("X-Filename")
            encoded_filename = self.headers.get("X-Filename-Encoded")
            if encoded_filename:
                filename = unquote(encoded_filename)
            filename = filename or "upload.mp4"
            dataset_hint = self.headers.get("X-Source-Dataset")
            source = _create_uploaded_source(filename, self._read_binary_body(), dataset_hint)
            self._send_json({"ok": True, **source})
            return True

        if path == "/campus_demo/api/jobs":
            payload = self._read_json_body()
            self._send_json({"ok": True, "job": _launch_job(payload)})
            return True

        cancel_match = re.fullmatch(r"/campus_demo/api/jobs/([^/]+)/cancel", path)
        if cancel_match:
            job_id = cancel_match.group(1)
            with RUNTIME_LOCK:
                if job_id not in JOB_CANCEL_FLAGS:
                    raise KeyError(f"Job '{job_id}' not found.")
                JOB_CANCEL_FLAGS[job_id].set()
            job = _get_job(job_id)
            if job.get("source_type") == "rtsp":
                _set_job_state(job_id, stream_state="stopped")
            self._send_json({"ok": True, "job": _job_public_view(_get_job(job_id))})
            return True

        export_match = re.fullmatch(r"/campus_demo/api/jobs/([^/]+)/export", path)
        if export_match:
            payload = self._read_json_body()
            kinds = payload.get("kinds") or [payload.get("kind") or "all"]
            kinds = [str(kind).strip().lower() for kind in kinds if kind]
            self._send_json({"ok": True, **_export_job(export_match.group(1), kinds or ["all"])})
            return True

        if path == "/campus_demo/api/build-report":
            payload = self._read_json_body()
            report_dir = build_report(payload.get("dataset", "ucf"), payload["video"])
            self._send_json({"ok": True, "report_href": _report_href_from_dir(report_dir)})
            return True

        if path == "/campus_demo/api/build-samples":
            payload = self._read_json_body()
            dataset = payload.get("dataset", "ucf")
            store = get_store(dataset)
            reports = []
            for video_name in store.default_samples():
                report_dir = build_report(dataset, video_name)
                reports.append(_report_href_from_dir(report_dir))
            self._send_json({"ok": True, "reports": reports})
            return True

        if path == "/campus_demo/api/save-events":
            payload = self._read_json_body()
            report_dir = save_report_events(payload["report_id"], payload.get("events", []))
            self._send_json(
                {
                    "ok": True,
                    "report_href": _report_href_from_dir(report_dir),
                    "events_path": f"reports/{report_dir.name}/events.current.json",
                }
            )
            return True

        if path == "/campus_demo/api/train":
            payload = self._read_json_body()
            self._send_json({"ok": True, "job": _train_stub(str(payload.get("recipe_id") or "demo-recipe"))})
            return True

        if path == "/campus_demo/api/eval":
            payload = self._read_json_body()
            self._send_json({"ok": True, "job": _eval_stub(str(payload.get("dataset") or "ucf"))})
            return True

        return False

    def _handle_api_patch(self, parsed: Any) -> bool:
        event_match = re.fullmatch(r"/campus_demo/api/events/([^/]+)", parsed.path)
        if event_match:
            payload = self._read_json_body()
            self._send_json({"ok": True, **_patch_event(event_match.group(1), payload)})
            return True
        return False

    def _serve_job_stream(self, job_id: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                payload = {
                    "job": _job_public_view(_get_job(job_id)),
                    "events": _load_job_events(job_id, version="current"),
                }
                self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
                if payload["job"]["status"] in TERMINAL_JOB_STATUSES | {"reviewable"}:
                    break
                time.sleep(1.0)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/campus_demo/console":
            self._send_text(_console_html())
            return

        try:
            if self._handle_api_get(parsed):
                return
        except Exception as exc:  # pragma: no cover - runtime path
            self._send_json({"ok": False, "error": str(exc)}, status=_status_for_exception(exc))
            return

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if self._handle_api_post(parsed):
                return
        except Exception as exc:  # pragma: no cover - runtime path
            self._send_json({"ok": False, "error": str(exc)}, status=_status_for_exception(exc))
            return
        self._send_json({"ok": False, "error": "Not found"}, status=404)

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        try:
            if self._handle_api_patch(parsed):
                return
        except Exception as exc:  # pragma: no cover - runtime path
            self._send_json({"ok": False, "error": str(exc)}, status=_status_for_exception(exc))
            return
        self._send_json({"ok": False, "error": "Not found"}, status=404)


def command_serve(host: str, port: int) -> None:
    _seed_runtime_from_history()
    server = ThreadingHTTPServer((host, port), CampusDemoHandler)
    print(f"Serving {REPO_ROOT} at http://{host}:{port}/campus_demo/console")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
    finally:
        server.server_close()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Campus demo server for A28 competition flow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List available videos for a dataset.")
    list_parser.add_argument("--dataset", default="ucf", choices=available_datasets())

    build_parser = subparsers.add_parser("build-report", help="Build a report for a specific video.")
    build_parser.add_argument("--dataset", default="ucf", choices=available_datasets())
    build_parser.add_argument("--video", required=True, help="Video name, with or without .mp4")

    samples_parser = subparsers.add_parser("build-samples", help="Build reports for built-in sample videos.")
    samples_parser.add_argument("--dataset", default="ucf", choices=available_datasets())

    serve_parser = subparsers.add_parser("serve", help="Serve the repo root and campus control console.")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", default=8000, type=int)

    return parser


def main() -> None:
    parser = make_parser()
    args = parser.parse_args()

    if args.command == "list":
        command_list(args.dataset)
    elif args.command == "build-report":
        report_dir = build_report(args.dataset, args.video)
        print(report_dir)
    elif args.command == "build-samples":
        command_build_samples(args.dataset)
    elif args.command == "serve":
        command_serve(args.host, args.port)
    else:
        parser.error(f"Unsupported command {args.command}")


if __name__ == "__main__":
    main()
