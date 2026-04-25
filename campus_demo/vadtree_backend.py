from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from sklearn.cluster import KMeans

from .config import OUTPUT_ROOT, REPO_ROOT
from .result_loader import get_store

StageCallback = Callable[[str, float], None]


GEBD_THRESHOLD = 0.5
HGTREE_GAMMA = 0.4
HGTREE_MIN_LENGTH = 1
REFINE_TOP_K = 10
REFINE_NUM_NEIGHBORS = 10
REFINE_TAO = 0.1
CORRELATION_BETA = 0.2
DEFAULT_GEBD_WEIGHTS = REPO_ROOT / "EfficientGEBD" / "output" / "x2x3x4_r50_eff" / "model_best.pth"
DEFAULT_GEBD_CONFIG = REPO_ROOT / "EfficientGEBD" / "config-files" / "baseline.yaml"
DEFAULT_VLM_MODEL_DIR = REPO_ROOT / "LLaVA-NeXT" / "LLaVA-Video-7B-Qwen2"
DEFAULT_LLM_MODEL_DIR = REPO_ROOT / "DeepSeek-R1" / "DeepSeek-R1-Distill-Qwen-7B"
DEFAULT_VADTREE_PYTHON = Path.home() / "miniconda3" / "envs" / "EfficientGEBD" / "bin" / "python"
DEFAULT_LLAVA_PYTHON = Path.home() / "miniconda3" / "envs" / "llava" / "bin" / "python"
DEFAULT_LLM_PYTHON = Path.home() / "miniconda3" / "envs" / "llava" / "bin" / "python"
DEFAULT_IMAGEBIND_PYTHON = Path.home() / "miniconda3" / "envs" / "VADTree" / "bin" / "python"
DEFAULT_PYTHON_BY_ENV = {
    "VADTREE_VADTREE_PYTHON": DEFAULT_VADTREE_PYTHON,
    "VADTREE_LLaVA_PYTHON": DEFAULT_LLAVA_PYTHON,
    "VADTREE_LLM_PYTHON": DEFAULT_LLM_PYTHON,
    "VADTREE_IMAGEBIND_PYTHON": DEFAULT_IMAGEBIND_PYTHON,
}


class VADTreeDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class VADTreeDependencyConfig:
    vadtree_python: str
    llava_python: str
    llm_python: str
    imagebind_python: str
    gebd_weights: Path
    gebd_config: Path
    vlm_model_dir: Path
    llm_model_dir: Path


@dataclass
class VADTreePipelineResult:
    dataset_name: str
    dataset_display_name: str
    video_name: str
    video_stem: str
    video_path: Path
    source_fps: float
    total_frames: int
    frame_scores: list[float]
    captions: dict[str, Any]
    reasoning: dict[str, Any]
    source_class: str
    metrics: dict[str, Any]
    work_dir: Path
    artifacts: dict[str, str]


def _dataset_token(dataset_name: str) -> str:
    normalized = dataset_name.strip().lower()
    if normalized == "ucf":
        return "UCF"
    if normalized == "msad":
        return "MSAD"
    if normalized == "xd":
        return "XD"
    raise ValueError(f"Unsupported dataset '{dataset_name}'.")


def _normalize_video_name(video_name: str) -> str:
    return Path(video_name).name if video_name else "input.mp4"


def _default_python_env(env_name: str) -> str:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        default_candidate = DEFAULT_PYTHON_BY_ENV.get(env_name)
        if default_candidate is not None and default_candidate.exists():
            return str(default_candidate.resolve())
        return sys.executable
    if "/" not in raw and "\\" not in raw:
        return raw
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return str(candidate.resolve())


def _resolve_env_path(env_name: str, default: Path | None = None) -> tuple[str, Path | None]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        if default is None:
            return "", None
        return str(default), default.resolve()

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return raw, candidate.resolve()


