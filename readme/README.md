# VADTree 目录结构与各包作用说明

本文档用于说明当前仓库的整体目录结构、核心执行流程，以及各个子目录和子包在 VADTree 中承担的职责。

## 1. 仓库整体定位

VADTree 不是一个单独的、标准化安装的 Python 包，而是一个论文复现型工程工作区。它将主项目代码与多个上游模型仓库直接放在同一目录下，通过脚本串联成一条完整的视频异常检测流水线。

整个方法的核心逻辑可以概括为：

1. 用 GEBD 模型提取视频边界，得到候选事件片段。
2. 根据边界分数构建层次化的 HGTree。
3. 用 VLM 对 coarse/fine 节点做视频描述。
4. 用 LLM 对节点描述进行异常推理与打分。
5. 用 ImageBind 计算节点间的视觉/文本相似度。
6. 用相似度对节点分数做细化。
7. 将 coarse/fine 两个粒度的结果做相关性融合，得到最终异常分数。

对应到仓库中的脚本链路如下：

```text
EfficientGEBD/GEBD_split100.py
  -> HGTree_generation.py
  -> LLaVA-NeXT/infer_VAD.py
  -> DeepSeek-R1/deepseek_batch_infer.py
  -> ImageBind/imagebind_sim.py
  -> refinement_eval.py
  -> correlation_eval.py
```

在当前工作区中，这条研究流水线之外还额外叠加了一层演示系统：`campus_demo/`。它基于 `result/` 中已有的帧级分数、caption 和 reasoning 缓存，提供报告页生成、历史记录、上传视频分析、控制台页面和导出能力。因此，这个仓库现在既是论文复现工程，也是一套可直接运行的 demo 工作区。

## 2. 顶层目录结构

```text
VADTree/
├── README.md
├── requirements.txt
├── HGTree_generation.py
├── refinement_eval.py
├── correlation_eval.py
├── assets/
├── campus_demo/
├── campus_demo_outputs/
├── dataset_info/
├── result/
├── src/
├── EfficientGEBD/
├── LLaVA-NeXT/
├── ImageBind/
├── DeepSeek-R1/
└── readme/
```

各目录的大致作用如下：

- `README.md`
  项目的官方说明文档，介绍论文背景、安装方式、7 步实验流程和数据准备方法。
- `requirements.txt`
  根目录依赖，主要覆盖 VADTree 主流程脚本、评估与后处理逻辑，不等价于所有子项目的完整环境。
- `HGTree_generation.py`
  VADTree 方法中的核心脚本，用于从边界结果构建分层树结构。
- `refinement_eval.py`
  使用 ImageBind 相似度对节点异常分数做细化，并完成评估。
- `correlation_eval.py`
  将 coarse/fine 两种粒度的结果做最终融合与评估。
- `assets/`
  论文可视化素材，目前主要是框架图。
- `campus_demo/`
  面向演示和比赛场景的轻量应用层，封装了报告构建、HTTP 服务、上传分析、静态页面和事件编辑导出能力。
- `campus_demo_outputs/`
  `campus_demo/` 的输出目录，保存报告页面、事件导出、剪辑片段、上传文件和历史记录。
- `dataset_info/`
  自带的测试集标注与时间区间注释文件。
- `result/`
  预置实验中间结果和输出目录。
- `src/`
  主项目公共代码层，提供数据结构、评估函数、可视化工具和融合工具。
- `EfficientGEBD/`
  上游 GEBD 子项目，负责事件边界检测。
- `LLaVA-NeXT/`
  上游 VLM 子项目，负责对视频片段做节点级描述。
- `ImageBind/`
  上游多模态特征子项目，负责计算视频节点与文本节点之间的相似度。
- `DeepSeek-R1/`
  轻量封装目录，用于调用 DeepSeek-R1-Distill-Qwen 系列模型对节点描述进行异常推理。

### 2.1 本地大文件准备

为了让新仓库保持轻量，下面这 3 个目录应继续作为“本地准备、Git 不跟踪”的大文件目录：

- `DeepSeek-R1/DeepSeek-R1-Distill-Qwen-7B/`
- `LLaVA-NeXT/LLaVA-Video-7B-Qwen2/`
- `EfficientGEBD/output/`

其中前两个是模型目录，建议直接下载到仓库当前默认使用的本地路径：

