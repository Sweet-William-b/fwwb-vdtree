from __future__ import annotations

import csv
import json
import math
import os
import shutil
import subprocess
import zipfile
from datetime import datetime
from html import escape
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import quote

import cv2

from .config import DATASETS, OUTPUT_ROOT, REPORTS_ROOT, UPLOADS_ROOT


def sanitize_for_json(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_for_json(item) for item in value]
    return value


def ensure_output_root(output_root: Path = OUTPUT_ROOT) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "reports").mkdir(parents=True, exist_ok=True)
    (output_root / "history").mkdir(parents=True, exist_ok=True)


def slugify(value: str) -> str:
    cleaned = []
    for char in value:
        if char.isalnum():
            cleaned.append(char.lower())
        else:
            cleaned.append("-")
    slug = "".join(cleaned).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "report"


def report_directory(dataset_name: str, video_stem: str, output_root: Path = OUTPUT_ROOT) -> Path:
    return output_root / "reports" / f"{dataset_name}-{slugify(video_stem)}"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(sanitize_for_json(payload), handle, ensure_ascii=False, indent=2)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        headers = [
            "event_id",
            "behavior_type",
            "campus_label",
            "risk_level",
            "review_status",
            "note",
            "track_ids",
            "start_sec",
            "end_sec",
            "confidence",
            "peak_score",
            "reason_text",
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
        return

    headers = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: sanitize_for_json(value) for key, value in row.items()})


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_report_events(report_dir: Path, prefer_current: bool = True) -> list[dict[str, Any]]:
    if prefer_current and (report_dir / "events.current.json").exists():
        return read_json(report_dir / "events.current.json")
    return read_json(report_dir / "events.json")


def write_event_exports(report_dir: Path, events: list[dict[str, Any]], current: bool = False) -> None:
    suffix = ".current" if current else ""
    json_path = report_dir / f"events{suffix}.json"
    csv_path = report_dir / f"events{suffix}.csv"
    write_json(json_path, events)
    write_csv(
        csv_path,
        [
            {
                "event_id": event.get("event_id"),
                "behavior_type": event.get("behavior_type") or event.get("campus_label"),
                "campus_label": event.get("campus_label"),
                "risk_level": event.get("risk_level"),
                "review_status": event.get("review_status"),
                "note": event.get("note"),
                "track_ids": ",".join(str(item) for item in event.get("track_ids", [])),
                "start_sec": event.get("start_sec"),
                "end_sec": event.get("end_sec"),
                "confidence": event.get("confidence", event.get("peak_score")),
                "peak_score": event.get("peak_score"),
                "mean_score": event.get("mean_score"),
                "reason_text": event.get("reason_text"),
                "caption_interval": event.get("caption_interval"),
                "reason_interval": event.get("reason_interval"),
            }
            for event in events
        ],
    )


