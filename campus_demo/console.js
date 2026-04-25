window.__newConsoleApp = true;

const CONSOLE_TEMPLATE = `
  <div class="console-shell">
    <header class="panel masthead">
      <div class="masthead-main">
        <div class="eyebrow">A28 Campus Security Command Center</div>
        <h1>校园安防视频行为预警控制台</h1>
        <p class="lead">
          面向比赛演示的统一前端入口，串联“样例回放 / 本地上传 / RTSP 接入、任务分析、实时预警、人工复核、导出留档、历史回看、训练测评占位”整条闭环。
        </p>
        <div class="story-rail">
          <article class="story-card">
            <span>01</span>
            <strong>源接入</strong>
            <p>选择缓存样例、导入本地视频，或预留 RTSP 流。</p>
          </article>
          <article class="story-card">
            <span>02</span>
            <strong>任务分析</strong>
            <p>创建任务后立即进入排队、运行、复核或完成态。</p>
          </article>
          <article class="story-card">
            <span>03</span>
            <strong>风险预警</strong>
            <p>实时显示事件数量、风险等级、性能指标和最近告警。</p>
          </article>
          <article class="story-card">
            <span>04</span>
            <strong>人工复核</strong>
            <p>在线编辑行为标签、风险等级、track、备注和误报状态。</p>
          </article>
          <article class="story-card">
            <span>05</span>
            <strong>导出归档</strong>
            <p>一键导出 JSON、CSV、异常切片与报告整包。</p>
          </article>
        </div>
      </div>

      <div class="masthead-side">
        <div class="status-pod">
          <div class="pod-label">服务健康</div>
          <div id="health-value" class="pod-value">检查中...</div>
          <div id="health-meta" class="pod-meta">等待后端返回状态</div>
        </div>

        <div class="hero-grid">
          <div class="hero-stat">
            <span>数据集</span>
            <strong id="overview-datasets">0</strong>
          </div>
          <div class="hero-stat">
            <span>历史任务</span>
            <strong id="overview-history">0</strong>
          </div>
          <div class="hero-stat">
            <span>当前模式</span>
            <strong id="overview-mode">样例回放</strong>
          </div>
          <div class="hero-stat">
            <span>活跃预警</span>
            <strong id="overview-alerts">0</strong>
          </div>
        </div>

        <div class="mission-card">
          <div class="mission-title">当前输入策略</div>
          <div id="mode-summary" class="mission-copy">
            直接使用仓库现有缓存结果，最快完成比赛演示闭环。
          </div>
          <div class="hero-links">
            <a class="ghost-link" href="/campus_demo_outputs/index.html" target="_blank">打开静态历史页</a>
            <a class="ghost-link" href="#events-panel">跳到复核日志</a>
          </div>
        </div>
      </div>
    </header>

    <nav class="panel section-nav">
      <a href="#source-panel">源接入</a>
      <a href="#runtime-panel">运行态</a>
      <a href="#events-panel">复核日志</a>
      <a href="#export-panel">导出中心</a>
      <a href="#history-panel">历史回看</a>
      <a href="#ops-panel">训练测评</a>
    </nav>

    <main class="workspace">
      <aside class="workspace-side">
        <section id="source-panel" class="panel">
          <div class="panel-head">
            <div>
              <div class="panel-kicker">Source Deck</div>
              <h2>输入源与任务创建</h2>
            </div>
            <div class="panel-note">三种模式共用一套任务、日志、报告和导出闭环。</div>
          </div>

          <div class="mode-switch">
            <button data-mode="sample" class="active">样例回放</button>
            <button data-mode="upload">本地上传</button>
            <button data-mode="rtsp">RTSP 在线流</button>
          </div>

          <div class="field">
            <label for="dataset-select">数据集</label>
            <select id="dataset-select"></select>
          </div>

          <div id="sample-fields">
            <div class="field">
              <label for="video-select">样例视频</label>
              <select id="video-select"></select>
            </div>
            <div id="sample-hint" class="field-note">样例列表加载中...</div>
            <div id="sample-gallery" class="sample-gallery hidden"></div>
          </div>

          <div id="upload-fields" class="hidden">
            <div class="upload-box">
              <div class="field">
                <label for="upload-input">上传本地视频</label>
                <input id="upload-input" type="file" accept="video/*" />
              </div>
              <div id="upload-status" class="field-note">
                尚未上传文件。上传任务会调用真实 VADTree 适配链路；若模型依赖未配置，任务会明确失败并返回原因。
              </div>
            </div>
          </div>

          <div id="rtsp-fields" class="hidden">
            <div class="field">
              <label for="rtsp-input">RTSP 地址</label>
              <input id="rtsp-input" type="text" placeholder="rtsp://demo-camera/live" />
            </div>
            <div class="field-note">
              流式任务会先分段聚合，再在停止后执行最终一致化分析并落盘报告。
            </div>
          </div>

          <div class="action-row">
            <button id="create-job-btn" class="primary-btn">开始分析</button>
            <button id="cancel-job-btn" class="danger-btn">取消任务</button>
            <a id="report-link" class="ghost-btn hidden" href="#" target="_blank">打开报告页</a>
          </div>

          <div id="source-status" class="status-bar">等待创建任务。</div>
        </section>

        <section id="export-panel" class="panel">
          <div class="panel-head">
            <div>
              <div class="panel-kicker">Export Hub</div>
              <h2>导出中心</h2>
            </div>
            <div class="panel-note">输出的都是当前复核版本，可直接用于展示和留档。</div>
          </div>

          <div class="export-grid">
            <button class="secondary-btn export-btn" data-kind="json">导出 JSON</button>
            <button class="secondary-btn export-btn" data-kind="csv">导出 CSV</button>
            <button class="secondary-btn export-btn" data-kind="clips">导出异常切片</button>
            <button class="primary-btn export-btn" data-kind="zip">导出 ZIP</button>
          </div>

          <div id="export-status" class="status-bar">暂无导出记录。</div>
          <div id="artifact-list" class="artifact-grid">
            <article class="artifact-card muted-card">暂无导出产物。</article>
          </div>
        </section>

        <section id="ops-panel" class="panel">
          <div class="panel-head">
            <div>
              <div class="panel-kicker">Ops Tools</div>
              <h2>训练与测评占位</h2>
            </div>
            <div class="panel-note">比赛演示环境走轻量接口，用于串联完整工程前端。</div>
          </div>

          <div class="ops-stack">
            <div class="ops-card">
              <div class="field">
                <label for="train-recipe">训练配方 ID</label>
                <input id="train-recipe" type="text" placeholder="demo-recipe" />
              </div>
              <button id="train-btn" class="ghost-btn">触发训练任务</button>
              <div id="train-status" class="status-inline">尚未触发训练任务。</div>
            </div>

            <div class="ops-card">
              <div class="field-note">默认使用当前选择的数据集触发演示评测。</div>
              <button id="eval-btn" class="ghost-btn">触发测评</button>
              <div id="eval-status" class="status-inline">尚未触发测评。</div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <div>
              <div class="panel-kicker">Quick Links</div>
              <h2>报告与产物跳转</h2>
            </div>
            <div class="panel-note">当前任务完成后，这里会直接给出报告页和输出目录。</div>
          </div>

          <div class="quick-grid">
            <article class="quick-card">
              <span>当前报告页</span>
              <div id="quick-report" class="quick-copy">尚未生成报告。</div>
            </article>
            <article class="quick-card">
              <span>当前输出目录</span>
              <div id="quick-output" class="quick-copy">尚未生成输出目录。</div>
            </article>
          </div>
        </section>
      </aside>

      <section class="workspace-main">
        <section id="runtime-panel" class="panel live-panel">
          <div class="panel-head panel-head-spread">
            <div>
              <div class="panel-kicker">Runtime Deck</div>
              <h2>运行态与风险态势</h2>
            </div>
            <div class="runtime-badges">
              <div id="job-badge" class="badge status-queued">未启动</div>
            </div>
          </div>

          <div class="insight-grid">
            <article class="insight-card">
              <span>任务状态</span>
              <strong id="metric-status">待机</strong>
            </article>
            <article class="insight-card">
              <span>事件数量</span>
              <strong id="metric-events">0</strong>
            </article>
            <article class="insight-card">
              <span>当前 FPS</span>
              <strong id="metric-fps">0.00</strong>
            </article>
            <article class="insight-card">
              <span>当前延迟</span>
              <strong id="metric-latency">0 ms</strong>
            </article>
          </div>

          <div class="preview-layout">
            <div class="preview-card">
              <div class="preview-header">
                <div>
                  <div class="preview-title">视频预览</div>
                  <div id="current-job-label" class="preview-copy">当前没有激活任务。</div>
                </div>
                <div class="legend-row">
                  <span class="legend-chip low">LOW</span>
                  <span class="legend-chip review">REVIEW</span>
                  <span class="legend-chip medium">MEDIUM</span>
                  <span class="legend-chip high">HIGH</span>
                </div>
              </div>
              <div id="preview-wrap" class="placeholder">
                选择样例、上传视频或接入 RTSP 后开始分析。<br />
                缓存样例可在没有原始视频的情况下仍完整展示报告与日志闭环。
              </div>
            </div>

            <div class="runtime-stack">
              <article class="progress-card">
                <div class="progress-meta">
                  <span>分析进度</span>
                  <span id="progress-value">0%</span>
                </div>
                <div class="progress-bar"><span id="progress-bar"></span></div>
                <div class="chip-list">
                  <div class="chip" id="summary-source">输入源：未选择</div>
                  <div class="chip" id="summary-video">视频：未选择</div>
                  <div class="chip" id="summary-time">时间点：0.00s</div>
                  <div class="chip" id="summary-runtime">窗口：5 帧段 / 10 帧窗</div>
                  <div class="chip" id="summary-stream">运行态：待机</div>
                </div>
              </article>

              <article class="alert-rail">
                <div class="alert-rail-head">
                  <strong>最近预警</strong>
                  <span>滚动刷新最近 5 条事件</span>
                </div>
                <div id="latest-alerts" class="alert-list">
                  <article class="alert-card muted-card">
                    <strong>暂无预警</strong>
                    <p>任务开始运行后，最近告警会出现在这里。</p>
                  </article>
                </div>
              </article>
            </div>
          </div>
        </section>

        <div class="review-layout">
          <section id="events-panel" class="panel">
            <div class="panel-head">
              <div>
                <div class="panel-kicker">Review Board</div>
                <h2>预警日志与人工复核</h2>
              </div>
              <div class="panel-note">点击时间可跳转视频时间点；原始版只读，当前版可编辑后落盘。</div>
            </div>

            <div class="toolbar">
              <div class="toggle">
                <button id="version-current" class="active">当前版</button>
                <button id="version-original">原始版</button>
              </div>
              <button id="reload-events-btn" class="ghost-btn">刷新日志</button>
              <button id="save-events-btn" class="primary-btn">保存当前修改</button>
            </div>

            <div id="events-status" class="status-bar">等待任务生成日志。</div>

            <div class="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>行为 / Track</th>
                    <th>风险</th>
                    <th>复核</th>
                    <th>置信度</th>
                    <th>备注</th>
                    <th>原因说明</th>
                    <th>切片</th>
                  </tr>
                </thead>
                <tbody id="events-body">
                  <tr><td colspan="8">当前没有日志记录。</td></tr>
                </tbody>
              </table>
            </div>
          </section>

          <aside id="history-panel" class="panel history-panel">
            <div class="panel-head">
              <div>
                <div class="panel-kicker">Replay Stack</div>
                <h2>历史任务与回看</h2>
              </div>
              <div id="history-summary" class="panel-note">历史记录加载中...</div>
            </div>

            <div id="history-list" class="history-grid">
              <article class="history-card muted-card">历史记录加载中...</article>
            </div>
          </aside>
        </div>
      </section>
    </main>
  </div>
`;

