from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DATASETS, DatasetConfig, get_dataset


@dataclass
class LoadedVideo:
    dataset_name: str
    dataset_display_name: str
    video_name: str
    video_stem: str
    source_class: str
    scores: list[float]
    captions: dict[str, str]
    reasoning: dict[str, Any]
    metrics: dict[str, Any]
    fps: float
    video_path: Path | None


def infer_source_class(config: DatasetConfig, video_name: str) -> str:
    stem = Path(video_name).stem
    if stem.startswith("Normal"):
        return "Normal"
    for prefix in config.compound_prefixes:
        if stem.startswith(prefix):
            return prefix
    match = re.match(r"^([A-Za-z]+)", stem)
    return match.group(1) if match else stem


class DatasetStore:
    def __init__(self, config: DatasetConfig):
        self.config = config
        self._score_data: dict[str, Any] | None = None
        self._metric_data: dict[str, Any] | None = None
        self._reason_data: dict[str, Any] | None = None
        self._caption_data: dict[str, Any] | None = None

    def _load_json(self, path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    @property
    def score_data(self) -> dict[str, Any]:
        if self._score_data is None:
            self._score_data = self._load_json(self.config.result_json)
        return self._score_data

    @property
    def metric_data(self) -> dict[str, Any]:
        if self._metric_data is None:
            self._metric_data = self._load_json(self.config.metrics_json)
        return self._metric_data

    @property
    def reason_data(self) -> dict[str, Any]:
        if self._reason_data is None:
            self._reason_data = self._load_json(self.config.reasoning_json)
        return self._reason_data

    @property
    def caption_data(self) -> dict[str, Any]:
        if self._caption_data is None:
            self._caption_data = self._load_json(self.config.caption_json)
        return self._caption_data

    def _normalize_video_name(self, video_name: str) -> str:
        if video_name in self.score_data["vid_score"]:
            return video_name
        if not video_name.endswith(".mp4"):
            mp4_name = f"{video_name}.mp4"
            if mp4_name in self.score_data["vid_score"]:
                return mp4_name
        raise KeyError(f"Video '{video_name}' not found in dataset '{self.config.name}'.")

    def available_videos(self) -> list[str]:
        return sorted(self.score_data["vid_score"])

    def has_local_video(self, video_name: str) -> bool:
        if self.config.video_root is None:
            return False
        return (self.config.video_root / Path(video_name).name).exists()

    def list_videos(self) -> list[dict[str, Any]]:
        default_samples = set(self.default_samples())
        return [
            {
                "name": name,
                "has_local_video": self.has_local_video(name),
                "is_default_sample": name in default_samples,
            }
            for name in self.available_videos()
        ]

    def default_samples(self) -> list[str]:
        available = set(self.available_videos())
        samples = [name for name in self.config.default_samples if name in available]
        if samples:
            return samples
        return self.available_videos()[:5]

    def load_video(self, video_name: str) -> LoadedVideo:
        normalized_name = self._normalize_video_name(video_name)
        video_stem = Path(normalized_name).stem
        scores = self.score_data["vid_score"][normalized_name]
        captions = self.caption_data.get("vid_captions", {}).get(normalized_name, {})
        reasoning = self.reason_data.get("vid_score", {}).get(normalized_name, {})
        metrics = self.metric_data.get("vid_metric", {}).get(video_stem, {})
        video_path = None
        if self.config.video_root is not None:
            candidate = self.config.video_root / normalized_name
            if candidate.exists():
                video_path = candidate
        return LoadedVideo(
            dataset_name=self.config.name,
            dataset_display_name=self.config.display_name,
            video_name=normalized_name,
            video_stem=video_stem,
            source_class=infer_source_class(self.config, normalized_name),
            scores=[float(score) for score in scores],
            captions=captions,
            reasoning=reasoning,
            metrics=metrics,
            fps=self.config.default_fps,
            video_path=video_path,
        )


_STORE_CACHE: dict[str, DatasetStore] = {}


def get_store(dataset_name: str) -> DatasetStore:
    key = dataset_name.lower()
    if key not in _STORE_CACHE:
        _STORE_CACHE[key] = DatasetStore(get_dataset(key))
    return _STORE_CACHE[key]


def available_datasets() -> list[str]:
    return sorted(DATASETS)