```bash
python -m pip install -U huggingface_hub

huggingface-cli download deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --local-dir DeepSeek-R1/DeepSeek-R1-Distill-Qwen-7B

huggingface-cli download lmms-lab/LLaVA-Video-7B-Qwen2 \
  --local-dir LLaVA-NeXT/LLaVA-Video-7B-Qwen2
```

`EfficientGEBD/output/` 在这个仓库里也按“下载项/本地缓存目录”处理，不放进 Git。可参考上游 `EfficientGEBD/README.md` 中给出的 checkpoint bundle 下载链接：

- Google Drive:
  `https://drive.google.com/file/d/1S4M-xnKpjWFGBimcRYzlEDFhDsWQWF_-/view?usp=drive_link`

一个可直接使用的示例流程是：

```bash
python -m pip install -U gdown
gdown --fuzzy "https://drive.google.com/file/d/1S4M-xnKpjWFGBimcRYzlEDFhDsWQWF_-/view?usp=drive_link"
mkdir -p EfficientGEBD/output
unzip /path/to/downloaded_checkpoint_bundle.zip -d EfficientGEBD/output
```

如果压缩包内部已经自带 `output/...` 或 `Kinetics-GEBD/...` 顶层目录，解压时保持原有目录层级即可，不要手动改平。

## 3. 根目录脚本的职责

### 3.1 `HGTree_generation.py`

该脚本是 VADTree 特有方法的核心实现。它主要承担以下工作：

- 从 GEBD 产生的边界分数中提取候选边界点。
- 通过 DFS 风格的递归切分构造层次化事件节点。
- 根据边界置信度把节点划分为 coarse 和 fine 两种粒度。
- 删除被其他节点完全包含的冗余节点。
- 输出 `pred.json`、`dfs_coarse_scenes.json`、`dfs_fine_scenes.json`、`dfs_redundant_scenes.json` 等中间结果。

该文件中的关键函数包括：

- `remove_redundant`
  去掉被其他区间完全覆盖的冗余节点。
- `hierarchical`
  按阈值区分 coarse/fine 节点并做完整性修补。
- `kmeans_two_clusters`
  用 KMeans 将边界分数分成两类。
- `cluster_two_groups`
  用 KMedoids 做分组。
- `calculate_dfs_all_idx`
  按高置信边界递归生成树形区间。
- `fine_completion`
  补齐 fine 节点，保证帧级覆盖完整。

### 3.2 `refinement_eval.py`

该脚本承担“节点分数细化 + 评估”的职责。

输入通常是：

- `DeepSeek-R1` 输出的节点异常分数 JSON。
- `ImageBind` 输出的相似度 `pkl` 文件。

它的主要工作包括：

- 读取数据集标注并恢复逐帧标签。
- 读取节点分数并展开为逐帧分数。
- 利用 `VxV`、`VxT`、`TxT`、`TxV` 等相似度矩阵对异常分数进行邻域加权细化。
- 输出 refined 分数文件。
- 计算视频级和数据集级 ROC AUC、PR AUC 等指标。

### 3.3 `correlation_eval.py`

该脚本用于 coarse/fine 跨粒度融合，是主流程的最后一步。

它的主要工作包括：

- 同时读取 coarse 和 fine 两个粒度的 refined score。
- 计算 coarse 节点内部对应 fine 节点分数的波动情况。
- 根据波动强弱动态调整 coarse/fine 的融合权重。
- 生成最终 ensemble 分数。
- 完成最终评估与可视化。

## 4. `src/` 主项目公共代码层

`src/` 不是模型本体，而是 VADTree 主流程的支撑代码。

目录结构如下：

```text
src/
├── data/
│   └── video_record.py
└── utils/
    ├── ensemble_utils.py
    ├── eval_utils.py
    ├── image_utils.py
    ├── path_utils.py
    ├── plot_utils.py
    ├── sample_utils.py
    ├── torch_utils.py
    ├── vis_utils.py
    └── fonts/
```

### 4.1 `src/data/`

- `video_record.py`
  定义 `VideoRecord`，统一表达一个视频样本的基础信息，包括路径、起止帧、帧数与标签。后续评估阶段会通过它把标注文件与视频内容对应起来。

### 4.2 `src/utils/`

- `eval_utils.py`
  主项目最核心的工具模块之一，负责：
  - 解析时间异常标注。
  - 将区间级分数展开为逐帧分数。
  - 计算 refinement 分数。
  - 计算 ROC AUC、PR AUC。
  - 根据数据集规则判断视频类别。