if (!document.body.dataset.consoleMounted) {
  document.body.dataset.consoleMounted = "true";
  document.body.innerHTML = CONSOLE_TEMPLATE;
}

const state = {
  mode: "sample",
  datasets: [],
  videos: [],
  currentJobId: null,
  currentVersion: "current",
  uploadedSource: null,
  eventSource: null,
  health: null,
  historyItems: [],
  trainJob: null,
  evalJob: null,
};

const modeMeta = {
  sample: {
    label: "样例回放",
    summary: "直接使用仓库现有缓存结果，最快完成比赛演示闭环。",
    action: "开始分析",
    cancel: "取消任务",
  },
  upload: {
    label: "本地上传",
    summary: "上传真实本地视频并调用运行时链路，生成可复核、可导出的完整报告。",
    action: "上传后分析",
    cancel: "取消任务",
  },
  rtsp: {
    label: "RTSP 在线流",
    summary: "接入在线流做分段聚合，停止后落盘最终报告，适合现场展示准实时能力。",
    action: "接入并分析",
    cancel: "停止采集",
  },
};

const reviewLabel = {
  pending: "待复核",
  confirmed: "已确认",
  false_positive: "误报",
};

const riskLabel = {
  low: "低风险",
  review: "待确认",
  medium: "中风险",
  high: "高风险",
};

