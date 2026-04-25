from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatasetConfig:
    name: str
    display_name: str
    result_json: Path
    metrics_json: Path
    reasoning_json: Path
    caption_json: Path
    video_root: Path | None
    default_fps: float
    normal_classes: tuple[str, ...]
    compound_prefixes: tuple[str, ...] = ()
    default_samples: tuple[str, ...] = ()
    website_samples: tuple[str, ...] = ()


REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = REPO_ROOT / "campus_demo_outputs"
REPORTS_ROOT = OUTPUT_ROOT / "reports"
HISTORY_ROOT = OUTPUT_ROOT / "history"
HISTORY_PATH = HISTORY_ROOT / "history.jsonl"
JOB_HISTORY_PATH = HISTORY_ROOT / "jobs.jsonl"
UPLOADS_ROOT = OUTPUT_ROOT / "uploads"

BASE_ALERT_THRESHOLD = 0.22
BASE_HIGH_ALERT_THRESHOLD = 0.36
MIN_EVENT_SECONDS = 1.0
MERGE_GAP_SECONDS = 1.0
SMOOTH_WINDOW_FRAMES = 21


def _repo_path(*parts: str) -> Path:
    return REPO_ROOT.joinpath(*parts)


UCF_FINAL_DIR = _repo_path(
    "result",
    "UCF_Crime_test",
    "EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4",
    "LLaVA-Video-7B-Qwen2_ucf_prior_q_coarse",
    "DeepSeek-R1-Distill-Qwen-14B_think_VxV10_nn10_tao0.1_ENSE_EX234R50ES0.5_peak_dfs_kmeans_1_0.4_LV7Q_ucf_prior_q_DRDQ14_think_VxV10_nn10_tao0.1_beat0.2",
)

UCF_COARSE_DIR = _repo_path(
    "result",
    "UCF_Crime_test",
    "EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4",
    "LLaVA-Video-7B-Qwen2_ucf_prior_q_coarse",
)

MSAD_FINAL_DIR = _repo_path(
    "result",
    "MSAD_test",
    "EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.3",
    "LLaVA-Video-7B-Qwen2_msad_prior_q_coarse",
    "DeepSeek-R1-Distill-Qwen-14B_think_VxV10_nn10_tao0.1_ENSE_EX234R50ES0.5_peak_dfs_kmeans_1_0.3_LV7Q_msad_prior_q_fine_DRDQ14_think_VxV10_nn10_tao0.1_beat-0.4",
)

MSAD_COARSE_DIR = _repo_path(
    "result",
    "MSAD_test",
    "EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.3",
    "LLaVA-Video-7B-Qwen2_msad_prior_q_coarse",
)

DATASETS: dict[str, DatasetConfig] = {
    "ucf": DatasetConfig(
        name="ucf",
        display_name="UCF-Crime",
        result_json=UCF_FINAL_DIR / "ense_refine_maxf64_ucf_prior_q_Here is a .json",
        metrics_json=UCF_FINAL_DIR / "00vid_metric.json",
        reasoning_json=UCF_COARSE_DIR / "DeepSeek-R1-Distill-Qwen-14B_think" / "maxf64_ucf_prior_q_Here is a .json",
        caption_json=UCF_COARSE_DIR / "maxf64_ucf_prior_q_Here is a .json",
        video_root=_repo_path("UCF_CRIME_TEST_VIDEO_DIR"),
        default_fps=30.0,
        normal_classes=("Normal",),
        compound_prefixes=("Normal_Videos",),
        default_samples=(
            "Normal_Videos_189_x264.mp4",
            "Fighting047_x264.mp4",
            "Assault006_x264.mp4",
            "Robbery048_x264.mp4",
            "Burglary018_x264.mp4",
            "Arson009_x264.mp4",
        ),
        website_samples=(
            "Normal_Videos_189_x264.mp4",
            "Fighting047_x264.mp4",
            "Assault006_x264.mp4",
            "Robbery048_x264.mp4",
            "Burglary018_x264.mp4",
            "Arson009_x264.mp4",
        ),
    ),
    "msad": DatasetConfig(
        name="msad",
        display_name="MSAD",
        result_json=MSAD_FINAL_DIR / "ense_refine_maxf64_msad_prior_q_Below is a.json",
        metrics_json=MSAD_FINAL_DIR / "00vid_metric.json",
        reasoning_json=MSAD_COARSE_DIR / "DeepSeek-R1-Distill-Qwen-14B_think" / "maxf64_msad_prior_q_Below is a.json",
        caption_json=MSAD_COARSE_DIR / "maxf64_msad_prior_q_Below is a.json",
        video_root=None,
        default_fps=25.0,
        normal_classes=("Normal",),
        compound_prefixes=(
            "People_falling",
            "Object_falling",
            "Traffic_accident",
            "Water_incident",
        ),
        default_samples=(
            "Fighting_10",
            "Assault_11",
            "People_falling_10",
            "Vandalism_4",
        ),
    ),
}