- `ensemble_utils.py`
  提供 coarse/fine 融合相关的工具函数，包括：
  - 节点分数聚合。
  - 粗细粒度区间匹配。
  - 方差与归一化统计。
  - 动态加权所需的辅助计算。

- `vis_utils.py`
  负责视频可视化，将预测分数、片段划分和文本信息绘制到视频或图像上。

- `plot_utils.py`
  负责画分数曲线与标签曲线。

- `path_utils.py`
  提供断点续跑辅助函数，例如识别哪些视频已处理、哪些视频还未处理。

- `sample_utils.py`
  提供简单的时间采样工具。

- `image_utils.py`
  图片读取与预处理辅助函数。

- `torch_utils.py`
  PyTorch 模型初始化辅助逻辑。

- `fonts/`
  可视化时使用的字体资源。

## 5. `dataset_info/` 标注目录

该目录内置了 3 个数据集的测试集标注信息：

```text
dataset_info/
├── MSAD/
├── ucf_crime/
└── xd_violence/
```

每个数据集目录下通常包含：

- `anomaly_test.txt`
  视频级样本列表和类别信息。
- `Temporal_Anomaly_Annotation_for_Testing_Videos.txt`
  或同义命名文件。
  用于提供时间区间级异常标注。

该目录的意义是：

- 将评估需要的 GT 文件直接随仓库提供。
- 让用户在不额外下载标注文件的情况下完成评估。
- 为 `refinement_eval.py` 和 `correlation_eval.py` 提供统一输入。

## 6. `result/` 中间结果目录

`result/` 存放预生成的实验中间产物和最终输出。当前仓库中已经包含三套数据集的部分结果：

```text
result/
├── MSAD_test/
├── UCF_Crime_test/
└── XD_Violence_test/
```

典型目录层次如下：

1. `EGEBD_xxx/`
   保存边界检测输出，如：
   - `pred_scenes_th0.5.json`
   - `scenes_th0.5.json`

2. `EGEBD_xxx_peak_dfs_xxx/`
   保存 HGTree 结果，如：
   - `pred.json`
   - `dfs_coarse_scenes.json`
   - `dfs_fine_scenes.json`
   - `dfs_redundant_scenes.json`

3. `LLaVA-Video-7B-Qwen2_xxx_prior_q_{coarse|fine}/`
   保存 VLM 节点描述结果，如：
   - `maxf64_xxx.json`

4. 同级的 `sim_*.pkl`
   保存 ImageBind 相似度矩阵和缓存特征。

5. 后续还可继续生成：
   - DeepSeek 推理结果
   - refinement 结果
   - correlation/ensemble 结果

这个目录的意义很重要：

- 它既是输出目录，也是实验缓存目录。
- 仓库作者已经放入一部分中间结果，便于用户跳过前面的重型步骤，直接从 HGTree、refinement 或 ensemble 阶段开始实验。
- 当前 `campus_demo/` 也直接依赖其中的 UCF-Crime 和 MSAD 缓存结果来构建报告和演示页面。

## 6.1 `campus_demo/` 与 `campus_demo_outputs/` 演示链路

这一部分不是论文主流程必需项，但它已经是当前仓库中的重要入口。

`campus_demo/` 的主要文件包括：

- `app.py`
  演示系统主入口，提供以下命令：
  - `list`
  - `build-report`
  - `build-samples`
  - `serve`
- `config.py`
  维护 demo 使用的数据集配置、结果 JSON 路径、默认样例和输出根目录。
- `runtime_pipeline.py`
  定义上传视频/预留 RTSP 输入时的运行时分析流程，负责窗口聚合、事件生成、进度回调和报告落盘。
- `vadtree_backend.py`
  作为 VADTree 结果与 demo 运行时之间的适配层。
- `exporter.py`
  负责生成报告 HTML、事件导出文件、剪辑片段和打包产物。
- `console.html`、`console.js`、`console.css`
  浏览器控制台页面与前端资源。

`campus_demo_outputs/` 主要存放：

- `reports/`
  每个分析任务对应的报告目录，里面有 `index.html`、`events.json`、`analysis.json`、`clips_manifest.json` 等文件。
- `history/`
  演示任务和报告的历史记录。
- `uploads/`
  用户上传的视频源文件。

当前 CLI 入口是：