const datasetSelect = document.getElementById("dataset-select");
const videoSelect = document.getElementById("video-select");
const sampleHint = document.getElementById("sample-hint");
const sampleGallery = document.getElementById("sample-gallery");
const uploadInput = document.getElementById("upload-input");
const uploadStatus = document.getElementById("upload-status");
const sourceStatus = document.getElementById("source-status");
const exportStatus = document.getElementById("export-status");
const eventsStatus = document.getElementById("events-status");
const artifactList = document.getElementById("artifact-list");
const historyList = document.getElementById("history-list");
const historySummary = document.getElementById("history-summary");
const reportLink = document.getElementById("report-link");
const quickReport = document.getElementById("quick-report");
const quickOutput = document.getElementById("quick-output");
const previewWrap = document.getElementById("preview-wrap");
const latestAlerts = document.getElementById("latest-alerts");
const eventsBody = document.getElementById("events-body");
const cancelJobBtn = document.getElementById("cancel-job-btn");
const createJobBtn = document.getElementById("create-job-btn");
const trainStatus = document.getElementById("train-status");
const evalStatus = document.getElementById("eval-status");
const currentJobLabel = document.getElementById("current-job-label");

function formatSeconds(value) {
  return `${Number(value || 0).toFixed(2)}s`;
}

function setStatus(target, text) {
  target.textContent = text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function statusText(status) {
  const map = {
    queued: "排队中",
    running: "分析中",
    reviewable: "待复核",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return map[status] || status || "未启动";
}

function streamStateText(stateValue) {
  const map = {
    connecting: "连接中",
    buffering: "缓冲中",
    running: "流处理中",
    reconnecting: "重连中",
    stopped: "已停止",
    failed: "流失败",
  };
  return map[stateValue] || (stateValue || "无");
}

function riskClass(level) {
  return `risk-${level || "review"}`;
}

function reviewClass(level) {
  return `review-${level || "pending"}`;
}

async function fetchJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

async function patchJson(url, payload) {
  const response = await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "请求失败");
  }
  return data;
}

function updateOverviewStats(activeAlertCount) {
  document.getElementById("overview-datasets").textContent = String(state.health?.dataset_count || state.datasets.length || 0);
  document.getElementById("overview-history").textContent = String(state.historyItems.length || 0);
  document.getElementById("overview-mode").textContent = modeMeta[state.mode].label;
  document.getElementById("overview-alerts").textContent = String(activeAlertCount ?? 0);
  document.getElementById("mode-summary").textContent = modeMeta[state.mode].summary;
}

function renderBadge(status) {
  const badge = document.getElementById("job-badge");
  badge.className = `badge status-${status || "queued"}`;
  badge.textContent = statusText(status);
}