def load_dependency_config() -> VADTreeDependencyConfig:
    _gebd_weights_raw, gebd_weights = _resolve_env_path(
        "VADTREE_GEBD_WEIGHTS",
        DEFAULT_GEBD_WEIGHTS,
    )
    _gebd_config_raw, gebd_config = _resolve_env_path(
        "VADTREE_GEBD_CONFIG",
        DEFAULT_GEBD_CONFIG,
    )
    vlm_model_dir_raw, vlm_model_dir = _resolve_env_path("VADTREE_VLM_MODEL_DIR", DEFAULT_VLM_MODEL_DIR)
    llm_model_dir_raw, llm_model_dir = _resolve_env_path("VADTREE_LLM_MODEL_DIR", DEFAULT_LLM_MODEL_DIR)

    missing: list[str] = []
    if gebd_weights is None or not gebd_weights.exists():
        missing.append(f"GEBD weights not found: {gebd_weights}")
    elif not gebd_weights.is_file():
        missing.append(f"GEBD weights must point to a checkpoint file, not a directory: {gebd_weights}")
    if gebd_config is None or not gebd_config.exists():
        missing.append(f"GEBD config not found: {gebd_config}")
    elif not gebd_config.is_file():
        missing.append(f"GEBD config must point to a config file: {gebd_config}")
    if vlm_model_dir is None or not vlm_model_dir.exists():
        missing.append(f"VLM model dir not found: {vlm_model_dir}")
    elif not vlm_model_dir.is_dir():
        missing.append(f"VLM model dir must be a directory: {vlm_model_dir}")
    if llm_model_dir is None or not llm_model_dir.exists():
        missing.append(f"LLM model dir not found: {llm_model_dir}")
    elif not llm_model_dir.is_dir():
        missing.append(f"LLM model dir must be a directory: {llm_model_dir}")

    for path in (
        REPO_ROOT / "LLaVA-NeXT" / "infer_VAD.py",
        REPO_ROOT / "DeepSeek-R1" / "deepseek_batch_infer.py",
        REPO_ROOT / "ImageBind" / "imagebind_sim.py",
        REPO_ROOT / "refinement_eval.py",
        REPO_ROOT / "correlation_eval.py",
    ):
        if not path.exists():
            missing.append(f"Missing pipeline script: {path}")

    if missing:
        raise VADTreeDependencyError(
            "Real VADTree pipeline is unavailable. "
            + " | ".join(missing)
            + " | Relative env paths are resolved from the repository root."
            + " | Configure the model directories and Python environments before using upload/RTSP."
        )

    return VADTreeDependencyConfig(
        vadtree_python=_default_python_env("VADTREE_VADTREE_PYTHON"),
        llava_python=_default_python_env("VADTREE_LLaVA_PYTHON"),
        llm_python=_default_python_env("VADTREE_LLM_PYTHON"),
        imagebind_python=_default_python_env("VADTREE_IMAGEBIND_PYTHON"),
        gebd_weights=gebd_weights,
        gebd_config=gebd_config,
        vlm_model_dir=vlm_model_dir,
        llm_model_dir=llm_model_dir,
    )


def _emit_stage(callback: StageCallback | None, stage: str, progress: float) -> None:
    if callback is not None:
        callback(stage, progress)