```bash
python campus_demo/app.py list --dataset ucf
python campus_demo/app.py build-report --dataset ucf --video Fighting047_x264.mp4
python campus_demo/app.py build-samples --dataset ucf
python campus_demo/app.py serve --host 127.0.0.1 --port 8000
```

启动服务后，浏览器入口为：

```text
http://127.0.0.1:8000/campus_demo/console
```

结合代码现状，可以把这一层理解为“把研究型输出封装成可展示、可导出、可回放的比赛演示界面”。

## 7. `assets/` 素材目录

当前该目录内容较少，主要包括：

- `framework.png`
  论文中的方法总览图。

它主要用于 README 展示，不参与主流程计算。

## 8. `EfficientGEBD/` 子项目说明

这是上游的 Generic Event Boundary Detection 项目，被 VADTree 用来提供“通用事件边界”先验。

其内部主要结构如下：

```text
EfficientGEBD/
├── GEBD_split100.py
├── modeling/
├── datasets/
├── config-files/
├── utils/
├── export/
├── EffSoccerNet/
└── README.md
```

各部分作用如下：

- `GEBD_split100.py`
  VADTree 直接调用的入口脚本。
  功能是：
  - 读取视频。
  - 调用 GEBD 模型做边界预测。
  - 将视频切分成候选事件片段。
  - 输出 `pred_scenes_th*.json` 和 `scenes_th*.json`。

- `modeling/`
  GEBD 模型结构定义，包括 backbone、diff former、配置与若干时序建模组件。

- `datasets/`
  数据读取和数据集定义。

- `config-files/`
  训练与推理使用的 YAML 配置文件。

- `utils/`
  分布式训练、评估、数据预处理、GT 生成等辅助代码。

- `export/`
  与推理导出相关的辅助代码。

- `EffSoccerNet/`
  上游项目附带的 benchmark/实验目录，更偏原始工程内容，不是 VADTree 主流程的核心依赖。

该子项目在 VADTree 中的意义是：

- 它不负责异常识别。
- 它只负责先把视频拆成“潜在事件节点”。
- 它为后续构建 HGTree 提供边界分数和初始区间。

## 9. `LLaVA-NeXT/` 子项目说明

这是上游的多模态大模型项目，被 VADTree 用来对 HGTree 中的 coarse/fine 节点进行视频描述。

内部主要结构如下：

```text
LLaVA-NeXT/
├── infer_VAD.py
├── VLM_prompt.py
├── VLM_utils.py
├── llava/
├── docs/
├── scripts/
├── playground/
├── trl/
├── requirements.txt
└── pyproject.toml
```

### 9.1 与 VADTree 强相关的文件

- `infer_VAD.py`
  是 VADTree 接入 LLaVA 的核心脚本。
  主要工作：
  - 读取 `dfs_coarse_scenes.json` 或 `dfs_fine_scenes.json`。
  - 从每个区间采样视频帧。
  - 构造提示词。
  - 调用 LLaVA-Video-7B-Qwen2 生成节点级描述。
  - 输出节点 caption JSON。

- `VLM_prompt.py`
  存放不同数据集使用的先验提示词，例如：
  - `ucf_prior_q`
  - `xd_prior_q`
  - `msad_prior_q`

  这些提示词会显式告诉 VLM：
  - 数据集可能出现哪些异常类别。
  - 应该从场景、人物/物体、行为三个维度理解片段。

- `VLM_utils.py`
  提供参数序列化等轻量辅助函数。

### 9.2 `llava/` 正式模型包

`llava/` 是上游项目真正的代码主体，内部又可大致分为：

- `llava/model/`
  模型构建逻辑，是核心部分。
  其中包括：
  - `language_model/`
    封装不同 LLM 后端，如 Qwen、Llama、Mistral 等。
  - `multimodal_encoder/`
    视觉编码器定义。
  - `multimodal_projector/`
    多模态特征投影层。
  - `multimodal_resampler/`
    用于处理视觉 token 的重采样模块。

- `llava/conversation.py`
  对话模板定义。

- `llava/mm_utils.py`
  多模态输入处理工具。

- `llava/constants.py`
  多模态 token 常量。

- `llava/train/`
  训练代码。

- `llava/serve/`
  服务部署和 Web/CLI 推理代码。

- `llava/eval/`
  上游评测脚本。

### 9.3 该子项目在 VADTree 中的意义

它的定位不是最终打分器，而是“视觉描述器”：