function renderPreview(job) {
  const previewHref = job.preview_href;
  if (previewHref) {
    previewWrap.innerHTML = `
      <video id="job-video" controls preload="metadata" src="${escapeHtml(previewHref)}"></video>
    `;
    return;
  }
  previewWrap.innerHTML = `
    <div class="placeholder">
      当前输入源没有可直接回放的本地视频。<br />
      这不会影响分析、日志编辑、导出和历史回看演示。
    </div>
  `;
}

function renderArtifacts(artifacts) {
  if (!artifacts || !artifacts.length) {
    artifactList.innerHTML = '<article class="artifact-card muted-card">暂无导出产物。</article>';
    return;
  }
  artifactList.innerHTML = artifacts.map((item) => `
    <article class="artifact-card">
      <strong>${escapeHtml(item.label || item.kind)}</strong>
      <div class="mini">${escapeHtml(item.path || "")}</div>
      <div class="artifact-actions">
        ${item.href ? `<a class="inline-link" href="${escapeHtml(item.href)}" target="_blank">打开 / 下载</a>` : ""}
      </div>
    </article>
  `).join("");
}

function renderLatestAlerts(events) {
  if (!events || !events.length) {
    latestAlerts.innerHTML = `
      <article class="alert-card muted-card">
        <strong>暂无预警</strong>
        <p>任务开始运行后，最近告警会出现在这里。</p>
      </article>
    `;
    return;
  }
  latestAlerts.innerHTML = events.slice(-5).reverse().map((event) => `
    <article class="alert-card ${escapeHtml(event.risk_level || "review")}">
      <strong>${escapeHtml(event.behavior_type || "待确认事件")}</strong>
      <div class="history-meta">
        <span class="${riskClass(event.risk_level)}">${escapeHtml(riskLabel[event.risk_level] || event.risk_level || "待确认")}</span>
        · ${formatSeconds(event.start_sec)} - ${formatSeconds(event.end_sec)}
        · ${reviewLabel[event.review_status] || event.review_status || "待复核"}
      </div>
      <div class="mini">置信度 ${Number(event.confidence || 0).toFixed(3)} · track ${escapeHtml((event.track_ids || []).join(", ") || event.event_id)}</div>
    </article>
  `).join("");
}

function renderJob(job) {
  if (!job) {
    renderBadge("queued");
    document.getElementById("metric-status").textContent = "待机";
    document.getElementById("metric-events").textContent = "0";
    document.getElementById("metric-fps").textContent = "0.00";
    document.getElementById("metric-latency").textContent = "0 ms";
    document.getElementById("progress-value").textContent = "0%";
    document.getElementById("progress-bar").style.width = "0%";
    document.getElementById("summary-source").textContent = "输入源：未选择";
    document.getElementById("summary-video").textContent = "视频：未选择";
    document.getElementById("summary-time").textContent = "时间点：0.00s";
    document.getElementById("summary-runtime").textContent = "窗口：5 帧段 / 10 帧窗";
    document.getElementById("summary-stream").textContent = "运行态：待机";
    currentJobLabel.textContent = "当前没有激活任务。";
    quickReport.textContent = "尚未生成报告。";
    quickOutput.textContent = "尚未生成输出目录。";
    reportLink.classList.add("hidden");
    renderPreview({ preview_href: null });
    renderLatestAlerts([]);
    renderArtifacts([]);
    updateOverviewStats(0);
    return;
  }

  renderBadge(job.status);
  const isStreaming = job.progress_mode === "indeterminate" && ["queued", "running"].includes(job.status);
  const processingFps = Number(job.processing_fps ?? job.current_fps ?? 0);
  const progressPercent = `${Math.round((Number(job.progress || 0)) * 100)}%`;

  document.getElementById("metric-status").textContent = isStreaming
    ? `${statusText(job.status)} / ${streamStateText(job.stream_state)}`
    : statusText(job.status);
  document.getElementById("metric-events").textContent = String(job.event_count || 0);
  document.getElementById("metric-fps").textContent = processingFps.toFixed(2);
  document.getElementById("metric-latency").textContent = `${Number(job.latency_ms || 0).toFixed(0)} ms`;
  document.getElementById("progress-value").textContent = isStreaming ? "流式任务" : progressPercent;
  document.getElementById("progress-bar").style.width = isStreaming
    ? "36%"
    : `${Math.max(0, Math.min(100, Number(job.progress || 0) * 100))}%`;
  document.getElementById("summary-source").textContent = `输入源：${job.source_label || "未命名输入"}`;
  document.getElementById("summary-video").textContent = `视频：${job.video_name || "-"}`;
  document.getElementById("summary-time").textContent = `时间点：${formatSeconds(job.current_sec || 0)}`;
  document.getElementById("summary-runtime").textContent =
    `窗口：${job.segment_frames || 5} 帧段 / ${job.window_frames || 10} 帧窗 · 分段 ${job.processed_segments || 0} · 窗口 ${job.analyzed_windows || 0} · 缓冲 ${job.buffered_segments || 0}`;
  document.getElementById("summary-stream").textContent =
    `运行态：${streamStateText(job.stream_state)} · 模式 ${job.progress_mode === "indeterminate" ? "流式" : "确定进度"} · 源 FPS ${Number(job.source_fps || 0).toFixed(2)}`;
  currentJobLabel.textContent = `当前任务：${job.job_id} · ${job.video_name || job.source_label || "未命名输入"}`;
  quickReport.innerHTML = job.report_href
    ? `<a href="/campus_demo_outputs/${escapeHtml(job.report_href)}" target="_blank">${escapeHtml(job.report_href)}</a>`
    : "尚未生成报告。";
  quickOutput.textContent = job.output_dir || "尚未生成输出目录。";

  if (job.report_href) {
    reportLink.href = `/campus_demo_outputs/${job.report_href}`;
    reportLink.classList.remove("hidden");
  } else {
    reportLink.classList.add("hidden");
  }

  renderPreview(job);
  renderLatestAlerts(job.latest_alerts || []);
  renderArtifacts(job.exports || []);
  setStatus(sourceStatus, job.error ? `任务异常：${job.error}` : `当前任务：${statusText(job.status)}，创建于 ${job.created_at}`);
  updateOverviewStats(job.event_count || 0);
}