CAMPUS_CLASS_MAP: dict[str, dict[str, str]] = {
    "Fighting": {
        "campus_label": "打架斗殴",
        "risk_level": "high",
        "summary": "多人发生明显肢体冲突，属于校园高风险异常行为。",
    },
    "Assault": {
        "campus_label": "单向攻击/疑似欺凌",
        "risk_level": "high",
        "summary": "疑似存在持续攻击、压制或校园欺凌场景，需要立即复核。",
    },
    "Abuse": {
        "campus_label": "异常攻击行为",
        "risk_level": "high",
        "summary": "画面中存在明显攻击性动作，建议尽快人工复核。",
    },
    "People_falling": {
        "campus_label": "人员跌倒/昏厥",
        "risk_level": "high",
        "summary": "人员突然倒地或长时间无法恢复站立，存在人身安全风险。",
    },
    "Vandalism": {
        "campus_label": "破坏公共设施",
        "risk_level": "high",
        "summary": "疑似存在踢打、砸物或破坏校园设备设施的行为。",
    },
    "Robbery": {
        "campus_label": "可疑闯入/财物风险",
        "risk_level": "medium",
        "summary": "存在抢夺、胁迫或明显财物风险行为，需要持续关注。",
    },
    "Burglary": {
        "campus_label": "可疑闯入/财物风险",
        "risk_level": "medium",
        "summary": "存在非常规闯入或异常靠近敏感区域的行为。",
    },
    "Stealing": {
        "campus_label": "可疑闯入/财物风险",
        "risk_level": "medium",
        "summary": "存在财物异常获取或可疑停留行为。",
    },
    "Shoplifting": {
        "campus_label": "财物异常行为",
        "risk_level": "medium",
        "summary": "存在异常取物或财物风险行为。",
    },
    "Arson": {
        "campus_label": "火情/极端危险行为",
        "risk_level": "high",
        "summary": "存在纵火或明显起火风险，属于极高风险事件。",
    },
    "Fire": {
        "campus_label": "火情/极端危险行为",
        "risk_level": "high",
        "summary": "存在明显火焰或火情迹象，需要立即处置。",
    },
    "Explosion": {
        "campus_label": "极端危险事件",
        "risk_level": "high",
        "summary": "出现爆炸或剧烈危险场景，属于极高风险事件。",
    },
    "Object_falling": {
        "campus_label": "高空坠物/物体坠落风险",
        "risk_level": "medium",
        "summary": "存在异常物体坠落，可能威胁校园公共区域安全。",
    },
    "RoadAccidents": {
        "campus_label": "交通风险",
        "risk_level": "medium",
        "summary": "存在交通冲突或车辆碰撞风险。",
    },
    "Traffic_accident": {
        "campus_label": "交通风险",
        "risk_level": "medium",
        "summary": "存在交通碰撞或道路安全风险。",
    },
    "Water_incident": {
        "campus_label": "涉水风险",
        "risk_level": "medium",
        "summary": "存在溺水或涉水危险场景，需要尽快关注。",
    },
    "Normal": {
        "campus_label": "正常行为",
        "risk_level": "low",
        "summary": "当前片段未发现需要预警的异常行为。",
    },
}


def get_dataset(name: str) -> DatasetConfig:
    try:
        return DATASETS[name.lower()]
    except KeyError as exc:
        available = ", ".join(sorted(DATASETS))
        raise ValueError(f"Unknown dataset '{name}'. Available: {available}") from exc
