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
from pathlib import Path
from typing import Any
from urllib.parse import quote

import cv2

from .config import OUTPUT_ROOT, REPORTS_ROOT


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
    video_path = summary.get("video_path")
    report_id = report_dir.name
    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clip_lookup_json = json.dumps(
        sanitize_for_json({item.get("event_id"): item for item in clip_manifest if item.get("event_id")}),
        ensure_ascii=False,
    )
    fallback_events_json = json.dumps(sanitize_for_json(events), ensure_ascii=False)
    downloads = [
        ("analysis.json", "原始分析结果"),
        ("analysis.current.json", "当前分析结果"),
        ("events.json", "原始事件日志"),
        ("events.current.json", "当前事件日志"),
        ("events.csv", "原始事件 CSV"),
        ("events.current.csv", "当前事件 CSV"),
        ("clips_manifest.json", "切片清单"),
        ("report_bundle.zip", "报告 ZIP"),
        ("../../index.html", "历史首页"),
        ("/campus_demo/console", "控制台"),
    ]
    download_cards_parts = []
    for href, label in downloads:
        target_attr = ' target="_blank"' if href.endswith(".zip") or href.endswith(".html") or href.startswith("/") else ""
        display_name = Path(href).name if not href.startswith("/") else href.split("/")[-1] or href
        download_cards_parts.append(
            f'<a class="download-card" href="{escape(href)}"{target_attr}>'
            f"<span>{escape(label)}</span><strong>{escape(display_name)}</strong></a>"
        )
    download_cards = "".join(download_cards_parts)
    video_html = '<div class="placeholder">当前数据集没有可直接回放的本地源视频，但日志、时间线、导出与复核仍可完整展示。</div>'
    if video_path:
        rel_video = _quoted_relative_path(Path(video_path), report_dir)
        video_html = (
            f'<video id="main-video" controls preload="metadata" src="{rel_video}" width="100%"></video>'
            '<p class="note">点击日志中的时间按钮可跳转对应起点；没有切片时也可使用 jump 链接打开原视频时间段。</p>'
        )

    high_count = sum(1 for event in events if str(event.get("risk_level") or "") == "high")
    pending_count = sum(1 for event in events if str(event.get("review_status") or "pending") == "pending")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(summary["video_name"])} - 校园安防报告</title>
  <style>
    :root {{
      --bg-top: #f0e8d8;
      --bg-bottom: #e0d3be;
      --panel: rgba(255, 252, 246, 0.9);
      --line: rgba(65, 53, 40, 0.14);
      --ink: #172521;
      --muted: #697169;
      --accent: #1d5a4a;
      --accent-deep: #143f33;
      --review: #3c6094;
      --warn: #a56c0d;
      --danger: #b24034;
      --low: #2b7a54;
      --shadow: 0 24px 60px rgba(73, 53, 29, 0.11);
      --shadow-soft: 0 12px 30px rgba(73, 53, 29, 0.08);
      --radius-lg: 28px;
      --radius-md: 22px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(29, 90, 74, 0.16), transparent 28%),
        radial-gradient(circle at top right, rgba(199, 134, 38, 0.14), transparent 22%),
        linear-gradient(180deg, var(--bg-top) 0%, var(--bg-bottom) 100%);
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    button, input, select, textarea {{ font: inherit; }}
    .page {{ max-width: 1440px; margin: 0 auto; padding: 24px 20px 40px; }}
    .hero, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }}
    .hero {{ padding: 28px; margin-bottom: 18px; }}
    .hero-top {{
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) 360px;
      gap: 20px;
      align-items: start;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      color: var(--accent);
      font-size: 12px;
      font-weight: 800;
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
      margin: 16px 0 10px;
      font-family: "STZhongsong", "Source Han Serif SC", "Songti SC", serif;
      font-size: clamp(34px, 4vw, 48px);
      line-height: 1.08;
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
      min-height: 36px;
      padding: 0 14px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.76);
      border: 1px solid var(--line);
      font-size: 13px;
      font-weight: 700;
    }}
    .summary-stack {{
      display: grid;
      gap: 12px;
    }}
    .summary-card, .stat, .download-card, .metric-line {{
      padding: 16px;
      border-radius: var(--radius-md);
      background: rgba(255, 255, 255, 0.8);
      border: 1px solid var(--line);
      box-shadow: var(--shadow-soft);
    }}
    .summary-card span, .stat .label, .download-card span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }}
    .summary-card strong, .stat .value {{
      display: block;
      margin-top: 10px;
      font-size: 28px;
      font-weight: 800;
    }}
    .summary-card p {{ margin: 8px 0 0; color: var(--muted); line-height: 1.7; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.3fr) 380px;
      gap: 18px;
    }}
    .panel {{ padding: 22px; margin-bottom: 18px; }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: start;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }}
    .panel h2 {{
      margin: 8px 0 0;
      font-family: "STZhongsong", "Source Han Serif SC", "Songti SC", serif;
      font-size: 28px;
    }}
    .video-shell video {{
      width: 100%;
      border-radius: 22px;
      background: #111;
      box-shadow: var(--shadow-soft);
    }}
    .placeholder {{
      min-height: 320px;
      display: flex;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 24px;
      border-radius: 24px;
      color: var(--muted);
      line-height: 1.8;
      background:
        radial-gradient(circle at top left, rgba(29, 90, 74, 0.08), transparent 40%),
        linear-gradient(155deg, rgba(255, 255, 255, 0.9), rgba(244, 236, 223, 0.92));
      border: 1px dashed rgba(29, 90, 74, 0.18);
    }}
    .download-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .download-card strong {{
      display: block;
      margin-top: 10px;
      color: var(--ink);
      font-size: 16px;
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
      padding: 6px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid var(--line);
    }}
    .toggle button,
    .toolbar button {{
      border: none;
      border-radius: 999px;
      min-height: 44px;
      padding: 0 16px;
      font-weight: 800;
      cursor: pointer;
    }}
    .toggle button {{
      background: transparent;
      color: var(--muted);
      min-width: 92px;
    }}
    .toggle button.active {{
      background: linear-gradient(135deg, var(--accent) 0%, var(--accent-deep) 100%);
      color: #fff;
    }}
    .toolbar .primary {{
      background: linear-gradient(135deg, var(--accent) 0%, #28705d 100%);
      color: #fff;
    }}
    .toolbar .secondary {{
      background: linear-gradient(135deg, #d8a03d 0%, #b9741f 100%);
      color: #fff;
    }}
    .table-shell {{
      overflow: auto;
      border-radius: 24px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.82);
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
      font-weight: 800;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }}
    .jump {{
      border: none;
      border-radius: 16px;
      padding: 10px 12px;
      background: rgba(29, 90, 74, 0.12);
      color: var(--accent);
      font-weight: 800;
      cursor: pointer;
    }}
    input[type="text"], textarea, select {{
      width: 100%;
      box-sizing: border-box;
      border: 1px solid rgba(65, 53, 40, 0.16);
      border-radius: 16px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      padding: 12px 14px;
    }}
    textarea {{ min-height: 88px; resize: vertical; }}
    .event-title {{ font-weight: 700; }}
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
      font-weight: 700;
    }}
    .event-tag.edited {{
      color: var(--accent);
      border-color: rgba(29, 90, 74, 0.22);
      background: rgba(29, 90, 74, 0.08);
    }}
    .risk-high, .review-false_positive {{ color: var(--danger); font-weight: 700; }}
    .risk-medium {{ color: var(--warn); font-weight: 700; }}
    .risk-review, .review-pending {{ color: var(--review); font-weight: 700; }}
    .risk-low, .review-confirmed {{ color: var(--low); font-weight: 700; }}
    @media (max-width: 960px) {{
      .hero-top, .grid {{ grid-template-columns: 1fr; }}
      .stats, .download-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body data-report-id="{escape(report_id)}">
  <div class="page">
    <section class="hero">
      <div class="hero-top">
        <div>
          <div class="eyebrow">VADTree Campus Report</div>
          <h1>{escape(summary["video_name"])}</h1>
          <p class="lead">{escape(summary["headline"])}</p>
          <div class="hero-chips">
            <span class="hero-chip">{escape(summary["dataset_display_name"])}</span>
            <span class="hero-chip">{escape(summary["source_class"])}</span>
            <span class="hero-chip">{escape(summary["campus_label"])}</span>
            <span class="hero-chip">生成于 {report_time}</span>
          </div>
        </div>
        <div class="summary-stack">
          <div class="summary-card">
            <span>复核版事件数</span>
            <strong id="summary-current-events">{summary["event_count"]}</strong>
            <p>当前展示的是可随日志保存即时更新的复核版本。</p>
          </div>
          <div class="summary-card">
            <span>高风险事件</span>
            <strong id="summary-high-events">{high_count}</strong>
            <p>高风险片段在时间线与日志表里保持最强视觉提示。</p>
          </div>
          <div class="summary-card">
            <span>待复核事件</span>
            <strong id="summary-review-events">{pending_count}</strong>
            <p>支持直接修改行为标签、风险、track、备注和误报状态。</p>
          </div>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><div class="label">Dataset</div><div class="value">{escape(summary["dataset_display_name"])}</div></div>
        <div class="stat"><div class="label">Peak Score</div><div class="value">{summary["peak_score"]:.3f}</div></div>
        <div class="stat"><div class="label">Mean Score</div><div class="value">{summary["mean_score"]:.3f}</div></div>
        <div class="stat"><div class="label">FPS</div><div class="value">{summary["fps"]:.2f}</div></div>
      </div>
    </section>

    <div class="grid">
      <div>
        <section class="panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">Playback</div>
              <h2>视频回放</h2>
            </div>
            <div class="note">点击日志时间按钮即可跳到对应片段，适合现场答辩快速回看。</div>
          </div>
          <div class="video-shell">{video_html}</div>
        </section>
        <section class="panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">Timeline</div>
              <h2>异常分数时间线</h2>
            </div>
            <div class="note">高亮区域是自动生成的告警片段，阈值线对应事件切分逻辑。</div>
          </div>
          <img src="score.svg" alt="score timeline" style="width:100%;height:auto" />
          <p class="note">切片导出模式：<strong>{escape(clip_mode)}</strong>。若没有直接 mp4 切片，可使用 jump 链接打开原视频时间段。</p>
        </section>
      </div>
      <div>
        <section class="panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">Downloads</div>
              <h2>导出与跳转</h2>
            </div>
            <div class="note">支持直接打开原始分析文件、当前复核文件、切片清单和整包 ZIP。</div>
          </div>
          <div class="download-grid">{download_cards}</div>
        </section>
        <section class="panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">Assessment</div>
              <h2>研判摘要</h2>
            </div>
            <div class="note">这一栏汇总阈值、评估指标和当前页面的可编辑能力。</div>
          </div>
          <div class="metric-lines">
            <div class="metric-line">当前校园标签：<strong>{escape(summary["campus_label"])}</strong><br />自动阈值 {summary["threshold"]:.3f}，高风险阈值 {summary["high_threshold"]:.3f}，均值分数 {summary["mean_score"]:.3f}</div>
            <div class="metric-line">ROC AUC：{summary["roc_auc"]} · PR AUC：{summary["pr_auc"]}<br />正样本均值：{summary["pos_mean"]} · 负样本均值：{summary["neg_mean"]}</div>
            <div class="metric-line">本页支持查看“当前版 / 原始版”事件日志；保存后会同步更新 `events.current.*` 与 `report_bundle.zip`。</div>
          </div>
        </section>
      </div>
    </div>

    <section class="panel">
      <div class="panel-head">
        <div>
          <div class="eyebrow">Review Table</div>
          <h2>预警日志与人工复核</h2>
        </div>
        <div class="note">当前版可编辑，原始版只读。这里的字段和控制台保持一致。</div>
      </div>
      <div class="toolbar">
        <div class="toggle">
          <button id="version-current" class="active">当前版</button>
          <button id="version-original">原始版</button>
        </div>
        <button id="save-events" class="primary">保存日志修改</button>
        <button id="reload-events" class="secondary">重新加载日志</button>
        <span id="save-status" class="status">在线修改功能仅在 `python3 campus_demo/app.py serve` 启动后可用。</span>
      </div>
      <div class="table-shell">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>行为</th>
              <th>风险</th>
              <th>复核</th>
              <th>Track</th>
              <th>备注</th>
              <th>原因说明</th>
              <th>切片</th>
            </tr>
          </thead>
          <tbody id="event-table-body"></tbody>
        </table>
      </div>
    </section>
  </div>
  <script>
    const clipLookup = {clip_lookup_json};
    const fallbackEvents = {fallback_events_json};
    const reportId = document.body.dataset.reportId;
    const eventTableBody = document.getElementById('event-table-body');
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
        return `<a href="clips/${{escapeHtml(fileName)}}">mp4</a>`;
      }}
      if (clipEntry.fragment_href) {{
        return `<a href="${{escapeHtml(clipEntry.fragment_href)}}">jump</a>`;
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
        eventTableBody.innerHTML = '<tr><td colspan="8">当前视频没有可展示的事件片段。</td></tr>';
        return;
      }}
      const editable = state.version === 'current';
      eventTableBody.innerHTML = events.map((event) => `
        <tr data-event-id="${{escapeHtml(event.event_id)}}">
          <td>
            <button class="jump" data-start="${{event.start_sec}}">
              ${{formatSeconds(event.start_sec)}}<br />${{formatSeconds(event.end_sec)}}
            </button>
          </td>
          <td>
            <div class="event-title">${{escapeHtml(event.behavior_type || event.campus_label || event.source_class || '待确认')}}</div>
            <div class="event-tags">
              <span class="event-tag">ID ${{escapeHtml(event.event_id)}}</span>
              ${{event.last_edited_at ? '<span class="event-tag edited">已编辑</span>' : ''}}
            </div>
            <div style="margin-top:10px;">
              <input type="text" name="behavior_type" value="${{escapeHtml(event.behavior_type || event.campus_label || event.source_class || '')}}" ${{editable ? '' : 'disabled'}} />
            </div>
          </td>
          <td>
            <select name="risk_level" ${{editable ? '' : 'disabled'}}>
              <option value="low" ${{normalizeRisk(event.risk_level) === 'low' ? 'selected' : ''}}>low</option>
              <option value="review" ${{normalizeRisk(event.risk_level) === 'review' ? 'selected' : ''}}>review</option>
              <option value="medium" ${{normalizeRisk(event.risk_level) === 'medium' ? 'selected' : ''}}>medium</option>
              <option value="high" ${{normalizeRisk(event.risk_level) === 'high' ? 'selected' : ''}}>high</option>
            </select>
            <div class="${{riskClass(event.risk_level)}}" style="margin-top:8px;">${{escapeHtml(normalizeRisk(event.risk_level))}}</div>
          </td>
          <td>
            <select name="review_status" ${{editable ? '' : 'disabled'}}>
              <option value="pending" ${{normalizeReview(event.review_status) === 'pending' ? 'selected' : ''}}>pending</option>
              <option value="confirmed" ${{normalizeReview(event.review_status) === 'confirmed' ? 'selected' : ''}}>confirmed</option>
              <option value="false_positive" ${{normalizeReview(event.review_status) === 'false_positive' ? 'selected' : ''}}>false_positive</option>
            </select>
            <div class="${{reviewClass(event.review_status)}}" style="margin-top:8px;">${{escapeHtml(normalizeReview(event.review_status))}}</div>
          </td>
          <td><input type="text" name="track_ids" value="${{escapeHtml(normalizeTrackIds(event.track_ids).join(', '))}}" ${{editable ? '' : 'disabled'}} /></td>
          <td><textarea name="note" ${{editable ? '' : 'disabled'}}>${{escapeHtml(event.note || '')}}</textarea></td>
          <td><textarea name="reason_text" ${{editable ? '' : 'disabled'}}>${{escapeHtml(event.reason_text || '')}}</textarea></td>
          <td>${{clipCell(event.event_id)}}</td>
        </tr>
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
      return Array.from(document.querySelectorAll('#event-table-body tr[data-event-id]')).map((row) => {{
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
      --bg-top: #f0e8d8;
      --bg-bottom: #e0d3be;
      --panel: rgba(255, 252, 246, 0.9);
      --line: rgba(65, 53, 40, 0.14);
      --ink: #172521;
      --muted: #697169;
      --accent: #1d5a4a;
      --shadow: 0 24px 60px rgba(73, 53, 29, 0.11);
      --shadow-soft: 0 12px 30px rgba(73, 53, 29, 0.08);
      --radius-lg: 28px;
      --radius-md: 22px;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(29, 90, 74, 0.16), transparent 28%),
        radial-gradient(circle at top right, rgba(199, 134, 38, 0.14), transparent 22%),
        linear-gradient(180deg, var(--bg-top) 0%, var(--bg-bottom) 100%);
    }}
    .page {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 24px 20px 40px;
    }}
    .hero, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow);
      padding: 24px;
      margin-bottom: 18px;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      color: var(--accent);
      font-size: 12px;
      font-weight: 800;
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
      margin: 16px 0 10px;
      font-family: "STZhongsong", "Source Han Serif SC", "Songti SC", serif;
      font-size: clamp(32px, 4vw, 46px);
      line-height: 1.08;
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
      border-radius: var(--radius-md);
      background: rgba(255, 255, 255, 0.8);
      border: 1px solid var(--line);
      box-shadow: var(--shadow-soft);
    }}
    .stat span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }}
    .stat strong {{
      display: block;
      margin-top: 10px;
      font-size: 28px;
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
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
      font-weight: 700;
    }}
    .links a {{
      display: inline-flex;
      align-items: center;
      min-height: 42px;
      margin-right: 12px;
      margin-top: 10px;
      padding: 0 14px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.82);
      border: 1px solid var(--line);
    }}
    .table-shell {{
      overflow: auto;
      border-radius: var(--radius-md);
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
    <section class="hero">
      <div class="eyebrow">Campus Demo Reports</div>
      <h1>校园安防历史报告索引</h1>
      <p>这里汇总了基于缓存结果或运行时任务生成的离线报告，适合在控制台之外做统一回看和跳转。</p>
      <div class="stats">
        <div class="stat"><span>报告数量</span><strong>{len(ordered_records)}</strong></div>
        <div class="stat"><span>覆盖数据集</span><strong>{dataset_count}</strong></div>
        <div class="stat"><span>最近生成</span><strong style="font-size:18px">{escape(latest_time)}</strong></div>
      </div>
      <div class="links">
        <a href="/campus_demo/console">打开控制台</a>
        <a href="/campus_demo_outputs/">打开输出目录</a>
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