function closeStream() {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}

function openStream(jobId) {
  closeStream();
  state.eventSource = new EventSource(`/campus_demo/api/jobs/${encodeURIComponent(jobId)}/stream`);
  state.eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    renderJob(data.job);
    if (state.currentVersion === "current") {
      renderEvents(data.events || [], true);
    }
    if (["reviewable", "completed", "failed", "cancelled"].includes(data.job.status)) {
      closeStream();
      loadEvents();
      loadHistory();
    }
  };
  state.eventSource.onerror = () => {
    closeStream();
  };
}

async function loadHealth() {
  try {
    const data = await fetchJson("/campus_demo/api/health");
    state.health = data;
    document.getElementById("health-value").textContent = data.status === "healthy" ? "服务正常" : data.status;
    document.getElementById("health-meta").textContent = `数据集 ${data.dataset_count} 个 · 历史任务 ${data.job_count} 条 · ${data.server_time}`;
    updateOverviewStats();
  } catch (error) {
    document.getElementById("health-value").textContent = "健康检查失败";
    document.getElementById("health-meta").textContent = error.message;
  }
}

async function loadDatasets() {
  const datasets = await fetchJson("/campus_demo/api/datasets");
  state.datasets = datasets;
  datasetSelect.innerHTML = datasets.map((item) => `
    <option value="${escapeHtml(item.name)}">${escapeHtml(item.display_name)} (${escapeHtml(item.name.toUpperCase())})</option>
  `).join("");
  await loadVideos();
  setMode(state.mode);
}

function updateSampleCardSelection() {
  document.querySelectorAll(".sample-card").forEach((button) => {
    button.classList.toggle("active", button.dataset.video === videoSelect.value);
  });
}

function renderSampleGallery(videos) {
  if (!videos.length) {
    sampleGallery.innerHTML = "";
    sampleGallery.classList.add("hidden");
    return;
  }

  sampleGallery.classList.remove("hidden");
  sampleGallery.innerHTML = videos.map((item) => `
    <button
      type="button"
      class="sample-card${item.is_default_sample ? " is-default" : ""}"
      data-video="${escapeHtml(item.name)}"
    >
      <span class="sample-card-class">${escapeHtml(item.source_class || "Sample")}</span>
      <strong>${escapeHtml(item.name)}</strong>
      <div class="sample-card-meta">
        ${item.has_local_video ? "本地视频可回放" : "仅缓存结果"}
        · ${item.is_default_sample ? "精选样例" : "缓存样例"}
      </div>
    </button>
  `).join("");

  document.querySelectorAll(".sample-card").forEach((button) => {
    button.addEventListener("click", () => {
      videoSelect.value = button.dataset.video;
      updateSampleCardSelection();
    });
  });
  updateSampleCardSelection();
}

async function loadVideos() {
  const dataset = datasetSelect.value || "ucf";
  sampleHint.textContent = "加载样例中...";
  const data = await fetchJson(`/campus_demo/api/videos?dataset=${encodeURIComponent(dataset)}`);
  state.videos = data.videos || [];
  if (!state.videos.length) {
    videoSelect.innerHTML = "";
    renderSampleGallery([]);
    sampleHint.textContent = `当前数据集 ${data.dataset_display_name} 没有可用缓存视频。`;
    return;
  }
  videoSelect.innerHTML = state.videos.map((item) => `
    <option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}${item.is_default_sample ? " · 默认样例" : ""}${item.has_local_video ? "" : " · 无本地视频"}</option>
  `).join("");
  renderSampleGallery(state.videos);
  sampleHint.textContent = data.sample_scope === "website_curated"
    ? `当前数据集 ${data.dataset_display_name} 已接入 ${data.displayed_video_count} 条本地精选样例，来源于本地处理缓存；全量缓存共 ${data.total_video_count} 条。`
    : `当前数据集 ${data.dataset_display_name} 共 ${state.videos.length} 条可用缓存视频，默认样例 ${data.default_samples.length} 条。`;
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-switch button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  document.getElementById("sample-fields").classList.toggle("hidden", mode !== "sample");
  document.getElementById("upload-fields").classList.toggle("hidden", mode !== "upload");
  document.getElementById("rtsp-fields").classList.toggle("hidden", mode !== "rtsp");
  createJobBtn.textContent = modeMeta[mode].action;
  cancelJobBtn.textContent = modeMeta[mode].cancel;
  document.getElementById("mode-summary").textContent = modeMeta[mode].summary;
  updateOverviewStats();
}