def write_current_analysis(report_dir: Path, analysis: dict[str, Any], events: list[dict[str, Any]]) -> None:
    current_analysis = sanitize_for_json(dict(analysis))
    current_analysis["events"] = sanitize_for_json(events)
    current_analysis.setdefault("summary", {})
    current_analysis["summary"]["current_event_count"] = len(events)
    current_analysis["summary"]["last_edited_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(report_dir / "analysis.current.json", current_analysis)


def build_report_bundle(report_dir: Path) -> Path:
    bundle_path = report_dir / "report_bundle.zip"
    include_names = [
        "index.html",
        "analysis.json",
        "analysis.current.json",
        "events.json",
        "events.current.json",
        "events.csv",
        "events.current.csv",
        "clips_manifest.json",
        "score.svg",
    ]
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in include_names:
            path = report_dir / name
            if path.exists():
                archive.write(path, arcname=name)
        clips_dir = report_dir / "clips"
        if clips_dir.exists():
            for clip in sorted(clips_dir.glob("*")):
                if clip.is_file():
                    archive.write(clip, arcname=f"clips/{clip.name}")
    return bundle_path


def _downsample_scores(scores: list[float], max_points: int = 900) -> list[tuple[int, float]]:
    if len(scores) <= max_points:
        return list(enumerate(scores))
    bucket_size = len(scores) / max_points
    points: list[tuple[int, float]] = []
    start = 0.0
    while int(start) < len(scores):
        end = min(len(scores), int(start + bucket_size))
        segment = scores[int(start) : max(int(start) + 1, end)]
        if not segment:
            break
        segment_peak = max(segment)
        segment_index = int(start) + segment.index(segment_peak)
        points.append((segment_index, segment_peak))
        start += bucket_size
    if points[-1][0] != len(scores) - 1:
        points.append((len(scores) - 1, scores[-1]))
    return points


def build_score_svg(
    scores: list[float],
    events: list[dict[str, Any]],
    threshold: float,
    high_threshold: float,
    width: int = 1200,
    height: int = 320,
) -> str:
    margin_left = 56
    margin_right = 16
    margin_top = 18
    margin_bottom = 40
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    background = "#fafafa"
    axis = "#777777"
    line_color = "#166534"

    def x_pos(frame_index: int) -> float:
        if len(scores) <= 1:
            return margin_left
        return margin_left + plot_width * (frame_index / (len(scores) - 1))

    def y_pos(score: float) -> float:
        bounded = min(1.0, max(0.0, score))
        return margin_top + plot_height * (1.0 - bounded)

    polyline = " ".join(
        f"{x_pos(frame_index):.2f},{y_pos(score):.2f}"
        for frame_index, score in _downsample_scores(scores)
    )

    event_rects = []
    for event in events:
        x = x_pos(event["start_frame"])
        end_x = x_pos(event["end_frame"])
        rect_width = max(2.0, end_x - x)
        color = "#fecaca" if event["risk_level"] == "high" else "#fde68a"
        event_rects.append(
            f'<rect x="{x:.2f}" y="{margin_top}" width="{rect_width:.2f}" '
            f'height="{plot_height}" fill="{color}" fill-opacity="0.35" />'
        )

    threshold_y = y_pos(threshold)
    high_threshold_y = y_pos(high_threshold)
    x_ticks = []
    if scores:
        for step in range(6):
            frame_index = int((len(scores) - 1) * step / 5)
            x = x_pos(frame_index)
            x_ticks.append(
                f'<line x1="{x:.2f}" y1="{margin_top + plot_height}" x2="{x:.2f}" '
                f'y2="{margin_top + plot_height + 6}" stroke="{axis}" />'
                f'<text x="{x:.2f}" y="{height - 12}" text-anchor="middle" '
                f'font-size="12" fill="{axis}">{frame_index}</text>'
            )

    y_ticks = []
    for tick in [0.0, 0.25, 0.5, 0.75, 1.0]:
        y = y_pos(tick)
        y_ticks.append(
            f'<line x1="{margin_left - 6}" y1="{y:.2f}" x2="{margin_left}" y2="{y:.2f}" stroke="{axis}" />'
            f'<text x="{margin_left - 10}" y="{y + 4:.2f}" text-anchor="end" '
            f'font-size="12" fill="{axis}">{tick:.2f}</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="{background}" />
  <text x="{margin_left}" y="14" font-size="14" fill="#111827">Anomaly Score Timeline</text>
  {''.join(event_rects)}
  <line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_height}" stroke="{axis}" />
  <line x1="{margin_left}" y1="{margin_top + plot_height}" x2="{margin_left + plot_width}" y2="{margin_top + plot_height}" stroke="{axis}" />
  <line x1="{margin_left}" y1="{threshold_y:.2f}" x2="{margin_left + plot_width}" y2="{threshold_y:.2f}" stroke="#b45309" stroke-dasharray="5 5" />
  <line x1="{margin_left}" y1="{high_threshold_y:.2f}" x2="{margin_left + plot_width}" y2="{high_threshold_y:.2f}" stroke="#b91c1c" stroke-dasharray="4 4" />
  <text x="{margin_left + plot_width - 4}" y="{threshold_y - 6:.2f}" text-anchor="end" font-size="12" fill="#b45309">alert {threshold:.2f}</text>
  <text x="{margin_left + plot_width - 4}" y="{high_threshold_y - 6:.2f}" text-anchor="end" font-size="12" fill="#b91c1c">high {high_threshold:.2f}</text>
  <polyline fill="none" stroke="{line_color}" stroke-width="2" points="{polyline}" />
  {''.join(x_ticks)}
  {''.join(y_ticks)}
</svg>"""


def _quoted_relative_path(target: Path, start: Path) -> str:
    relative = os.path.relpath(target, start)
    return quote(relative.replace(os.sep, "/"), safe="/#")


def _portable_basename(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return PureWindowsPath(raw).name if "\\" in raw else Path(raw).name


def _resolve_report_video_path(summary: dict[str, Any]) -> Path | None:
    raw_path = summary.get("video_path")
    if raw_path:
        candidate = Path(str(raw_path))
        if candidate.exists():
            return candidate

    dataset_name = str(summary.get("dataset_name") or "")
    candidate_names = [
        summary.get("video_name"),
        summary.get("source_label"),
        _portable_basename(raw_path),
    ]
    dataset = DATASETS.get(dataset_name)
    if dataset and dataset.video_root:
        for name in candidate_names:
            basename = _portable_basename(name)
            if not basename:
                continue
            candidate = dataset.video_root / basename
            if candidate.exists():
                return candidate

    if UPLOADS_ROOT.exists():
        for name in candidate_names:
            basename = _portable_basename(name)
            if not basename:
                continue
            direct = UPLOADS_ROOT / basename
            if direct.exists():
                return direct
            matches = sorted(UPLOADS_ROOT.glob(f"*{basename}"))
            if matches:
                return matches[0]

    return None


def _export_clip_with_opencv(video_path: Path, clip_path: Path, start_sec: float, end_sec: float) -> tuple[bool, str | None]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return False, "opencv_open_failed"

    fps = capture.get(cv2.CAP_PROP_FPS)
    if not fps or not math.isfinite(fps) or fps <= 0:
        fps = 25.0

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        capture.release()
        return False, "opencv_invalid_frame_size"

    start_frame = max(0, int(math.floor(start_sec * fps)))
    end_frame = max(start_frame + 1, int(math.ceil(end_sec * fps)))
    capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(clip_path), fourcc, fps, (width, height))
    if not writer.isOpened():
        capture.release()
        return False, "opencv_writer_failed"

    written_frames = 0
    frame_index = start_frame
    try:
        while frame_index < end_frame:
            ok, frame = capture.read()
            if not ok:
                break
            writer.write(frame)
            written_frames += 1
            frame_index += 1
    finally:
        writer.release()
        capture.release()

    if written_frames <= 0:
        if clip_path.exists():
            clip_path.unlink()
        return False, "opencv_empty_clip"
    return True, None


def export_clips(
    report_dir: Path,
    video_path: Path | None,
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    clips_dir = report_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    ffmpeg_path = shutil.which("ffmpeg")

    if video_path is None:
        status = "source_video_missing"
    elif ffmpeg_path is not None:
        status = "ffmpeg"
    else:
        status = "opencv"

    for event in events:
        clip_name = f"{event['event_id']}.mp4"
        clip_path = clips_dir / clip_name
        clip_entry = {
            "event_id": event["event_id"],
            "start_sec": event["start_sec"],
            "end_sec": event["end_sec"],
            "status": status,
            "clip_path": str(clip_path) if status in {"ffmpeg", "opencv"} else None,
        }
        if video_path is not None:
            clip_entry["source_video"] = str(video_path)
            clip_entry["fragment_href"] = _quoted_relative_path(video_path, report_dir) + f"#t={event['start_sec']},{event['end_sec']}"

        if status == "ffmpeg":
            command = [
                ffmpeg_path,
                "-y",
                "-ss",
                str(event["start_sec"]),
                "-to",
                str(event["end_sec"]),
                "-i",
                str(video_path),
                "-c",
                "copy",
                str(clip_path),
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                ok, error_code = _export_clip_with_opencv(video_path, clip_path, event["start_sec"], event["end_sec"])
                if ok:
                    clip_entry["status"] = "opencv"
                else:
                    clip_entry["status"] = "ffmpeg_failed"
                    clip_entry["stderr"] = (result.stderr or "")[-500:]
                    clip_entry["opencv_error"] = error_code
                    clip_entry["clip_path"] = None
        elif status == "opencv":
            ok, error_code = _export_clip_with_opencv(video_path, clip_path, event["start_sec"], event["end_sec"])
            if not ok:
                clip_entry["status"] = "opencv_failed"
                clip_entry["opencv_error"] = error_code
                clip_entry["clip_path"] = None
        manifest.append(clip_entry)

    write_json(report_dir / "clips_manifest.json", manifest)
    return manifest, status


def build_report_html(
    report_dir: Path,
    summary: dict[str, Any],
    events: list[dict[str, Any]],
    clip_manifest: list[dict[str, Any]],
    clip_mode: str,
) -> str:
    video_path = _resolve_report_video_path(summary)
    report_id = report_dir.name
    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clip_lookup_json = json.dumps(
        sanitize_for_json({item.get("event_id"): item for item in clip_manifest if item.get("event_id")}),
        ensure_ascii=False,
    )
    fallback_events_json = json.dumps(sanitize_for_json(events), ensure_ascii=False)
    downloads = [
        ("report_bundle.zip", "下载归档包"),
        ("events.current.csv", "下载事件表"),
        ("events.current.json", "下载结构化数据"),
        ("clips_manifest.json", "查看片段清单"),
        ("analysis.current.json", "当前分析结果"),
        ("events.json", "原始事件日志"),
        ("/campus_demo/console?view=history", "返回历史回看"),
        ("/campus_demo/console?view=events", "返回事件中心"),
    ]
    download_cards_parts = []
    for href, label in downloads:
        target_attr = ' target="_blank"' if href.endswith(".zip") or href.endswith(".html") or href.startswith("/") else ""
        display_name = Path(href).name if not href.startswith("/") else href.split("/")[-1] or href
        download_cards_parts.append(
            f'<a class="download-card" href="{escape(href)}"{target_attr}>'
            f"<span>{escape(label)}</span><strong>打开 / 下载</strong><em>{escape(display_name)}</em></a>"
        )
    download_cards = "".join(download_cards_parts)
    video_html = '<div class="placeholder">当前数据集没有可直接回放的本地源视频，但日志、时间线、导出与复核仍可完整展示。</div>'
    if video_path:
        rel_video = _quoted_relative_path(video_path, report_dir)
        video_html = (
            f'<video id="main-video" controls preload="metadata" src="{rel_video}" width="100%"></video>'
            '<p class="note">点击日志中的时间按钮可跳转对应起点；没有切片时也可使用 jump 链接打开原视频时间段。</p>'
        )

    high_count = sum(1 for event in events if str(event.get("risk_level") or "") == "high")
    pending_count = sum(1 for event in events if str(event.get("review_status") or "pending") == "pending")
    dataset_display = str(summary.get("dataset_display_name") or summary.get("dataset_name") or "未知数据集")
    source_class = str(summary.get("source_class") or "未知来源")
    campus_label = str(summary.get("campus_label") or "待研判")
    headline = str(summary.get("headline") or "当前报告已生成，请结合视频片段和事件表进行人工复核。")
    video_name = str(summary.get("video_name") or report_id)
    event_count = int(summary.get("event_count") or len(events))
    peak_score = float(summary.get("peak_score") or 0)
    mean_score = float(summary.get("mean_score") or 0)
    fps = float(summary.get("fps") or 0)
    threshold = float(summary.get("threshold") or 0)
    high_threshold = float(summary.get("high_threshold") or 0)
    roc_auc = summary.get("roc_auc", "暂无")
    pr_auc = summary.get("pr_auc", "暂无")
    pos_mean = summary.get("pos_mean", "暂无")
    neg_mean = summary.get("neg_mean", "暂无")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(video_name)} - 校园安防报告</title>
  <style>
    :root {{
      --font-ui: "PingFang SC", "STHeiti Light", "Noto Sans SC", "HarmonyOS Sans SC", "Source Han Sans SC", "Microsoft YaHei UI", sans-serif;
      --font-display: "Songti SC", "Noto Serif SC", "STSong", "SimSun", serif;
      --font-latin: "Geist", "Satoshi", "DIN Alternate", "Arial", sans-serif;
      --bg: #f5f8f7;
      --panel: rgba(255, 255, 255, 0.88);
      --panel-soft: #f8fbfa;
      --line: rgba(22, 41, 37, 0.12);
      --line-strong: rgba(31, 117, 107, 0.28);
      --ink: #17211e;
      --muted: #66736f;
      --subtle: #8b9893;
      --accent: #1f756b;
      --accent-deep: #15584f;
      --review: #3c6094;
      --warn: #a56c0d;
      --danger: #b24034;
      --low: #2b7a54;
      --shadow: 0 18px 42px rgba(28, 50, 45, 0.055);
      --radius: 8px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: var(--font-ui);
      font-weight: 300;
      letter-spacing: 0;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(31, 117, 107, 0.04) 0 1px, transparent 1px 100%) 0 0 / 56px 56px,
        linear-gradient(180deg, rgba(22, 41, 37, 0.03) 0 1px, transparent 1px 100%) 0 0 / 56px 56px,
        linear-gradient(180deg, #fbfcfb 0%, var(--bg) 100%);
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    button, input, select, textarea {{ font: inherit; }}
    a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {{
      outline: 2px solid rgba(31, 117, 107, 0.34);
      outline-offset: 2px;
    }}
    .page {{ max-width: 1480px; margin: 0 auto; padding: 16px 18px 40px; }}
    .topbar {{
      position: sticky;
      top: 14px;
      z-index: 20;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 14px;
      padding: 10px;
      border: 1px solid rgba(22, 41, 37, 0.08);
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.88);
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
    }}
    .brand {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 0 8px;
      color: var(--ink);
      font-family: var(--font-ui);
      font-weight: 520;
    }}
    .brand-mark {{
      display: inline-grid;
      width: 30px;
      height: 30px;
      place-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--accent);
      background: rgba(31, 117, 107, 0.06);
      font-size: 13px;
      font-weight: 900;
    }}
    .brand-mark img {{
      width: 22px;
      height: 22px;
      object-fit: contain;
    }}
    .top-actions {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    .top-actions a, .download-card, .jump, .toggle button, .toolbar button {{
      transition: transform 0.18s ease, background 0.18s ease, border-color 0.18s ease;
    }}
    .top-actions a {{
      display: inline-flex;
      min-height: 36px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 12px;
      color: var(--muted);
      background: rgba(255, 255, 255, 0.72);
      font-size: 13px;
      font-weight: 420;
    }}
    .top-actions a:hover,
    .download-card:hover,
    .jump:hover,
    .toolbar button:hover {{
      transform: translateY(-1px);
      border-color: var(--line-strong);
    }}
    .hero, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }}
    .hero {{ padding: 16px; margin-bottom: 14px; }}
    .hero-top {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      align-items: end;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      color: var(--accent);
      font-size: 12px;
      font-family: var(--font-latin), var(--font-ui);
      font-weight: 760;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }}
    .eyebrow::before {{
      content: "";
      width: 28px;
      height: 1px;
      background: currentColor;
    }}
    .hero h1 {{
      margin: 10px 0 8px;
      font-family: var(--font-display);
      font-size: clamp(26px, 3vw, 38px);
      font-weight: 300;
      line-height: 1.12;
      letter-spacing: 0;
    }}
    .lead, .note, .status {{
      color: var(--muted);
      line-height: 1.8;
      font-size: 14px;
    }}
    .hero-chips {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 18px;
    }}
    .hero-chip {{
      display: inline-flex;
      align-items: center;
      min-height: 32px;
      padding: 0 11px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.76);
      border: 1px solid var(--line);
      font-size: 13px;
      font-weight: 520;
    }}
    .summary-stack {{
      display: grid;
      grid-template-columns: repeat(3, minmax(118px, 1fr));
      gap: 8px;
    }}
    .summary-card, .stat, .download-card, .metric-line {{
      padding: 13px;
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.8);
      border: 1px solid var(--line);
    }}
    .summary-card span, .stat .label, .download-card span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-family: var(--font-latin), var(--font-ui);
      font-weight: 680;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }}
    .summary-card strong, .stat .value {{
      display: block;
      margin-top: 8px;
      font-family: var(--font-latin), var(--font-ui);
      font-size: 24px;
      font-weight: 520;
    }}
    .summary-card p {{ margin: 6px 0 0; color: var(--muted); line-height: 1.55; font-size: 12px; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-top: 14px;
    }}
    .report-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1.55fr) minmax(330px, 0.55fr);
      gap: 14px;
      align-items: start;
    }}
    .side-stack {{
      display: grid;
      gap: 14px;
    }}
    .panel {{ padding: 16px; margin-bottom: 14px; }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: start;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }}
    .panel h2 {{
      margin: 7px 0 0;
      font-family: var(--font-display);
      font-size: 24px;
      font-weight: 300;
      line-height: 1.14;
    }}
    .video-shell video {{
      width: 100%;
      border-radius: var(--radius);
      background: #111;
    }}
    .placeholder {{
      min-height: 480px;
      display: grid;
      place-items: center;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 24px;
      border-radius: var(--radius);
      color: var(--muted);
      line-height: 1.8;
      background:
        linear-gradient(90deg, rgba(31, 117, 107, 0.045) 0 1px, transparent 1px 100%) 0 0 / 44px 44px,
        linear-gradient(180deg, rgba(22, 41, 37, 0.04) 0 1px, transparent 1px 100%) 0 0 / 44px 44px,
        #fbfdfc;
      border: 1px dashed rgba(31, 117, 107, 0.18);
    }}
    .placeholder::before {{
      content: "";
      display: block;
      width: min(46vw, 420px);
      height: min(28vw, 260px);
      border: 1px solid rgba(31, 117, 107, 0.16);
      border-radius: 8px;
      background:
        linear-gradient(90deg, transparent 49%, rgba(31, 117, 107, 0.14) 50%, transparent 51%),
        linear-gradient(180deg, transparent 49%, rgba(31, 117, 107, 0.14) 50%, transparent 51%);
      opacity: 0.42;
      margin-bottom: 18px;
    }}
    .side-stack .panel {{
      margin-bottom: 0;
    }}
    .response-list {{
      display: grid;
      gap: 8px;
    }}
    .response-item {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: center;
      padding: 12px 0;
      border-top: 1px solid rgba(22, 41, 37, 0.1);
    }}
    .response-item:first-child {{
      border-top: 0;
      padding-top: 0;
    }}
    .response-item span {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 420;
    }}
    .response-item strong {{
      font-family: var(--font-latin), var(--font-ui);
      font-size: 22px;
      font-weight: 520;
      line-height: 1;
    }}
    .download-grid {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }}
    .download-card strong {{
      display: block;
      margin-top: 6px;
      color: var(--ink);
      font-size: 14px;
    }}
    .download-card em {{
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
      font-style: normal;
      word-break: break-all;
    }}
    .metric-lines {{
      display: grid;
      gap: 10px;
    }}
    .toolbar {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 14px;
    }}
    .toggle {{
      display: inline-flex;
      gap: 8px;
      padding: 5px;
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid var(--line);
    }}
    .toggle button,
    .toolbar button {{
      border: 1px solid transparent;
      border-radius: 8px;
      min-height: 38px;
      padding: 0 16px;
      font-family: var(--font-ui);
      font-weight: 420;
      cursor: pointer;
    }}
    .toggle button {{
      background: transparent;
      color: var(--muted);
      min-width: 92px;
    }}
    .toggle button.active {{
      background: var(--accent);
      color: #fff;
    }}
    .toolbar .primary {{
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }}
    .toolbar .secondary {{
      border-color: rgba(31, 117, 107, 0.28);
      background: rgba(31, 117, 107, 0.08);
      color: var(--accent);
    }}
    .table-shell {{
      overflow: auto;
      border-radius: var(--radius);
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.82);
    }}
    .review-list {{
      display: grid;
      gap: 10px;
    }}
    .review-event {{
      display: grid;
      grid-template-columns: 190px minmax(0, 1fr);
      gap: 14px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.78);
    }}
    .event-time {{
      display: grid;
      gap: 10px;
      align-content: start;
    }}
    .event-fields {{
      display: grid;
      grid-template-columns: minmax(180px, 0.9fr) minmax(140px, 0.55fr) minmax(140px, 0.55fr) minmax(140px, 0.55fr);
      gap: 10px;
      align-items: start;
    }}
    .field {{
      display: grid;
      gap: 6px;
    }}
    .field label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 420;
    }}
    .wide-field {{
      grid-column: span 2;
    }}
    .reason-field {{
      grid-column: 1 / -1;
    }}
    .event-meta-line {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .clip-link {{
      display: inline-flex;
      min-height: 34px;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 12px;
      color: var(--accent);
      background: rgba(31, 117, 107, 0.06);
      font-size: 13px;
      font-weight: 420;
    }}
    table {{ width: 100%; border-collapse: collapse; min-width: 1100px; }}
    th, td {{
      padding: 14px 12px;
      border-top: 1px solid rgba(65, 53, 40, 0.1);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-family: var(--font-latin), var(--font-ui);
      font-weight: 680;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }}
    .jump {{
      border: 1px solid rgba(31, 117, 107, 0.22);
      border-radius: 8px;
      padding: 10px 12px;
      background: rgba(29, 90, 74, 0.12);
      color: var(--accent);
      font-weight: 420;
      cursor: pointer;
    }}
    input[type="text"], textarea, select {{
      width: 100%;
      box-sizing: border-box;
      border: 1px solid rgba(22, 41, 37, 0.14);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      padding: 12px 14px;
    }}
    textarea {{ min-height: 88px; resize: vertical; }}
    .event-title {{ font-weight: 420; }}
    .event-tags {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 8px;
    }}
    .event-tag {{
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 0 10px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.9);
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      font-weight: 420;
    }}
    .event-tag.edited {{
      color: var(--accent);
      border-color: rgba(29, 90, 74, 0.22);
      background: rgba(29, 90, 74, 0.08);
    }}
    .risk-high, .review-false_positive {{ color: var(--danger); font-weight: 520; }}
    .risk-medium {{ color: var(--warn); font-weight: 520; }}
    .risk-review, .review-pending {{ color: var(--review); font-weight: 520; }}
    .risk-low, .review-confirmed {{ color: var(--low); font-weight: 520; }}
    @media (max-width: 960px) {{
      .topbar {{ position: static; display: grid; }}
      .hero-top, .report-layout {{ grid-template-columns: 1fr; }}
      .stats, .download-grid, .summary-stack {{ grid-template-columns: 1fr; }}
      .placeholder {{ min-height: 320px; }}
      .review-event, .event-fields {{ grid-template-columns: 1fr; }}
      .wide-field, .reason-field {{ grid-column: auto; }}
    }}
  </style>
</head>
<body data-report-id="{escape(report_id)}">
  <div class="page">
    <header class="topbar">
      <a class="brand" href="/campus_demo/console?view=history">
        <span class="brand-mark"><img src="/campus_demo/assets/dingxin-vision-logo.svg" alt="" /></span>
        <span>鼎新智眼</span>
      </a>
      <nav class="top-actions" aria-label="报告操作">
        <a href="/campus_demo/console?view=history">历史回看</a>
        <a href="/campus_demo/console?view=events">事件中心</a>
        <a href="report_bundle.zip" target="_blank">下载归档包</a>
      </nav>
    </header>
    <section class="hero">
      <div class="hero-top">
        <div>
          <div class="eyebrow">Campus Security Report</div>
          <h1>{escape(video_name)}</h1>
          <p class="lead">{escape(headline)}</p>
          <div class="hero-chips">
            <span class="hero-chip">{escape(dataset_display)}</span>
            <span class="hero-chip">{escape(source_class)}</span>
            <span class="hero-chip">{escape(campus_label)}</span>
            <span class="hero-chip">报告生成 {report_time}</span>
          </div>
        </div>
        <div class="stats">
          <div class="stat"><div class="label">当前事件</div><div class="value" id="summary-current-events">{event_count}</div></div>
          <div class="stat"><div class="label">高风险</div><div class="value" id="summary-high-events">{high_count}</div></div>
          <div class="stat"><div class="label">待复核</div><div class="value" id="summary-review-events">{pending_count}</div></div>
          <div class="stat"><div class="label">最高分数</div><div class="value">{peak_score:.3f}</div></div>
        </div>
      </div>
    </section>

    <div class="report-layout">
      <div>
        <section class="panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">Playback</div>
              <h2>视频核验</h2>
            </div>
            <div class="note">点击事件时间可跳到对应片段，用于人工复核。</div>
          </div>
          <div class="video-shell">{video_html}</div>
        </section>
        <section class="panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">Timeline</div>
              <h2>异常分数</h2>
            </div>
            <div class="note">高亮区域是系统识别出的异常片段。</div>
          </div>
          <img src="score.svg" alt="score timeline" style="width:100%;height:auto" />
          <p class="note">切片导出模式：<strong>{escape(clip_mode)}</strong>。若没有直接 mp4 切片，可使用 jump 链接打开原视频时间段。</p>
        </section>
      </div>
      <div class="side-stack">
        <section class="panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">First Response</div>
              <h2>处置摘要</h2>
            </div>
          </div>
          <div class="response-list">
            <div class="response-item"><span>数据集</span><strong>{escape(dataset_display)}</strong></div>
            <div class="response-item"><span>校园标签</span><strong>{escape(campus_label)}</strong></div>
            <div class="response-item"><span>平均分数</span><strong>{mean_score:.3f}</strong></div>
            <div class="response-item"><span>视频 FPS</span><strong>{fps:.2f}</strong></div>
          </div>
        </section>
        <section class="panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">Archive</div>
              <h2>报告归档</h2>
            </div>
            <div class="note">下载归档包、事件表和结构化数据。</div>
          </div>
          <div class="download-grid">{download_cards}</div>
        </section>
        <section class="panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">Assessment</div>
              <h2>研判摘要</h2>
            </div>
            <div class="note">用于判断当前结果是否需要继续复核。</div>
          </div>
          <div class="metric-lines">
            <div class="metric-line">当前校园标签：<strong>{escape(campus_label)}</strong><br />自动阈值 {threshold:.3f}，高风险阈值 {high_threshold:.3f}，均值分数 {mean_score:.3f}</div>
            <div class="metric-line">ROC AUC：{roc_auc} · PR AUC：{pr_auc}<br />正样本均值：{pos_mean} · 负样本均值：{neg_mean}</div>
            <div class="metric-line">当前版可继续保存修订，归档包会同步更新当前事件表与报告数据。</div>
          </div>
        </section>
      </div>
    </div>

    <section class="panel">
      <div class="panel-head">
        <div>
          <div class="eyebrow">Review Table</div>
          <h2>事件复核</h2>
        </div>
        <div class="note">当前版可编辑，原始版只读。复杂处理建议返回事件中心。</div>
      </div>
      <div class="toolbar">
        <div class="toggle">
          <button id="version-current" class="active">当前版</button>
          <button id="version-original">原始版</button>
        </div>
        <button id="save-events" class="primary">保存复核结果</button>
        <button id="reload-events" class="secondary">重新加载</button>
        <span id="save-status" class="status">在线修改功能仅在 `python3 campus_demo/app.py serve` 启动后可用。</span>
      </div>
      <div class="review-list" id="event-review-list"></div>
    </section>
  </div>
  <script>
    const clipLookup = {clip_lookup_json};
    const fallbackEvents = {fallback_events_json};
    const reportId = document.body.dataset.reportId;
    const eventReviewList = document.getElementById('event-review-list');
    const saveStatus = document.getElementById('save-status');
    const state = {{
      version: 'current',
      currentEvents: fallbackEvents,
      originalEvents: fallbackEvents,
    }};

    function escapeHtml(value) {{
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }}

    function formatSeconds(value) {{
      return `${{Number(value || 0).toFixed(2)}}s`;
    }}

    function normalizeTrackIds(value) {{
      if (Array.isArray(value)) {{
        return value.map((item) => String(item || '').trim()).filter(Boolean);
      }}
      return String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
    }}

    function normalizeRisk(value) {{
      return ['low', 'review', 'medium', 'high'].includes(String(value || '').trim()) ? String(value).trim() : 'review';
    }}

    function normalizeReview(value) {{
      return ['pending', 'confirmed', 'false_positive'].includes(String(value || '').trim()) ? String(value).trim() : 'pending';
    }}

    function riskClass(level) {{
      return `risk-${{normalizeRisk(level)}}`;
    }}

    function reviewClass(level) {{
      return `review-${{normalizeReview(level)}}`;
    }}

    function clipCell(eventId) {{
      const clipEntry = clipLookup[eventId] || {{}};
      if (clipEntry.clip_path) {{
        const fileName = String(clipEntry.clip_path).split('/').pop();
        return `<a class="clip-link" href="clips/${{escapeHtml(fileName)}}">查看切片</a>`;
      }}
      if (clipEntry.fragment_href) {{
        return `<a class="clip-link" href="${{escapeHtml(clipEntry.fragment_href)}}">跳转原视频</a>`;
      }}
      return '-';
    }}

    function updateSummary(events) {{
      const highCount = events.filter((event) => normalizeRisk(event.risk_level) === 'high').length;
      const pendingCount = events.filter((event) => normalizeReview(event.review_status) === 'pending').length;
      document.getElementById('summary-current-events').textContent = String(events.length);
      document.getElementById('summary-high-events').textContent = String(highCount);
      document.getElementById('summary-review-events').textContent = String(pendingCount);
    }}

    function renderEvents(events) {{
      updateSummary(events);
      if (!events.length) {{
        eventReviewList.innerHTML = '<div class="placeholder" style="min-height:220px;">当前视频没有可展示的事件片段。</div>';
        return;
      }}
      const editable = state.version === 'current';
      eventReviewList.innerHTML = events.map((event, index) => `
        <article class="review-event" data-event-id="${{escapeHtml(event.event_id)}}">
          <div class="event-time">
            <button class="jump" data-start="${{event.start_sec}}">
              ${{formatSeconds(event.start_sec)}} - ${{formatSeconds(event.end_sec)}}
            </button>
            <div class="event-meta-line">
              <span class="event-tag">事件 ${{String(index + 1).padStart(2, '0')}}</span>
              <span class="event-tag">ID ${{escapeHtml(event.event_id)}}</span>
              ${{event.last_edited_at ? '<span class="event-tag edited">已编辑</span>' : ''}}
            </div>
            ${{clipCell(event.event_id)}}
          </div>
          <div class="event-fields">
            <div class="field">
              <label>行为标签</label>
              <input type="text" name="behavior_type" value="${{escapeHtml(event.behavior_type || event.campus_label || event.source_class || '')}}" ${{editable ? '' : 'disabled'}} />
            </div>
            <div class="field">
              <label>风险等级</label>
              <select name="risk_level" ${{editable ? '' : 'disabled'}}>
                <option value="low" ${{normalizeRisk(event.risk_level) === 'low' ? 'selected' : ''}}>low</option>
                <option value="review" ${{normalizeRisk(event.risk_level) === 'review' ? 'selected' : ''}}>review</option>
                <option value="medium" ${{normalizeRisk(event.risk_level) === 'medium' ? 'selected' : ''}}>medium</option>
                <option value="high" ${{normalizeRisk(event.risk_level) === 'high' ? 'selected' : ''}}>high</option>
              </select>
              <div class="${{riskClass(event.risk_level)}}">${{escapeHtml(normalizeRisk(event.risk_level))}}</div>
            </div>
            <div class="field">
              <label>复核状态</label>
              <select name="review_status" ${{editable ? '' : 'disabled'}}>
                <option value="pending" ${{normalizeReview(event.review_status) === 'pending' ? 'selected' : ''}}>pending</option>
                <option value="confirmed" ${{normalizeReview(event.review_status) === 'confirmed' ? 'selected' : ''}}>confirmed</option>
                <option value="false_positive" ${{normalizeReview(event.review_status) === 'false_positive' ? 'selected' : ''}}>false_positive</option>
              </select>
              <div class="${{reviewClass(event.review_status)}}">${{escapeHtml(normalizeReview(event.review_status))}}</div>
            </div>
            <div class="field">
              <label>Track</label>
              <input type="text" name="track_ids" value="${{escapeHtml(normalizeTrackIds(event.track_ids).join(', '))}}" ${{editable ? '' : 'disabled'}} />
            </div>
            <div class="field wide-field">
              <label>复核备注</label>
              <textarea name="note" ${{editable ? '' : 'disabled'}}>${{escapeHtml(event.note || '')}}</textarea>
            </div>
            <div class="field wide-field">
              <label>系统原因</label>
              <textarea name="reason_text" ${{editable ? '' : 'disabled'}}>${{escapeHtml(event.reason_text || '')}}</textarea>
            </div>
          </div>
        </article>
      `).join('');

      document.querySelectorAll('.jump').forEach((button) => {{
        button.addEventListener('click', () => {{
          const video = document.getElementById('main-video');
          if (!video) return;
          video.currentTime = Number(button.dataset.start || 0);
          video.play();
        }});
      }});
    }}

    async function reloadEventVersions() {{
      try {{
        const [currentResponse, originalResponse] = await Promise.all([
          fetch(`events.current.json?ts=${{Date.now()}}`),
          fetch(`events.json?ts=${{Date.now()}}`),
        ]);
        if (currentResponse.ok) {{
          state.currentEvents = await currentResponse.json();
        }}
        if (originalResponse.ok) {{
          state.originalEvents = await originalResponse.json();
        }}
      }} catch (_error) {{
        state.currentEvents = fallbackEvents;
        state.originalEvents = fallbackEvents;
      }}
    }}

    function updateVersionUI() {{
      document.getElementById('version-current').classList.toggle('active', state.version === 'current');
      document.getElementById('version-original').classList.toggle('active', state.version === 'original');
      saveStatus.textContent = state.version === 'current'
        ? '当前展示的是可编辑的复核版日志。'
        : '当前展示的是只读的原始版日志。';
    }}

    async function loadEvents() {{
      await reloadEventVersions();
      updateVersionUI();
      renderEvents(state.version === 'original' ? state.originalEvents : state.currentEvents);
    }}

    function collectEditedEvents() {{
      return Array.from(document.querySelectorAll('#event-review-list [data-event-id]')).map((row) => {{
        const eventId = row.dataset.eventId;
        const original = state.currentEvents.find((item) => item.event_id === eventId) || {{}};
        return {{
          ...original,
          event_id: eventId,
          behavior_type: row.querySelector('[name="behavior_type"]').value.trim(),
          risk_level: row.querySelector('[name="risk_level"]').value,
          review_status: row.querySelector('[name="review_status"]').value,
          track_ids: row.querySelector('[name="track_ids"]').value.trim(),
          note: row.querySelector('[name="note"]').value.trim(),
          reason_text: row.querySelector('[name="reason_text"]').value.trim(),
        }};
      }});
    }}

    async function saveEvents() {{
      if (state.version !== 'current') {{
        saveStatus.textContent = '原始版日志不可编辑，请切回“当前版”。';
        return;
      }}
      const payload = {{
        report_id: reportId,
        events: collectEditedEvents(),
      }};
      saveStatus.textContent = '正在保存...';
      try {{
        const response = await fetch('/campus_demo/api/save-events', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload),
        }});
        const result = await response.json();
        if (!response.ok || !result.ok) {{
          throw new Error(result.error || 'save failed');
        }}
        saveStatus.textContent = '日志修改已保存，并已更新 current 导出文件与 zip 包。';
        await loadEvents();
      }} catch (error) {{
        saveStatus.textContent = `保存失败：${{error.message}}`;
      }}
    }}

    document.getElementById('version-current').addEventListener('click', async () => {{
      state.version = 'current';
      await loadEvents();
    }});
    document.getElementById('version-original').addEventListener('click', async () => {{
      state.version = 'original';
      await loadEvents();
    }});
    document.getElementById('save-events').addEventListener('click', saveEvents);
    document.getElementById('reload-events').addEventListener('click', loadEvents);
    loadEvents();
  </script>
</body>
</html>"""


def build_history_index(output_root: Path, history_records: list[dict[str, Any]]) -> None:
    ensure_output_root(output_root)
    latest_by_report: dict[str, dict[str, Any]] = {}
    for record in history_records:
        latest_by_report[record["report_href"]] = record

    rows = []
    ordered_records = sorted(
        latest_by_report.values(),
        key=lambda item: item.get("generated_at", ""),
        reverse=True,
    )
    for record in ordered_records:
        rows.append(
            "<tr>"
            f'<td><a href="{escape(record["report_href"])}">{escape(record["video_name"])}</a></td>'
            f'<td>{escape(record["dataset_display_name"])}</td>'
            f'<td>{escape(record["source_class"])}</td>'
            f'<td>{escape(record["campus_label"])}</td>'
            f'<td>{record["event_count"]}</td>'
            f'<td>{record["peak_score"]:.3f}</td>'
            f'<td>{escape(record["generated_at"])}</td>'
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="7">当前还没有生成任何报告。</td></tr>')

    dataset_count = len({record.get("dataset_display_name") for record in ordered_records if record.get("dataset_display_name")})
    latest_time = ordered_records[0].get("generated_at") if ordered_records else "暂无"

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Campus Demo Reports</title>
  <style>
    :root {{
      --font-ui: "PingFang SC", "STHeiti Light", "Noto Sans SC", "HarmonyOS Sans SC", "Source Han Sans SC", "Microsoft YaHei UI", sans-serif;
      --font-display: "Songti SC", "Noto Serif SC", "STSong", "SimSun", serif;
      --font-latin: "Geist", "Satoshi", "DIN Alternate", "Arial", sans-serif;
      --bg: #f5f8f7;
      --panel: rgba(255, 255, 255, 0.88);
      --line: rgba(22, 41, 37, 0.12);
      --ink: #17211e;
      --muted: #66736f;
      --accent: #1f756b;
      --shadow: 0 18px 42px rgba(28, 50, 45, 0.055);
      --radius: 8px;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: var(--font-ui);
      font-weight: 300;
      letter-spacing: 0;
      background:
        linear-gradient(90deg, rgba(31, 117, 107, 0.04) 0 1px, transparent 1px 100%) 0 0 / 56px 56px,
        linear-gradient(180deg, rgba(22, 41, 37, 0.03) 0 1px, transparent 1px 100%) 0 0 / 56px 56px,
        linear-gradient(180deg, #fbfcfb 0%, var(--bg) 100%);
    }}
    .page {{
      max-width: 1480px;
      margin: 0 auto;
      padding: 16px 18px 40px;
    }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 14px;
      padding: 10px;
      border: 1px solid rgba(22, 41, 37, 0.08);
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.88);
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
    }}
    .brand {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 0 8px;
      color: var(--ink);
      font-family: var(--font-ui);
      font-weight: 520;
    }}
    .brand-mark {{
      display: inline-grid;
      width: 30px;
      height: 30px;
      place-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--accent);
      background: rgba(31, 117, 107, 0.06);
      font-size: 13px;
      font-weight: 900;
    }}
    .hero, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 18px;
      margin-bottom: 14px;
      backdrop-filter: blur(12px);
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      color: var(--accent);
      font-size: 12px;
      font-family: var(--font-latin), var(--font-ui);
      font-weight: 760;
      letter-spacing: 0.16em;
      text-transform: uppercase;
    }}
    .eyebrow::before {{
      content: "";
      width: 28px;
      height: 1px;
      background: currentColor;
    }}
    .hero h1 {{
      margin: 10px 0 8px;
      font-family: var(--font-display);
      font-size: clamp(26px, 3vw, 38px);
      font-weight: 300;
      line-height: 1.12;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.8;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .stat {{
      padding: 16px;
      border-radius: var(--radius);
      background: rgba(255, 255, 255, 0.8);
      border: 1px solid var(--line);
    }}
    .stat span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-family: var(--font-latin), var(--font-ui);
      font-weight: 680;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }}
    .stat strong {{
      display: block;
      margin-top: 10px;
      font-family: var(--font-latin), var(--font-ui);
      font-size: 28px;
      font-weight: 520;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
    }}
    th, td {{
      border-top: 1px solid rgba(65, 53, 40, 0.1);
      padding: 14px 12px;
      text-align: left;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-family: var(--font-latin), var(--font-ui);
      font-weight: 680;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 420;
    }}
    .links a {{
      display: inline-flex;
      align-items: center;
      min-height: 42px;
      margin-right: 12px;
      margin-top: 10px;
      padding: 0 14px;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.82);
      border: 1px solid var(--line);
    }}
    .table-shell {{
      overflow: auto;
      border-radius: var(--radius);
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.82);
    }}
    @media (max-width: 900px) {{
      .stats {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <header class="topbar">
      <a class="brand" href="/campus_demo/console?view=history">
        <span class="brand-mark">DX</span>
        <span>鼎新智眼</span>
      </a>
      <div class="links">
        <a href="/campus_demo/console?view=history">历史回看</a>
        <a href="/campus_demo/console?view=events">事件中心</a>
      </div>
    </header>
    <section class="hero">
      <div class="eyebrow">Report Archive</div>
      <h1>校园安防报告归档</h1>
      <p>按任务保留可回看的视频核验、事件复核和归档下载入口。</p>
      <div class="stats">
        <div class="stat"><span>报告数量</span><strong>{len(ordered_records)}</strong></div>
        <div class="stat"><span>覆盖数据集</span><strong>{dataset_count}</strong></div>
        <div class="stat"><span>最近生成</span><strong style="font-size:18px">{escape(latest_time)}</strong></div>
      </div>
    </section>
    <section class="panel">
      <div class="table-shell">
        <table>
          <thead>
            <tr>
              <th>视频</th>
              <th>数据集</th>
              <th>原始类别</th>
              <th>校园标签</th>
              <th>事件数</th>
              <th>峰值分数</th>
              <th>生成时间</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
      </div>
    </section>
  </div>
</body>
</html>"""
    (output_root / "index.html").write_text(html, encoding="utf-8")