- 输入是 HGTree 的节点区间。
- 输出是每个节点对应的文字描述。
- 这些描述后续会交给 DeepSeek-R1 做异常推理。

## 10. `DeepSeek-R1/` 子目录说明

该目录非常轻，只包含一个核心脚本：

- `deepseek_batch_infer.py`

它并不是完整的 DeepSeek 项目源码，而是 VADTree 自己写的一个调用脚本。其主要作用是：

- 读取 LLaVA 生成的节点描述 JSON。
- 组织上下文提示词。
- 调用兼容的 `DeepSeek-R1-Distill-Qwen` 系列模型。
- 为每个节点输出异常分数或推理结果。

结合当前仓库状态，这里还有两个需要注意的现实细节：

- 现有 released result 和 `campus_demo/config.py` 中引用的目录命名主要基于 `DeepSeek-R1-Distill-Qwen-14B`。
- 但 `deepseek_batch_infer.py` 里示例默认的 `--ckpt_dir` 已经指向 `DeepSeek-R1-Distill-Qwen-7B` 风格路径。

也就是说，这一层实际是“脚本兼容多种 checkpoint，但缓存结果目录名和默认示例路径目前并不完全统一”。

这个目录的工程意义是：

- 将“节点描述”转化为“节点异常评分”。
- 在 VADTree 中承担 LLM reasoning 这一步。
- 让 LLM 负责可解释的异常推断，而不是直接依赖纯视觉分数。

## 11. `ImageBind/` 子项目说明

这是上游的多模态表征模型，用于计算节点之间的相似度关系。

内部主要结构如下：

```text
ImageBind/
├── imagebind_sim.py
├── imagebind/
│   ├── data.py
│   └── models/
├── setup.py
├── requirements.txt
└── README.md
```

### 11.1 与 VADTree 强相关的入口

- `imagebind_sim.py`
  是 VADTree 主流程中直接调用的脚本。
  主要工作：
  - 读取节点 caption JSON。
  - 将文本输入送入 ImageBind 文本编码分支。
  - 将视频片段送入 ImageBind 视觉编码分支。
  - 计算多种相似度矩阵：
    - `VxV`
    - `VxT`
    - `TxV`
    - `TxT`
  - 保存到 `sim_*.pkl` 文件中。

### 11.2 `imagebind/` 正式包

- `imagebind/data.py`
  负责文本和视频的预处理与加载。

- `imagebind/models/imagebind_model.py`
  ImageBind 主模型定义。

- `imagebind/models/transformer.py`
  Transformer 模块实现。

- `imagebind/models/multimodal_preprocessors.py`
  不同模态输入的预处理逻辑。

### 11.3 该子项目在 VADTree 中的意义

ImageBind 在这里不是做分类，而是做“结构约束”：

- 衡量片段之间是否相似。
- 让相似片段的异常分数能够相互影响。
- 给 `refinement_eval.py` 提供 refinement 所需的邻域关系。

## 12. 工程组织方式的特点

从工程角度看，这个仓库采用的是“主项目脚本 + 上游仓库内嵌”的组织方式，而不是严格的模块化单包设计。

这种组织方式的优点是：

- 论文复现流程直观。
- 各阶段入口脚本清晰。
- 可以直接使用上游仓库代码，不需要重新封装。

对应的代价是：

- 环境拆分明显，往往需要多个 conda 环境。
- 一些脚本里仍保留硬编码路径与默认 GPU 配置。
- 项目更偏实验复现，不是高度产品化的代码库。

## 13. 总结

如果从职责划分上看，这个仓库现在更适合理解为 6 个层次：

1. `EfficientGEBD/`
   负责事件边界检测，给出候选节点。
2. `HGTree_generation.py`
   负责构建层次化树结构，是 VADTree 方法本身的核心。
3. `LLaVA-NeXT/` 与 `DeepSeek-R1/`
   分别负责节点描述和节点推理打分。
4. `ImageBind/` 与 `refinement_eval.py`
   负责根据跨模态相似性细化分数。
5. `correlation_eval.py`
   负责 coarse/fine 融合，输出最终结果。
6. `campus_demo/`
   负责把缓存结果和运行时分析能力包装成可直接演示的控制台、报告页和导出系统。

因此，仓库虽然目录较多，但本质上围绕的是一条清晰的研究流水线：

**边界发现 -> 树结构构建 -> 节点描述 -> 节点推理 -> 相似度细化 -> 跨粒度融合。**