async function uploadSource(file) {
  if (!file) {
    return;
  }
  uploadStatus.textContent = "上传中...";
  const encodedFilename = encodeURIComponent(file.name);
  const response = await fetch("/campus_demo/api/sources/upload", {
    method: "POST",
    headers: {
      "X-Filename-Encoded": encodedFilename,
      "Content-Type": file.type || "application/octet-stream",
      "X-Source-Dataset": datasetSelect.value || "ucf",
    },
    body: file,
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "上传失败");
  }
  state.uploadedSource = data;
  uploadStatus.innerHTML = `上传完成：<strong>${escapeHtml(data.filename)}</strong><br />源 ID：${escapeHtml(data.source_id)}${data.media_href ? ` · <a href="${escapeHtml(data.media_href)}" target="_blank">打开源视频</a>` : ""}`;
}

async function loadJob(jobId, startStreaming = true) {
  const data = await fetchJson(`/campus_demo/api/jobs/${encodeURIComponent(jobId)}`);
  state.currentJobId = jobId;
  renderJob(data.job);
  await loadEvents();
  if (startStreaming && ["queued", "running"].includes(data.job.status)) {
    openStream(jobId);
  }
}

function renderEvents(events, fromStream = false) {
  if (!events.length) {
    eventsBody.innerHTML = '<tr><td colspan="8">当前没有日志记录。</td></tr>';
    if (!fromStream) {
      eventsStatus.textContent = "当前没有可展示的事件日志。";
    }
    return;
  }

  const editable = state.currentVersion === "current";
  eventsBody.innerHTML = events.map((event) => `
    <tr data-event-id="${escapeHtml(event.event_id)}">
      <td>
        <button class="time-btn" data-start="${Number(event.start_sec || 0)}">
          ${formatSeconds(event.start_sec)}<br />${formatSeconds(event.end_sec)}
        </button>
      </td>
      <td>
        <div class="event-title">${escapeHtml(event.behavior_type || "待确认")}</div>
        <div class="event-tags">
          <span class="event-tag">ID ${escapeHtml(event.event_id)}</span>
          ${event.is_edited ? '<span class="event-tag edited">已编辑</span>' : ""}
        </div>
        <div style="margin-top:10px;">
          <input type="text" name="behavior_type" value="${escapeHtml(event.behavior_type || "")}" ${editable ? "" : "disabled"} />
        </div>
        <div style="margin-top:10px;">
          <input type="text" name="track_ids" value="${escapeHtml((event.track_ids || []).join(", "))}" ${editable ? "" : "disabled"} />
        </div>
      </td>
      <td>
        <select name="risk_level" ${editable ? "" : "disabled"}>
          <option value="low" ${event.risk_level === "low" ? "selected" : ""}>low</option>
          <option value="review" ${event.risk_level === "review" ? "selected" : ""}>review</option>
          <option value="medium" ${event.risk_level === "medium" ? "selected" : ""}>medium</option>
          <option value="high" ${event.risk_level === "high" ? "selected" : ""}>high</option>
        </select>
        <div class="${riskClass(event.risk_level)} mini" style="margin-top:8px;">${escapeHtml(riskLabel[event.risk_level] || event.risk_level || "待确认")}</div>
      </td>
      <td>
        <select name="review_status" ${editable ? "" : "disabled"}>
          <option value="pending" ${event.review_status === "pending" ? "selected" : ""}>pending</option>
          <option value="confirmed" ${event.review_status === "confirmed" ? "selected" : ""}>confirmed</option>
          <option value="false_positive" ${event.review_status === "false_positive" ? "selected" : ""}>false_positive</option>
        </select>
        <div class="${reviewClass(event.review_status)} mini" style="margin-top:8px;">${escapeHtml(reviewLabel[event.review_status] || event.review_status || "待复核")}</div>
      </td>
      <td>${Number(event.confidence || 0).toFixed(3)}</td>
      <td><textarea name="note" ${editable ? "" : "disabled"}>${escapeHtml(event.note || "")}</textarea></td>
      <td>
        <div>${escapeHtml(event.reason_text || "-")}</div>
        ${event.last_edited_at ? `<div class="mini" style="margin-top:8px;">最后修改：${escapeHtml(event.last_edited_at)}</div>` : ""}
      </td>
      <td>${event.clip_href ? `<a class="inline-link" href="${escapeHtml(event.clip_href)}" target="_blank">打开片段</a>` : '<span class="mini">暂无</span>'}</td>
    </tr>
  `).join("");

  document.querySelectorAll(".time-btn").forEach((button) => {
    button.addEventListener("click", () => {
      const video = document.getElementById("job-video");
      if (!video) {
        eventsStatus.textContent = "当前任务没有可回放视频，但日志和导出仍可正常演示。";
        return;
      }
      video.currentTime = Number(button.dataset.start || 0);
      video.play();
    });
  });

  if (!fromStream) {
    eventsStatus.textContent = editable
      ? `当前展示的是可编辑的最新日志版本，共 ${events.length} 条。`
      : `当前展示的是只读的原始日志版本，共 ${events.length} 条。`;
  }
}

async function loadEvents() {
  if (!state.currentJobId) {
    renderEvents([]);
    return;
  }
  const events = await fetchJson(`/campus_demo/api/jobs/${encodeURIComponent(state.currentJobId)}/events?version=${state.currentVersion}`);
  renderEvents(events);
}