def _run_command(command: list[str], *, cwd: Path, stage: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"exit code {result.returncode}"
        raise RuntimeError(f"{stage} failed: {detail[-800:]}")
    return result


def _inspect_video(video_path: Path) -> tuple[float, int]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Failed to open source video: {video_path}")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        capture.release()
    if fps <= 0 or not math.isfinite(fps):
        fps = 25.0
    if frame_count <= 0:
        raise RuntimeError(f"Source video has no readable frames: {video_path}")
    return fps, frame_count


def _gebd_dataset_metadata(dataset_name: str) -> tuple[str, str]:
    normalized = dataset_name.strip().lower()
    if normalized == "ucf":
        return "ucf_crime", "UCF_Crime_test"
    if normalized == "msad":
        return "MSAD", "MSAD_test"
    if normalized == "xd":
        return "xd_violence", "XD_Violence_test"
    raise ValueError(f"Unsupported dataset '{dataset_name}'.")


def _safe_link_or_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        target.symlink_to(source)
    except OSError:
        shutil.copy2(source, target)


def _prepare_runtime_gebd_inputs(
    *,
    dataset_name: str,
    video_path: Path,
    video_name: str,
    total_frames: int,
    work_dir: Path,
) -> tuple[Path, Path]:
    annotation_token, _result_token = _gebd_dataset_metadata(dataset_name)
    runtime_video_dir = work_dir / "gebd_video_dir"
    runtime_video_dir.mkdir(parents=True, exist_ok=True)

    normalized_video_name = _normalize_video_name(video_name)
    runtime_video_path = runtime_video_dir / normalized_video_name
    _safe_link_or_copy(video_path, runtime_video_path)

    runtime_annotation_dir = work_dir / annotation_token / "annotations"
    runtime_annotation_dir.mkdir(parents=True, exist_ok=True)
    annotation_path = runtime_annotation_dir / "runtime_single_video.txt"
    video_stem = Path(normalized_video_name).stem
    annotation_line = f"{video_stem} 0 {max(total_frames - 1, 0)} 0\n"
    annotation_path.write_text(annotation_line, encoding="utf-8")
    return runtime_video_dir, annotation_path


def _prepare_runtime_video_root(*, video_path: Path, video_name: str, work_dir: Path) -> Path:
    runtime_video_root = work_dir / "runtime_video_root"
    runtime_video_root.mkdir(parents=True, exist_ok=True)
    runtime_video_path = runtime_video_root / _normalize_video_name(video_name)
    _safe_link_or_copy(video_path, runtime_video_path)
    return runtime_video_root


def _run_gebd(
    config: VADTreeDependencyConfig,
    *,
    dataset_name: str,
    video_name: str,
    video_path: Path,
    work_dir: Path,
) -> tuple[list[float], list[int], float, int]:
    fps_hint, total_frames_hint = _inspect_video(video_path)
    runtime_video_dir, annotation_path = _prepare_runtime_gebd_inputs(
        dataset_name=dataset_name,
        video_path=video_path,
        video_name=video_name,
        total_frames=total_frames_hint,
        work_dir=work_dir,
    )
    dataset_token, result_dir_name = _gebd_dataset_metadata(dataset_name)
    runtime_ckpt_dir = work_dir / f"{config.gebd_weights.parent.name}__{work_dir.parent.name}"
    runtime_ckpt_dir.mkdir(parents=True, exist_ok=True)
    runtime_ckpt_path = runtime_ckpt_dir / config.gebd_weights.name
    _safe_link_or_copy(config.gebd_weights, runtime_ckpt_path)

    command = [
        config.vadtree_python,
        str(REPO_ROOT / "EfficientGEBD" / "GEBD_split100.py"),
        "--video_dir",
        str(runtime_video_dir),
        "--annotationfile_path",
        str(annotation_path),
        "--config-file",
        str(config.gebd_config),
        "--resume",
        str(runtime_ckpt_path),
    ]
    _run_command(command, cwd=REPO_ROOT / "EfficientGEBD", stage="GEBD inference")

    model_name = runtime_ckpt_path.parent.name
    if dataset_token == "xd_violence":
        output_dir = REPO_ROOT / "result" / result_dir_name / f"EGEBD_{model_name}_split_th{GEBD_THRESHOLD}"
    else:
        output_dir = REPO_ROOT / "result" / result_dir_name / f"EGEBD_{model_name}_split_out_th{GEBD_THRESHOLD}"
    pred_json_path = output_dir / f"pred_scenes_th{GEBD_THRESHOLD}.json"
    scenes_json_path = output_dir / f"scenes_th{GEBD_THRESHOLD}.json"
    if not pred_json_path.exists():
        raise RuntimeError(f"GEBD inference did not create {pred_json_path}.")

    pred_payload = json.loads(pred_json_path.read_text(encoding="utf-8"))
    video_key = _normalize_video_name(video_name)
    if video_key not in pred_payload:
        raise RuntimeError(f"GEBD output does not contain video '{video_key}'.")
    payload = pred_payload[video_key]

    pred = [float(item) for item in payload.get("pred", [])]
    total_frames = int(payload.get("frames") or total_frames_hint)
    fps = float(payload.get("fps") or fps_hint)
    frame_indices = np.linspace(0, max(total_frames - 1, 0), len(pred), dtype=int).tolist() if pred else []
    if not pred or not frame_indices or total_frames <= 0:
        raise RuntimeError("GEBD inference returned empty boundary scores.")

    (work_dir / "gebd.raw.json").write_text(
        json.dumps(
            {
                "video_name": video_key,
                "pred_json": str(pred_json_path),
                "scenes_json": str(scenes_json_path),
                "pred_entry": payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return pred, frame_indices, fps, total_frames


def _get_idx_from_score_by_threshold(
    threshold: float,
    seq_indices: list[int],
    seq_scores: list[float],
) -> tuple[list[int], list[float]]:
    boundary_groups: list[list[int]] = []
    active_group: list[int] = []
    for index, score in enumerate(seq_scores):
        if score >= threshold:
            active_group.append(index)
        elif active_group:
            boundary_groups.append(active_group)
            active_group = []
    if active_group:
        boundary_groups.append(active_group)

    boundary_frames: list[int] = []
    boundary_scores: list[float] = []
    for group in boundary_groups:
        center = int(round(sum(group) / len(group)))
        source_index = group[min(range(len(group)), key=lambda item: abs(group[item] - center))]
        boundary_frames.append(int(seq_indices[center]))
        boundary_scores.append(round(float(seq_scores[source_index]), 4))
    return boundary_frames, boundary_scores


def _get_peak_idx_from_score_by_threshold(
    threshold: float,
    seq_indices: list[int],
    seq_scores: list[float],
) -> tuple[list[int], list[float]]:
    peaks: list[int] = []
    peak_scores: list[float] = []
    for index in range(1, len(seq_indices) - 1):
        score = float(seq_scores[index])
        if score < threshold:
            continue
        if score >= float(seq_scores[index - 1]) and score >= float(seq_scores[index + 1]):
            peaks.append(int(seq_indices[index]))
            peak_scores.append(round(score, 4))
    return peaks, peak_scores


def _calculate_dfs_all_idx(
    frame_end: int,
    out_idx: list[int],
    out_idx_score: list[float],
    min_length: int,
) -> tuple[list[list[int]], list[list[Any]]]:
    candidates = sorted(zip(out_idx, out_idx_score), key=lambda item: (-item[1], item[0]))
    frame_score_dict = {int(frame): float(score) for frame, score in candidates}
    frame_score_dict[0] = 1.0
    frame_score_dict[frame_end] = 1.0

    stack: list[list[int]] = [[0, frame_end]]
    used: set[int] = set()
    segments: list[list[int]] = []
    segments_b: list[list[Any]] = []

    while stack:
        start, end = stack.pop()
        segments_b.append([[start, end], [frame_score_dict[start], frame_score_dict[end]]])

        split_frame: int | None = None
        for frame, _score in candidates:
            if frame in used:
                continue
            if start < frame < end:
                split_frame = int(frame)
                used.add(split_frame)
                break

        if split_frame is None:
            segments.append([start, end])
            continue

        left_len = split_frame - start
        right_len = end - split_frame
        if left_len < min_length or right_len < min_length:
            if left_len >= min_length or right_len >= min_length:
                if start == 0 and left_len < min_length:
                    segments.append([start, split_frame])
                    segments_b.append([[start, split_frame], [frame_score_dict[start], frame_score_dict[split_frame]]])
                    stack.append([split_frame, end])
                elif end == frame_end and right_len < min_length:
                    segments.append([split_frame, end])
                    segments_b.append([[split_frame, end], [frame_score_dict[split_frame], frame_score_dict[end]]])
                    stack.append([start, split_frame])
                else:
                    stack.append([start, end])
                    segments_b.pop()
            else:
                segments.append([start, end])
            continue

        stack.append([split_frame, end])
        stack.append([start, split_frame])

    segments.sort()
    segments_b.sort(key=lambda item: item[0][0])
    return segments, segments_b


def _check_scenes(scenes: list[list[int]], frames: int) -> None:
    if not scenes:
        raise RuntimeError("HGTree scenes are empty.")
    if scenes[0][0] != 0 or scenes[-1][-1] != frames - 1:
        raise RuntimeError("HGTree scenes do not cover the whole video.")
    for index in range(len(scenes) - 1):
        if scenes[index][1] != scenes[index + 1][0]:
            raise RuntimeError("HGTree scenes are not continuous.")


def _remove_redundant(nodes: list[list[Any]]) -> tuple[list[list[int]], list[list[int]]]:
    non_redundant: list[list[int]] = []
    redundant: list[list[int]] = []
    for index, node in enumerate(nodes):
        interval = node[0]
        is_redundant = False
        for other_index, other_node in enumerate(nodes):
            if index == other_index:
                continue
            other_interval = other_node[0]
            if other_interval[0] >= interval[0] and other_interval[1] <= interval[1]:
                if other_interval[0] == interval[0] and other_interval[1] == interval[1]:
                    continue
                is_redundant = True
                break
        if is_redundant:
            redundant.append(interval)
        else:
            non_redundant.append(interval)
    return non_redundant, redundant


def _fine_completion(coarse: list[list[int]], fine: list[list[int]], frames: int) -> list[list[int]]:
    completed = list(fine)
    if not completed:
        return list(coarse)
    if completed[0][0] != 0:
        completed.insert(0, coarse[0])
    if completed[-1][-1] != frames - 1:
        completed.append(coarse[-1])

    common: list[list[int]] = []
    for index in range(len(completed) - 1):
        if completed[index][1] == completed[index + 1][0]:
            continue
        for coarse_interval in coarse:
            if coarse_interval[0] >= completed[index][1] and coarse_interval[1] <= completed[index + 1][0]:
                common.append(coarse_interval)
    completed.extend(common)
    completed.sort()
    return completed


def _kmeans_two_clusters(boundary_score: dict[int, float]) -> tuple[list[int], list[int], list[float], list[float]]:
    keys = list(boundary_score.keys())
    scores = np.array(list(boundary_score.values()), dtype=float).reshape(-1, 1)
    kmeans = KMeans(n_clusters=2, random_state=0, n_init=10)
    kmeans.fit(scores)
    labels = kmeans.labels_
    centers = kmeans.cluster_centers_.flatten()

    class0_keys = [int(key) for key, label in zip(keys, labels) if label == 0]
    class1_keys = [int(key) for key, label in zip(keys, labels) if label == 1]
    class0_scores = [float(boundary_score[key]) for key in class0_keys]
    class1_scores = [float(boundary_score[key]) for key in class1_keys]

    if centers[0] > centers[1]:
        return class1_keys, class0_keys, class1_scores, class0_scores
    return class0_keys, class1_keys, class0_scores, class1_scores


def _hierarchical(
    nodes_with_scores: list[list[Any]],
    threshold: float,
    frames: int,
) -> tuple[list[list[int]], list[list[int]], list[list[int]]]:
    coarse_nodes = [node for node in nodes_with_scores if min(node[1]) >= threshold]
    fine_nodes = [node for node in nodes_with_scores if min(node[1]) < threshold]

    coarse, redundant_c = _remove_redundant(coarse_nodes)
    _check_scenes(coarse, frames)

    fine, redundant_f = _remove_redundant(fine_nodes)
    fine = _fine_completion(coarse, fine, frames)
    _check_scenes(fine, frames)
    return coarse, fine, redundant_c + redundant_f


def _build_initial_scenes(boundary_frames: list[int], total_frames: int) -> list[list[int]]:
    points = sorted({0, *[int(item) for item in boundary_frames], total_frames - 1})
    scenes: list[list[int]] = []
    for index in range(len(points) - 1):
        scenes.append([points[index], points[index + 1]])
    if not scenes:
        scenes.append([0, total_frames - 1])
    return scenes


def _build_hgtree_outputs(
    *,
    video_name: str,
    sampled_scores: list[float],
    fps: float,
    total_frames: int,
    work_dir: Path,
) -> dict[str, Path]:
    input_json_path = work_dir / "pred_scenes_input.json"
    boundary_frames, _ = _get_idx_from_score_by_threshold(
        GEBD_THRESHOLD,
        np.linspace(0, total_frames - 1, len(sampled_scores), dtype=int).tolist(),
        sampled_scores,
    )
    input_payload = {
        video_name: {
            "pred": [round(float(item), 4) for item in sampled_scores],
            "scenes": _build_initial_scenes(boundary_frames, total_frames),
            "fps": round(float(fps), 4),
            "frames": int(total_frames),
        }
    }
    input_json_path.write_text(json.dumps(input_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    idx_split = np.linspace(0, total_frames - 1, len(sampled_scores), dtype=int).tolist()
    peak_idx, peak_scores = _get_peak_idx_from_score_by_threshold(HGTREE_GAMMA, idx_split, sampled_scores)
    fine_nodes, nodes_with_scores = _calculate_dfs_all_idx(total_frames - 1, peak_idx, peak_scores, HGTREE_MIN_LENGTH)

    legal_peak_idx: list[int] = []
    for interval in fine_nodes:
        legal_peak_idx.extend(interval)
    legal_peak_idx = sorted(set(legal_peak_idx))[1:-1]
    peak_boundary_score = {int(frame): float(score) for frame, score in zip(peak_idx, peak_scores)}
    legal_peak_boundary_score = {
        frame: peak_boundary_score[frame]
        for frame in legal_peak_idx
        if frame in peak_boundary_score
    }

    if len(set(legal_peak_boundary_score.values())) >= 2:
        _fine_keys, _coarse_keys, fine_values, coarse_values = _kmeans_two_clusters(legal_peak_boundary_score)
        split_threshold = min(coarse_values)
        coarse_scenes, fine_scenes, redundant_scenes = _hierarchical(nodes_with_scores, split_threshold, total_frames)
    else:
        coarse_scenes = list(fine_nodes)
        fine_scenes = list(fine_nodes)
        redundant_scenes = list(fine_nodes)

    hgtree_dir = work_dir / "EGEBD_runtime_peak_dfs_kmeans_1_0.4"
    hgtree_dir.mkdir(parents=True, exist_ok=True)

    pred_payload = {
        video_name: {
            "pred": [round(float(item), 4) for item in sampled_scores],
            "fps": round(float(fps), 4),
            "frames": int(total_frames),
            "threshold": "kmeans",
            "min_length": HGTREE_MIN_LENGTH,
            "gamma": HGTREE_GAMMA,
        }
    }
    coarse_payload = {
        video_name: {
            "scenes": coarse_scenes,
            "fps": round(float(fps), 4),
            "frames": int(total_frames),
            "threshold": "kmeans",
            "min_length": HGTREE_MIN_LENGTH,
            "gamma": HGTREE_GAMMA,
        }
    }
    fine_payload = {
        video_name: {
            "scenes": fine_scenes,
            "fps": round(float(fps), 4),
            "frames": int(total_frames),
            "threshold": "kmeans",
            "min_length": HGTREE_MIN_LENGTH,
            "gamma": HGTREE_GAMMA,
        }
    }
    redundant_payload = {
        video_name: {
            "scenes": redundant_scenes,
            "fps": round(float(fps), 4),
            "frames": int(total_frames),
            "threshold": "kmeans",
            "min_length": HGTREE_MIN_LENGTH,
            "gamma": HGTREE_GAMMA,
        }
    }

    pred_json = hgtree_dir / "pred.json"
    coarse_json = hgtree_dir / "dfs_coarse_scenes.json"
    fine_json = hgtree_dir / "dfs_fine_scenes.json"
    redundant_json = hgtree_dir / "dfs_redundant_scenes.json"
    pred_json.write_text(json.dumps(pred_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    coarse_json.write_text(json.dumps(coarse_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fine_json.write_text(json.dumps(fine_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    redundant_json.write_text(json.dumps(redundant_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "input_json": input_json_path,
        "pred_json": pred_json,
        "coarse_json": coarse_json,
        "fine_json": fine_json,
        "redundant_json": redundant_json,
    }


def _latest_created_file(root: Path, pattern: str, *, exclude: set[Path] | None = None) -> Path:
    exclude = exclude or set()
    candidates = [path for path in root.rglob(pattern) if path not in exclude]
    if not candidates:
        raise RuntimeError(f"No file matching '{pattern}' was produced under {root}.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _run_llava(
    config: VADTreeDependencyConfig,
    *,
    json_path: Path,
    video_root: Path,
) -> Path:
    before = set(json_path.parent.rglob("*.json"))
    command = [
        config.llava_python,
        str(REPO_ROOT / "LLaVA-NeXT" / "infer_VAD.py"),
        "--pretrained",
        str(config.vlm_model_dir),
        "--video_root",
        str(video_root),
        "--json_path",
        str(json_path),
        "--prompt_flag",
        "prior_q",
    ]
    _run_command(command, cwd=REPO_ROOT / "LLaVA-NeXT", stage=f"LLaVA captioning {json_path.name}")
    return _latest_created_file(json_path.parent, "*.json", exclude=before)


def _run_deepseek(
    config: VADTreeDependencyConfig,
    *,
    caption_json: Path,
    video_root: Path,
) -> Path:
    before = set(caption_json.parent.rglob(caption_json.name))
    command = [
        config.llm_python,
        str(REPO_ROOT / "DeepSeek-R1" / "deepseek_batch_infer.py"),
        "--video_root",
        str(video_root),
        "--ckpt_dir",
        str(config.llm_model_dir),
        "--video_clip_summary_json",
        str(caption_json),
    ]
    _run_command(command, cwd=REPO_ROOT / "DeepSeek-R1", stage=f"DeepSeek reasoning {caption_json.name}")
    produced = _latest_created_file(caption_json.parent, caption_json.name, exclude=before)
    if produced == caption_json:
        raise RuntimeError(f"DeepSeek did not create a score file for {caption_json.name}.")
    return produced


def _run_imagebind(
    config: VADTreeDependencyConfig,
    *,
    caption_json: Path,
    video_root: Path,
) -> Path:
    target = caption_json.parent / f"sim_{caption_json.name[:-4]}pkl"
    before_exists = target.exists()
    command = [
        config.imagebind_python,
        str(REPO_ROOT / "ImageBind" / "imagebind_sim.py"),
        "--video_summary_json",
        str(caption_json),
        "--video_root",
        str(video_root),
    ]
    _run_command(command, cwd=REPO_ROOT / "ImageBind", stage=f"ImageBind similarity {caption_json.name}")
    if not target.exists() and not before_exists:
        raise RuntimeError(f"ImageBind did not create {target.name}.")
    return target


def _run_refinement(config: VADTreeDependencyConfig, *, scores_json: Path) -> Path:
    output_dir = Path(
        str(scores_json.parent) + f"_VxV{REFINE_TOP_K}_nn{REFINE_NUM_NEIGHBORS}_tao{REFINE_TAO}"
    )
    target = output_dir / f"refine_{scores_json.name}"
    command = [
        config.vadtree_python,
        str(REPO_ROOT / "refinement_eval.py"),
        "--scores_json",
        str(scores_json),
        "--similarity_type",
        "VxV",
        "--topK",
        str(REFINE_TOP_K),
        "--num_neighbors",
        str(REFINE_NUM_NEIGHBORS),
        "--tao",
        str(REFINE_TAO),
        "--without_labels",
    ]
    _run_command(command, cwd=REPO_ROOT, stage=f"Refinement {scores_json.name}")
    if not target.exists():
        raise RuntimeError(f"Refinement did not create {target}.")
    return target


def _correlation_output_path(coarse_scores_json: Path, fine_scores_json: Path) -> Path:
    coarse_parent = coarse_scores_json.parent
    output_dir = Path(str(coarse_parent) + "_ENSE")
    fine_parent_name = os.path.normpath(str(fine_scores_json.parent)).replace(
        "EGEBD_x2x3x4_r50_eff_split_out_th",
        "EX234R50ES",
    )
    fine_parent_name = fine_parent_name.replace("LLaVA-Video-7B-Qwen2", "LV7Q").replace(
        "DeepSeek-R1-Distill-Qwen-14B",
        "DRDQ14",
    )
    suffix = "_".join(Path(fine_parent_name).parts[-3:])
    output_dir = Path(str(output_dir) + f"_{suffix}_beat{CORRELATION_BETA}")
    return output_dir / f"ense_{coarse_scores_json.name}"


def _run_correlation(
    config: VADTreeDependencyConfig,
    *,
    coarse_scores_json: Path,
    fine_scores_json: Path,
) -> Path:
    target = _correlation_output_path(coarse_scores_json, fine_scores_json)
    command = [
        config.vadtree_python,
        str(REPO_ROOT / "correlation_eval.py"),
        "--coarse_scores_json",
        str(coarse_scores_json),
        "--fine_scores_json",
        str(fine_scores_json),
        "--beta",
        str(CORRELATION_BETA),
        "--without_labels",
    ]
    _run_command(command, cwd=REPO_ROOT, stage="Correlation fusion")
    if not target.exists():
        raise RuntimeError(f"Correlation fusion did not create {target}.")
    return target


def _extract_video_entry(mapping: dict[str, Any], video_name: str) -> Any:
    candidates = [video_name, Path(video_name).name, Path(video_name).stem, f"{Path(video_name).stem}.mp4"]
    for candidate in candidates:
        if candidate in mapping:
            return mapping[candidate]
    raise RuntimeError(f"Pipeline output does not contain video '{video_name}'.")


def _merge_interval_maps(*maps: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for current in maps:
        for key, value in current.items():
            merged[str(key)] = value
    return merged


def _guess_source_class(video_name: str, captions: dict[str, Any], reasoning: dict[str, Any]) -> str:
    stem = Path(video_name).stem
    prefix = re.match(r"^([A-Za-z_]+)", stem)
    if prefix:
        return prefix.group(1)

    merged_text = " ".join(str(value) for value in list(captions.values())[:4])
    merged_text += " " + " ".join(str(value) for value in list(reasoning.values())[:4])
    keyword_map = {
        "fight": "Fighting",
        "assault": "Assault",
        "fall": "People_falling",
        "fire": "Fire",
        "robbery": "Robbery",
        "burglary": "Burglary",
        "vandal": "Vandalism",
    }
    lowered = merged_text.lower()
    for keyword, label in keyword_map.items():
        if keyword in lowered:
            return label
    return "RuntimeAnomaly"


class VADTreeRuntimeAdapter:
    def __init__(self, dataset_name: str, job_id: str):
        self.dataset_name = dataset_name
        self.job_id = job_id
        self.dataset_display_name = get_store(dataset_name).config.display_name
        self._config: VADTreeDependencyConfig | None = None

    @property
    def config(self) -> VADTreeDependencyConfig:
        if self._config is None:
            self._config = load_dependency_config()
        return self._config

    def validate(self) -> None:
        _ = self.config

    def analyze_video(
        self,
        *,
        source_path: Path,
        source_label: str,
        video_name: str,
        source_type: str,
        work_suffix: str,
        stage_callback: StageCallback | None = None,
    ) -> VADTreePipelineResult:
        config = self.config
        video_name = _normalize_video_name(video_name)
        source_path = source_path.resolve()
        dataset_token = _dataset_token(self.dataset_name)
        work_dir = OUTPUT_ROOT / "jobs" / self.job_id / f"{dataset_token}_{source_type}_{work_suffix}"
        work_dir.mkdir(parents=True, exist_ok=True)

        _emit_stage(stage_callback, "inspect", 0.02)
        source_fps, total_frames = _inspect_video(source_path)

        _emit_stage(stage_callback, "gebd", 0.12)
        sampled_scores, _frame_indices, gebd_fps, gebd_total_frames = _run_gebd(
            config,
            dataset_name=self.dataset_name,
            video_name=video_name,
            video_path=source_path,
            work_dir=work_dir,
        )

        if gebd_total_frames != total_frames:
            total_frames = gebd_total_frames
        if gebd_fps > 0:
            source_fps = gebd_fps

        _emit_stage(stage_callback, "hgtree", 0.22)
        hgtree_outputs = _build_hgtree_outputs(
            video_name=video_name,
            sampled_scores=sampled_scores,
            fps=source_fps,
            total_frames=total_frames,
            work_dir=work_dir,
        )
        runtime_video_root = _prepare_runtime_video_root(
            video_path=source_path,
            video_name=video_name,
            work_dir=work_dir,
        )

        _emit_stage(stage_callback, "llava_coarse", 0.34)
        coarse_caption_json = _run_llava(
            config,
            json_path=hgtree_outputs["coarse_json"],
            video_root=runtime_video_root,
        )
        _emit_stage(stage_callback, "llava_fine", 0.44)
        fine_caption_json = _run_llava(
            config,
            json_path=hgtree_outputs["fine_json"],
            video_root=runtime_video_root,
        )

        _emit_stage(stage_callback, "deepseek_coarse", 0.56)
        coarse_reason_json = _run_deepseek(config, caption_json=coarse_caption_json, video_root=runtime_video_root)
        _emit_stage(stage_callback, "deepseek_fine", 0.66)
        fine_reason_json = _run_deepseek(config, caption_json=fine_caption_json, video_root=runtime_video_root)

        _emit_stage(stage_callback, "imagebind", 0.74)
        coarse_similarity_pkl = _run_imagebind(config, caption_json=coarse_caption_json, video_root=runtime_video_root)
        fine_similarity_pkl = _run_imagebind(config, caption_json=fine_caption_json, video_root=runtime_video_root)

        _emit_stage(stage_callback, "refine", 0.84)
        coarse_refine_json = _run_refinement(config, scores_json=coarse_reason_json)
        fine_refine_json = _run_refinement(config, scores_json=fine_reason_json)

        _emit_stage(stage_callback, "correlation", 0.94)
        final_scores_json = _run_correlation(
            config,
            coarse_scores_json=coarse_refine_json,
            fine_scores_json=fine_refine_json,
        )

        _emit_stage(stage_callback, "load_outputs", 0.98)
        coarse_caption_payload = json.loads(coarse_caption_json.read_text(encoding="utf-8"))
        fine_caption_payload = json.loads(fine_caption_json.read_text(encoding="utf-8"))
        coarse_reason_payload = json.loads(coarse_reason_json.read_text(encoding="utf-8"))
        fine_reason_payload = json.loads(fine_reason_json.read_text(encoding="utf-8"))
        final_payload = json.loads(final_scores_json.read_text(encoding="utf-8"))

        frame_scores = [float(item) for item in _extract_video_entry(final_payload.get("vid_score", {}), video_name)]
        captions = _merge_interval_maps(
            _extract_video_entry(coarse_caption_payload.get("vid_captions", {}), video_name),
            _extract_video_entry(fine_caption_payload.get("vid_captions", {}), video_name),
        )
        reasoning = _merge_interval_maps(
            _extract_video_entry(coarse_reason_payload.get("vid_score", {}), video_name),
            _extract_video_entry(fine_reason_payload.get("vid_score", {}), video_name),
        )
        source_class = _guess_source_class(video_name, captions, reasoning)

        manifest = {
            "source_video": str(source_path),
            "input_json": str(hgtree_outputs["input_json"]),
            "pred_json": str(hgtree_outputs["pred_json"]),
            "coarse_json": str(hgtree_outputs["coarse_json"]),
            "fine_json": str(hgtree_outputs["fine_json"]),
            "coarse_caption_json": str(coarse_caption_json),
            "fine_caption_json": str(fine_caption_json),
            "coarse_reason_json": str(coarse_reason_json),
            "fine_reason_json": str(fine_reason_json),
            "coarse_similarity_pkl": str(coarse_similarity_pkl),
            "fine_similarity_pkl": str(fine_similarity_pkl),
            "coarse_refine_json": str(coarse_refine_json),
            "fine_refine_json": str(fine_refine_json),
            "final_scores_json": str(final_scores_json),
        }
        (work_dir / "pipeline_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return VADTreePipelineResult(
            dataset_name=self.dataset_name,
            dataset_display_name=self.dataset_display_name,
            video_name=video_name,
            video_stem=Path(video_name).stem,
            video_path=source_path,
            source_fps=round(float(source_fps), 2),
            total_frames=len(frame_scores),
            frame_scores=frame_scores,
            captions=captions,
            reasoning=reasoning,
            source_class=source_class,
            metrics=final_payload.get("dataset_metric", {}),
            work_dir=work_dir,
            artifacts=manifest,
        )
