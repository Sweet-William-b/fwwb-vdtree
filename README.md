<div align="center">

# VADTree: Explainable Training-Free Video Anomaly Detection via Hierarchical Granularity-Aware Tree

[Wenlong Li](), [Yifei Xu](), [Yuan Rao](), [Zhenhua Wang](), [Shuiguang Deng]() <br>


[![Paper](https://img.shields.io/badge/paper-arxiv.2510.22693-B31B1B.svg)](https://arxiv.org/abs/2510.22693)

</div>

<p align="center">
  <img style="width: 100%" src="assets/framework.png">
</p>
<br>

> **Abstract:** Video anomaly detection (VAD) focuses on identifying anomalies in videos. Supervised methods demand substantial in-domain training data and fail to deliver
clear explanations for anomalies. In contrast, training-free methods leverage
the knowledge reserves and language interactivity of large pre-trained models
to detect anomalies. However, the current fixed-length temporal window sampling approaches struggle to accurately capture anomalies with varying temporal
spans. Therefore, we propose VADTree that utilizes a Hierarchical Granularityaware Tree (HGTree) structure for flexible sampling in VAD. VADTree leverages
the knowledge embedded in a pre-trained Generic Event Boundary Detection
(GEBD) model to characterize potential anomaly event boundaries. Specifically,
VADTree decomposes the video into generic event nodes based on boundary
confidence, and performs adaptive coarse-fine hierarchical structuring and redundancy removal to construct the HGTree. Then, the multi-dimensional priors
are injected into the visual language models (VLMs) to enhance the node-wise
anomaly perception, and anomaly reasoning for generic event nodes is achieved
via large language models (LLMs). Finally, an inter-cluster node correlation
method is used to integrate the multi-granularity anomaly scores. Extensive
experiments on three challenging datasets demonstrate that VADTree achieves
state-of-the-art performance in training-free settings while drastically reducing
the number of sampled video segments. 

# Progress
- Future Plan: Clearer code structure and comments.
- [x] `2025-11-30` Experimental results released. 
- [x] `2025-11-27` Code released. 
- [x] `2025-09-19` Paper accepted at NeurIPS 2025. 

# Repository Layout

- `README.md`: project overview, setup notes, and the end-to-end pipeline.
- `readme/README.md`: a Chinese walkthrough of the current repository structure and module responsibilities.
- `result/`: released intermediate outputs so you can skip some heavy stages.
- `campus_demo/`: a lightweight campus-security demo built on top of cached VADTree outputs and runtime adapters.
- `campus_demo_outputs/`: generated reports, clips, uploads, job history, and exported artifacts for the demo.

# Datasets Preparation

Ground-truth annotations for UCF-Crime, XD-Violence (from [LAVAD](https://github.com/lucazanella/lavad)), and MSAD are already included in `dataset_info/`, so no extra annotation download is required.

Only the stages that read raw videos require you to prepare the video files locally. Official download pages:

- UCF-Crime: [link](https://www.crcv.ucf.edu/projects/real-world/)
- XD-Violence: [link](https://roc-ng.github.io/XD-Violence/)
- MSAD: [link](https://msad-dataset.github.io/)

For example, the UCF-Crime test video directory should look like:

```text
UCF_CRIME_TEST_VIDEO_DIR (290 videos)
├── Abuse028_x264.mp4
├── Abuse030_x264.mp4
└── ...
```

# Large Local Assets

To keep this repository lightweight, the following local directories are intentionally excluded from version control and should be prepared after cloning:

- `DeepSeek-R1/DeepSeek-R1-Distill-Qwen-7B/`
- `LLaVA-NeXT/LLaVA-Video-7B-Qwen2/`
- `EfficientGEBD/output/`

If `huggingface_hub` is not installed yet:

```bash
python -m pip install -U huggingface_hub
```

Download the two model directories into the exact local paths expected by the current repo layout:

```bash
huggingface-cli download deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --local-dir DeepSeek-R1/DeepSeek-R1-Distill-Qwen-7B

huggingface-cli download lmms-lab/LLaVA-Video-7B-Qwen2 \
  --local-dir LLaVA-NeXT/LLaVA-Video-7B-Qwen2
```

`EfficientGEBD/output/` is also prepared outside Git. In this repo it is treated as a large local asset directory for EfficientGEBD checkpoints / exported results. The upstream EfficientGEBD release provides a checkpoint bundle here:

- Google Drive: https://drive.google.com/file/d/1S4M-xnKpjWFGBimcRYzlEDFhDsWQWF_-/view?usp=drive_link

Example download flow with `gdown`:

```bash
python -m pip install -U gdown
gdown --fuzzy "https://drive.google.com/file/d/1S4M-xnKpjWFGBimcRYzlEDFhDsWQWF_-/view?usp=drive_link"
mkdir -p EfficientGEBD/output
unzip /path/to/downloaded_checkpoint_bundle.zip -d EfficientGEBD/output
```

If the archive already contains an `output/...` or `Kinetics-GEBD/...` top-level directory, keep that internal structure when extracting.

# Install

### 1. Clone the repository and install the root environment

```bash
git clone https://github.com/wenlongli10/VADTree.git
cd VADTree
conda create --name VADTree python=3.10
conda activate VADTree
pip install -r requirements.txt
```

The root environment mainly covers `HGTree_generation.py`, `ImageBind/imagebind_sim.py`, `refinement_eval.py`, `correlation_eval.py`, and the local demo utilities. It does not replace the full environments required by the upstream subprojects.

### 2. Install EfficientGEBD and prepare GEBD weights

Follow the instructions in [EfficientGEBD](https://github.com/Ziwei-Zheng/EfficientGEBD).

`EfficientGEBD/output/` is intentionally not tracked in this repository. For this project, treat it as a large local download/cache directory for EfficientGEBD checkpoints or exported results.

Upstream download reference from `EfficientGEBD/README.md`:

- checkpoint bundle: https://drive.google.com/file/d/1S4M-xnKpjWFGBimcRYzlEDFhDsWQWF_-/view?usp=drive_link

Example:

```bash
python -m pip install -U gdown
gdown --fuzzy "https://drive.google.com/file/d/1S4M-xnKpjWFGBimcRYzlEDFhDsWQWF_-/view?usp=drive_link"
mkdir -p EfficientGEBD/output
unzip /path/to/downloaded_checkpoint_bundle.zip -d EfficientGEBD/output
```

### 3. Install LLaVA-Video-7B-Qwen2 and prepare VLM weights

Follow the instructions in [LLaVA-NeXT](https://github.com/LLaVA-VL/LLaVA-NeXT) to install the environment.

- LLaVA-Video-7B-Qwen2 checkpoint: [huggingface](https://huggingface.co/lmms-lab/LLaVA-Video-7B-Qwen2)

Recommended local download command:

```bash
huggingface-cli download lmms-lab/LLaVA-Video-7B-Qwen2 \
  --local-dir LLaVA-NeXT/LLaVA-Video-7B-Qwen2
```

### 4. Prepare DeepSeek-R1-Distill-Qwen checkpoints

You can reuse the LLaVA environment for `DeepSeek-R1/deepseek_batch_infer.py`.

- DeepSeek-R1-Distill-Qwen-14B checkpoint: [huggingface](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-14B)
- DeepSeek-R1-Distill-Qwen-7B checkpoint: [huggingface](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B)

Recommended local download command for the current default path:

```bash
huggingface-cli download deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --local-dir DeepSeek-R1/DeepSeek-R1-Distill-Qwen-7B
```

Notes:

- The released cached results in `result/` and the current `campus_demo` config use `DeepSeek-R1-Distill-Qwen-14B` naming.
- `DeepSeek-R1/deepseek_batch_infer.py` currently uses a `DeepSeek-R1-Distill-Qwen-7B` example path as its default `--ckpt_dir`.
- Keep your `--ckpt_dir` and all downstream folder references consistent with the checkpoint you actually run.

# Pipeline Quick Start (UCF-Crime)

The examples below use UCF-Crime. Paths for XD-Violence and MSAD follow the same pattern.

### 1. GEBD boundary extraction

Configure the EfficientGEBD environment, GEBD checkpoint, and config file first.

```bash
conda activate EfficientGEBD
cd EfficientGEBD
python GEBD_split100.py \
  --video_dir /path/to/UCF_CRIME_TEST_VIDEO_DIR \
  --resume /path/to/GEBD_MODEL_WEIGHT \
  --config-file /path/to/MODEL_CONFIG
```

Typical output:

```text
VADTree/result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5
├── pred_scenes_th0.5.json
└── scenes_th0.5.json
```

### 2. Build HGTree

```bash
cd ..
conda activate VADTree
python HGTree_generation.py \
  --json_path ./result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5/pred_scenes_th0.5.json \
  --threshold kmeans \
  --gamma 0.4
```

Typical output:

```text
VADTree/result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4
├── pred.json
├── dfs_coarse_scenes.json
├── dfs_fine_scenes.json
└── dfs_redundant_scenes.json
```

### 3. Node-wise VLM captioning (coarse and fine)

Configure the LLaVA environment and checkpoint first.

```bash
conda activate llava
cd LLaVA-NeXT
python infer_VAD.py \
  --pretrained /path/to/LLaVA-Video-7B-Qwen2 \
  --video_root /path/to/UCF_CRIME_TEST_VIDEO_DIR \
  --json_path ../result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/dfs_coarse_scenes.json

python infer_VAD.py \
  --pretrained /path/to/LLaVA-Video-7B-Qwen2 \
  --video_root /path/to/UCF_CRIME_TEST_VIDEO_DIR \
  --json_path ../result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/dfs_fine_scenes.json
```

Typical output:

```text
VADTree/result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/
└── LLaVA-Video-7B-Qwen2_ucf_prior_q_{coarse|fine}/
    └── maxf64_ucf_prior_q_*.json
```

### 4. Node-wise LLM reasoning (coarse and fine)

`deepseek_batch_infer.py` takes the VLM caption JSON via `--video_clip_summary_json`.

```bash
cd ../DeepSeek-R1
python deepseek_batch_infer.py \
  --ckpt_dir /path/to/DeepSeek-R1-Distill-Qwen-14B \
  --video_root /path/to/UCF_CRIME_TEST_VIDEO_DIR \
  --video_clip_summary_json "../result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/LLaVA-Video-7B-Qwen2_ucf_prior_q_coarse/maxf64_ucf_prior_q_Here is a .json"

python deepseek_batch_infer.py \
  --ckpt_dir /path/to/DeepSeek-R1-Distill-Qwen-14B \
  --video_root /path/to/UCF_CRIME_TEST_VIDEO_DIR \
  --video_clip_summary_json "../result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/LLaVA-Video-7B-Qwen2_ucf_prior_q_fine/maxf64_ucf_prior_q_Here is a .json"
```

Reasoning outputs are written back under each caption directory in an auto-generated subdirectory derived from the checkpoint name and prompt settings.

### 5. Feature similarity (coarse and fine)

```bash
conda activate VADTree
cd ../ImageBind
python imagebind_sim.py \
  --video_summary_json "../result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/LLaVA-Video-7B-Qwen2_ucf_prior_q_coarse/maxf64_ucf_prior_q_Here is a .json" \
  --video_root /path/to/UCF_CRIME_TEST_VIDEO_DIR

python imagebind_sim.py \
  --video_summary_json "../result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/LLaVA-Video-7B-Qwen2_ucf_prior_q_fine/maxf64_ucf_prior_q_Here is a .json" \
  --video_root /path/to/UCF_CRIME_TEST_VIDEO_DIR
```

Typical output:

```text
VADTree/result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/
└── LLaVA-Video-7B-Qwen2_ucf_prior_q_{coarse|fine}/
    └── sim_maxf64_ucf_prior_q_*.pkl
```

### 6. Intra-cluster refinement and evaluation

`refinement_eval.py` expects `--scores_json` rather than `--score_json`.

```bash
cd ..
python refinement_eval.py \
  --scores_json "result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/LLaVA-Video-7B-Qwen2_ucf_prior_q_coarse/<REASONING_DIR>/maxf64_ucf_prior_q_Here is a .json"

python refinement_eval.py \
  --scores_json "result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/LLaVA-Video-7B-Qwen2_ucf_prior_q_fine/<REASONING_DIR>/maxf64_ucf_prior_q_Here is a .json"
```

The script auto-loads the matching `sim_*.pkl` file and writes `refine_*.json` into a new output directory named by the similarity settings.

### 7. Inter-cluster correlation and final evaluation

```bash
python correlation_eval.py \
  --coarse_scores_json "result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/LLaVA-Video-7B-Qwen2_ucf_prior_q_coarse/<REFINE_DIR>/refine_maxf64_ucf_prior_q_Here is a .json" \
  --fine_scores_json "result/UCF_Crime_test/EGEBD_x2x3x4_r50_eff_split_out_th0.5_peak_dfs_kmeans_1_0.4/LLaVA-Video-7B-Qwen2_ucf_prior_q_fine/<REFINE_DIR>/refine_maxf64_ucf_prior_q_Here is a .json" \
  --beta 0.2
```

`correlation_eval.py` produces the final ensemble scores and evaluation metrics in a new output directory under the coarse branch.

# Campus Demo

This repository now also includes a lightweight demo system in `campus_demo/` for offline report generation and browser-based inspection on top of cached VADTree outputs.

Supported built-in datasets in the current demo config:

- `ucf`
- `msad`

Useful commands:

```bash
python campus_demo/app.py list --dataset ucf
python campus_demo/app.py build-report --dataset ucf --video Fighting047_x264.mp4
python campus_demo/app.py build-samples --dataset ucf
python campus_demo/app.py serve --host 127.0.0.1 --port 8000
```

After `serve`, open:

```text
http://127.0.0.1:8000/campus_demo/console
```

Notes:

- Generated reports, clips, uploads, bundles, and history are stored in `campus_demo_outputs/`.
- Upload analysis goes through the runtime adapter in `campus_demo/runtime_pipeline.py`.
- The RTSP API surface is present in the demo code, but the current demo environment does not connect it to live-stream inference yet.

# Citation

Please consider citing our paper in your publications if the project helps your research.
```bibtex
@inproceedings{li2025vadtree,
  title={VADTree: Explainable Training-Free Video Anomaly Detection via Hierarchical Granularity-Aware Tree},
  author={Li, Wenlong and Xu, Yifei and Rao, Yuan and Wang, Zhenhua and Deng, Shuiguang},
  booktitle={The Thirty-ninth Annual Conference on Neural Information Processing Systems},
  year={2025}
}
```

# Acknowledgements

This repository builds upon the [LAVAD](https://github.com/lucazanella/lavad). Thanks to the authors!