async function saveEvents() {
  if (!state.currentJobId) {
    setStatus(eventsStatus, "请先选择一个任务。");
    return;
  }
  if (state.currentVersion !== "current") {
    setStatus(eventsStatus, "原始版日志不可编辑，请切回“当前版”。");
    return;
  }
  const rows = Array.from(document.querySelectorAll("#events-body tr[data-event-id]"));
  if (!rows.length) {
    setStatus(eventsStatus, "没有可保存的日志内容。");
    return;
  }
  setStatus(eventsStatus, "正在保存修改...");
  for (const row of rows) {
    const eventId = row.dataset.eventId;
    await patchJson(`/campus_demo/api/events/${encodeURIComponent(eventId)}`, {
      job_id: state.currentJobId,
      behavior_type: row.querySelector('[name="behavior_type"]').value.trim(),
      risk_level: row.querySelector('[name="risk_level"]').value,
      review_status: row.querySelector('[name="review_status"]').value,
      note: row.querySelector('[name="note"]').value.trim(),
      track_ids: row.querySelector('[name="track_ids"]').value.trim(),
    });
  }
  setStatus(eventsStatus, "日志修改已保存，当前版本文件和 ZIP 包已同步更新。");
  await loadJob(state.currentJobId, false);
  await loadEvents();
  await loadHistory();
}

async function exportJob(kind) {
  if (!state.currentJobId) {
    setStatus(exportStatus, "请先创建或打开一个任务。");
    return;
  }
  setStatus(exportStatus, `正在执行 ${kind} 导出...`);
  const data = await postJson(`/campus_demo/api/jobs/${encodeURIComponent(state.currentJobId)}/export`, { kind });
  renderArtifacts(data.artifacts || []);
  setStatus(exportStatus, `导出完成：${kind}。已刷新导出产物列表。`);
  if (data.job) {
    renderJob(data.job);
  }
  await loadHistory();
}

async function createJob() {
  closeStream();
  const payload = { source_type: state.mode, dataset: datasetSelect.value || "ucf" };
  if (state.mode === "sample") {
    if (!videoSelect.value) {
      setStatus(sourceStatus, "当前数据集没有可分析的样例视频。");
      return;
    }
    payload.video = videoSelect.value;
  } else if (state.mode === "upload") {
    if (!state.uploadedSource) {
      setStatus(sourceStatus, "请先上传一个本地视频。");
      return;
    }
    payload.source_id = state.uploadedSource.source_id;
    payload.video = state.uploadedSource.filename;
  } else if (state.mode === "rtsp") {
    payload.rtsp_url = document.getElementById("rtsp-input").value.trim();
  }

  setStatus(sourceStatus, "任务创建中...");
  const data = await postJson("/campus_demo/api/jobs", payload);
  state.currentJobId = data.job.job_id;
  renderJob(data.job);
  setStatus(sourceStatus, `任务已创建：${data.job.job_id}`);
  await loadHistory();
  if (["queued", "running"].includes(data.job.status)) {
    openStream(data.job.job_id);
  }
}

async function cancelJob() {
  if (!state.currentJobId) {
    setStatus(sourceStatus, "当前没有正在查看的任务。");
    return;
  }
  await postJson(`/campus_demo/api/jobs/${encodeURIComponent(state.currentJobId)}/cancel`, {});
  setStatus(sourceStatus, "已发送停止请求，正在等待后端完成收尾与最终报告。");
  await loadJob(state.currentJobId, true);
  await loadHistory();
}

async function loadHistory() {
  const items = await fetchJson("/campus_demo/api/history");
  state.historyItems = items;
  const successfulItems = items.filter((item) => item.report_href || ["reviewable", "completed"].includes(item.status));
  const transientItems = items.length - successfulItems.length;
  historySummary.textContent = items.length
    ? `当前展示 ${items.length} 条历史记录，其中成功案例 ${successfulItems.length} 条优先保留${transientItems ? `，最近失败/取消 ${transientItems} 条补充保留。` : "。"}`
    : "暂无历史任务。";

  if (!items.length) {
    historyList.innerHTML = '<article class="history-card muted-card">暂无历史任务。</article>';
    updateOverviewStats(document.getElementById("metric-events").textContent);
    return;
  }

  historyList.innerHTML = items.map((item) => `
    <article class="history-card">
      <div class="history-head">
        <div>
          <strong>${escapeHtml(item.video_name || item.source_label || item.job_id)}</strong>
          <div class="history-meta">${escapeHtml(item.dataset_display_name || item.dataset_name || "未知数据集")} · ${escapeHtml(item.created_at || "")}</div>
        </div>
        <div class="badge status-${escapeHtml(item.status || "queued")}">${escapeHtml(statusText(item.status))}</div>
      </div>
      <div class="mini">
        事件 ${item.event_count || 0} 条 · 分段 ${item.processed_segments || 0} · 窗口 ${item.analyzed_windows || 0}
        · ${item.source_type || "sample"}
        · ${item.report_href ? "已生成报告" : "未生成报告"}
        · ${item.progress_mode === "indeterminate" && ["queued", "running"].includes(item.status) ? "流式任务" : `进度 ${Math.round(Number(item.progress || 0) * 100)}%`}
      </div>
      <div class="history-actions">
        <button class="ghost-btn open-history-btn" data-job-id="${escapeHtml(item.job_id)}">重新打开</button>
        ${item.report_href ? `<a class="inline-link" href="/campus_demo_outputs/${escapeHtml(item.report_href)}" target="_blank">报告页</a>` : ""}
      </div>
    </article>
  `).join("");

  document.querySelectorAll(".open-history-btn").forEach((button) => {
    button.addEventListener("click", async () => {
      await loadJob(button.dataset.jobId, false);
      const jobData = await fetchJson(`/campus_demo/api/jobs/${encodeURIComponent(button.dataset.jobId)}`);
      if (["queued", "running"].includes(jobData.job.status)) {
        openStream(button.dataset.jobId);
      }
    });
  });
  updateOverviewStats(document.getElementById("metric-events").textContent);
}

