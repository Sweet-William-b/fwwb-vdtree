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
  <text x="{margin_left}" y="14" font-size="14" fill="#111827">Anomaly score timeline</text>
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
    video_html = '<p class="note">No local source video is available for this dataset.</p>'
    if video_path:
        rel_video = _quoted_relative_path(Path(video_path), report_dir)
        video_html = (
            f'<video id="main-video" controls preload="metadata" src="{rel_video}" width="100%"></video>'
            '<p class="note">Click any event row to jump to its start time.</p>'
        )

    report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(summary["video_name"])} - Campus Demo</title>
  <style>
    :root {{
      --bg: #f3efe6;
      --panel: #fffdf8;
      --ink: #1f2937;
      --muted: #6b7280;
      --line: #d6d3d1;
      --accent: #14532d;
      --warn: #92400e;
      --danger: #991b1b;
    }}
    body {{
      margin: 0;
      font-family: "Georgia", "Noto Serif SC", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(20,83,45,0.10), transparent 30%),
        radial-gradient(circle at bottom right, rgba(153,27,27,0.08), transparent 25%),
        var(--bg);
    }}
    .page {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 24px;
    }}
    .hero, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 8px 30px rgba(0,0,0,0.05);
    }}
    .hero {{
      padding: 24px;
      margin-bottom: 18px;
    }}
    .hero h1 {{
      margin: 0 0 8px;
      font-size: 32px;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.6;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .stat {{
      padding: 14px;
      border-radius: 14px;
      background: #faf7ef;
      border: 1px solid var(--line);
    }}
    .stat .label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .stat .value {{
      font-size: 24px;
      margin-top: 8px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 18px;
    }}
    .panel {{
      padding: 18px;
      margin-bottom: 18px;
    }}
    .panel h2 {{
      margin-top: 0;
      font-size: 22px;
    }}
    .downloads a {{
      display: inline-block;
      margin-right: 10px;
      margin-bottom: 8px;
      color: var(--accent);
      text-decoration: none;
      font-weight: 600;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      border-top: 1px solid var(--line);
      padding: 10px 8px;
      vertical-align: top;
      text-align: left;
    }}
    th {{
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--muted);
    }}
    .jump {{
      border: none;
      background: #ecfccb;
      color: #365314;
      border-radius: 999px;
      padding: 6px 10px;
      cursor: pointer;
      font-weight: 700;
    }}
    .note {{
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }}
    .toolbar {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 14px;
    }}
    .toolbar button {{
      border: none;
      border-radius: 999px;
      padding: 10px 16px;
      background: #14532d;
      color: #fff;
      cursor: pointer;
      font-weight: 700;
    }}
    .toolbar button.secondary {{
      background: #d97706;
    }}
    .toolbar .status {{
      align-self: center;
      color: var(--muted);
      font-size: 14px;
    }}
    input[type="text"], textarea, select {{
      width: 100%;
      box-sizing: border-box;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      padding: 8px 10px;
    }}
    textarea {{
      min-height: 76px;
      resize: vertical;
    }}
    .risk-high {{ color: var(--danger); font-weight: 700; }}
    .risk-medium {{ color: var(--warn); font-weight: 700; }}
    .risk-review {{ color: #1d4ed8; font-weight: 700; }}
    .risk-low {{ color: var(--accent); font-weight: 700; }}
    @media (max-width: 960px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body data-report-id="{escape(report_id)}">
  <div class="page">
    <section class="hero">
      <h1>{escape(summary["video_name"])}</h1>
      <p>{escape(summary["headline"])}</p>
      <div class="stats">
        <div class="stat"><div class="label">Dataset</div><div class="value">{escape(summary["dataset_display_name"])}</div></div>
        <div class="stat"><div class="label">Source Class</div><div class="value">{escape(summary["source_class"])}</div></div>
        <div class="stat"><div class="label">Campus Label</div><div class="value">{escape(summary["campus_label"])}</div></div>
        <div class="stat"><div class="label">Events</div><div class="value">{summary["event_count"]}</div></div>
        <div class="stat"><div class="label">Peak Score</div><div class="value">{summary["peak_score"]:.3f}</div></div>
        <div class="stat"><div class="label">Generated</div><div class="value" style="font-size:18px">{report_time}</div></div>
      </div>
    </section>

    <div class="grid">
      <div>
        <section class="panel">
          <h2>Video Playback</h2>
          {video_html}
        </section>
        <section class="panel">
          <h2>Timeline</h2>
          <img src="score.svg" alt="score timeline" style="width:100%;height:auto" />
          <p class="note">
            Clip export mode: <strong>{escape(clip_mode)}</strong>. If direct mp4 slices are unavailable,
            use the jump links to open the source video at the alert segment.
          </p>
        </section>
      </div>
      <div>
        <section class="panel">
          <h2>Downloads</h2>
          <div class="downloads">
            <a href="analysis.json">analysis.json</a>
            <a href="analysis.current.json">analysis.current.json</a>
            <a href="events.json">events.json</a>
            <a href="events.current.json">events.current.json</a>
            <a href="events.csv">events.csv</a>
            <a href="events.current.csv">events.current.csv</a>
            <a href="clips_manifest.json">clips_manifest.json</a>
            <a href="report_bundle.zip">report_bundle.zip</a>
            <a href="../../index.html">history index</a>
            <a href="/campus_demo/console">web console</a>
          </div>
          <p class="note">
            Threshold {summary["threshold"]:.3f}, high-risk threshold {summary["high_threshold"]:.3f},
            score mean {summary["mean_score"]:.3f}.
          </p>
        </section>
        <section class="panel">
          <h2>Metrics Snapshot</h2>
          <p class="note">
            ROC AUC: {summary["roc_auc"]} | PR AUC: {summary["pr_auc"]}<br/>
            Positive mean: {summary["pos_mean"]} | Negative mean: {summary["neg_mean"]}
          </p>
        </section>
      </div>
    </div>

    <section class="panel">
      <h2>Alert Events</h2>
      <div class="toolbar">
        <button id="save-events">保存日志修改</button>
        <button id="reload-events" class="secondary">重新加载当前日志</button>
        <span id="save-status" class="status">在线修改功能仅在 `python3 campus_demo/app.py serve` 启动后可用。</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Event</th>
            <th>Campus Label</th>
            <th>Risk</th>
            <th>Range</th>
            <th>Peak</th>
            <th>Mean</th>
            <th>Reason</th>
            <th>Clip</th>
          </tr>
        </thead>
        <tbody id="event-table-body"></tbody>
      </table>
    </section>
  </div>
  <script>
    const clipLookup = {json.dumps(sanitize_for_json({item["event_id"]: item for item in clip_manifest}), ensure_ascii=False)};
    const fallbackEvents = {json.dumps(sanitize_for_json(events), ensure_ascii=False)};
    const reportId = document.body.dataset.reportId;
    const eventTableBody = document.getElementById('event-table-body');
    const saveStatus = document.getElementById('save-status');

    function escapeHtml(value) {{
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
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

    function renderEvents(events) {{
      if (!events.length) {{
        eventTableBody.innerHTML = '<tr><td colspan="8">No alert segment was generated for this video.</td></tr>';
        return;
      }}
      eventTableBody.innerHTML = events.map((event) => `
        <tr data-event-id="${{escapeHtml(event.event_id)}}">
          <td><button class="jump" data-start="${{event.start_sec}}">${{escapeHtml(event.event_id)}}</button></td>
          <td><input type="text" name="campus_label" value="${{escapeHtml(event.campus_label)}}" /></td>
          <td>
            <select name="risk_level">
              <option value="low" ${{event.risk_level === 'low' ? 'selected' : ''}}>low</option>
              <option value="review" ${{event.risk_level === 'review' ? 'selected' : ''}}>review</option>
              <option value="medium" ${{event.risk_level === 'medium' ? 'selected' : ''}}>medium</option>
              <option value="high" ${{event.risk_level === 'high' ? 'selected' : ''}}>high</option>
            </select>
          </td>
          <td>${{event.start_sec.toFixed(2)}}s - ${{event.end_sec.toFixed(2)}}s</td>
          <td>${{Number(event.peak_score).toFixed(3)}}</td>
          <td>${{Number(event.mean_score).toFixed(3)}}</td>
          <td><textarea name="reason_text">${{escapeHtml(event.reason_text)}}</textarea></td>
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

    async function loadEvents() {{
      try {{
        const response = await fetch(`events.current.json?ts=${{Date.now()}}`);
        if (!response.ok) throw new Error('failed');
        renderEvents(await response.json());
        saveStatus.textContent = '当前展示的是最新已保存的日志。';
      }} catch (_error) {{
        renderEvents(fallbackEvents);
      }}
    }}

    function collectEditedEvents() {{
      return Array.from(document.querySelectorAll('#event-table-body tr[data-event-id]')).map((row) => {{
        const eventId = row.dataset.eventId;
        const original = fallbackEvents.find((item) => item.event_id === eventId) || {{}};
        return {{
          ...original,
          event_id: eventId,
          campus_label: row.querySelector('[name="campus_label"]').value.trim(),
          risk_level: row.querySelector('[name="risk_level"]').value,
          reason_text: row.querySelector('[name="reason_text"]').value.trim(),
        }};
      }});
    }}

    async function saveEvents() {{
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
        rows.append('<tr><td colspan="7">No report has been generated yet.</td></tr>')

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Campus Demo Reports</title>
  <style>
    body {{
      margin: 0;
      background: #f5f5f4;
      color: #1c1917;
      font-family: "Georgia", serif;
    }}
    .page {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 24px;
    }}
    .hero, .panel {{
      background: #fffbeb;
      border: 1px solid #d6d3d1;
      border-radius: 18px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.05);
      padding: 20px;
      margin-bottom: 18px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      border-top: 1px solid #d6d3d1;
      padding: 10px 8px;
      text-align: left;
    }}
    th {{
      color: #57534e;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    a {{
      color: #14532d;
      text-decoration: none;
      font-weight: 600;
    }}
    .links a {{
      display: inline-block;
      margin-right: 14px;
      margin-top: 8px;
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1 style="margin-top:0">Campus Security Demo Reports</h1>
      <p>Static offline reports generated from cached VADTree anomaly results.</p>
      <div class="links">
        <a href="/campus_demo/console">Open web console</a>
      </div>
    </section>
    <section class="panel">
      <table>
        <thead>
          <tr>
            <th>Video</th>
            <th>Dataset</th>
            <th>Source Class</th>
            <th>Campus Label</th>
            <th>Events</th>
            <th>Peak Score</th>
            <th>Generated</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>
  </div>
</body>
</html>"""
    (output_root / "index.html").write_text(html, encoding="utf-8")