function renderTrainJob(job) {
  if (!job) {
    trainStatus.textContent = "尚未触发训练任务。";
    return;
  }
  trainStatus.innerHTML = `训练任务 <strong>${escapeHtml(job.train_id)}</strong> · ${escapeHtml(job.status)} · ${escapeHtml(job.model_alias || "")}${job.metrics_path ? ` · <span class="mini">${escapeHtml(job.metrics_path)}</span>` : ""}`;
}

function renderEvalJob(job) {
  if (!job) {
    evalStatus.textContent = "尚未触发测评。";
    return;
  }
  evalStatus.innerHTML = `测评任务 <strong>${escapeHtml(job.eval_id || job.dataset || "eval")}</strong> · ${escapeHtml(job.status)} · ${escapeHtml(job.message || "")}`;
}

async function runTrain() {
  trainStatus.textContent = "训练任务提交中...";
  const recipeId = document.getElementById("train-recipe").value.trim() || "demo-recipe";
  const data = await postJson("/campus_demo/api/train", { recipe_id: recipeId });
  const detail = await fetchJson(`/campus_demo/api/train/${encodeURIComponent(data.job.train_id)}`);
  state.trainJob = detail;
  renderTrainJob(detail);
}

async function runEval() {
  evalStatus.textContent = "测评任务提交中...";
  const data = await postJson("/campus_demo/api/eval", { dataset: datasetSelect.value || "ucf" });
  state.evalJob = data.job;
  renderEvalJob(data.job);
}

async function init() {
  renderJob(null);
  renderTrainJob(null);
  renderEvalJob(null);
  await loadHealth();
  await loadDatasets();
  await loadHistory();

  const params = new URLSearchParams(window.location.search);
  const jobId = params.get("job_id");
  if (jobId) {
    try {
      await loadJob(jobId);
    } catch (error) {
      setStatus(sourceStatus, `打开指定任务失败：${error.message}`);
    }
  }
}

document.querySelectorAll(".mode-switch button").forEach((button) => {
  button.addEventListener("click", async () => {
    setMode(button.dataset.mode);
    if (button.dataset.mode === "sample") {
      try {
        await loadVideos();
      } catch (error) {
        sampleHint.textContent = `加载样例失败：${error.message}`;
      }
    }
  });
});

datasetSelect.addEventListener("change", async () => {
  state.uploadedSource = null;
  uploadInput.value = "";
  uploadStatus.textContent = "尚未上传文件。上传任务会调用真实 VADTree 适配链路；若模型依赖未配置，任务会明确失败并返回原因。";
  try {
    await loadVideos();
  } catch (error) {
    sampleHint.textContent = `加载样例失败：${error.message}`;
  }
});

videoSelect.addEventListener("change", () => {
  updateSampleCardSelection();
});

uploadInput.addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) {
    return;
  }
  try {
    await uploadSource(file);
  } catch (error) {
    uploadStatus.textContent = `上传失败：${error.message}`;
  }
});

document.getElementById("create-job-btn").addEventListener("click", async () => {
  try {
    await createJob();
  } catch (error) {
    setStatus(sourceStatus, `创建任务失败：${error.message}`);
  }
});

document.getElementById("cancel-job-btn").addEventListener("click", async () => {
  try {
    await cancelJob();
  } catch (error) {
    setStatus(sourceStatus, `停止失败：${error.message}`);
  }
});

document.getElementById("reload-events-btn").addEventListener("click", async () => {
  try {
    await loadEvents();
  } catch (error) {
    setStatus(eventsStatus, `刷新日志失败：${error.message}`);
  }
});

document.getElementById("save-events-btn").addEventListener("click", async () => {
  try {
    await saveEvents();
  } catch (error) {
    setStatus(eventsStatus, `保存失败：${error.message}`);
  }
});

document.getElementById("version-current").addEventListener("click", async () => {
  state.currentVersion = "current";
  document.getElementById("version-current").classList.add("active");
  document.getElementById("version-original").classList.remove("active");
  await loadEvents();
});

document.getElementById("version-original").addEventListener("click", async () => {
  state.currentVersion = "original";
  document.getElementById("version-original").classList.add("active");
  document.getElementById("version-current").classList.remove("active");
  await loadEvents();
});

document.querySelectorAll(".export-btn").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await exportJob(button.dataset.kind);
    } catch (error) {
      setStatus(exportStatus, `导出失败：${error.message}`);
    }
  });
});

document.getElementById("train-btn").addEventListener("click", async () => {
  try {
    await runTrain();
  } catch (error) {
    trainStatus.textContent = `训练触发失败：${error.message}`;
  }
});

document.getElementById("eval-btn").addEventListener("click", async () => {
  try {
    await runEval();
  } catch (error) {
    evalStatus.textContent = `测评触发失败：${error.message}`;
  }
});

window.addEventListener("DOMContentLoaded", () => {
  init().catch((error) => {
    setStatus(sourceStatus, `初始化失败：${error.message}`);
  });
});
