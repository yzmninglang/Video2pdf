const state = {
  rootDir: "",
  selectedFolders: new Set(),
  selectedFiles: new Set(),
  searchPattern: "",
  lastJobId: "",
  autoRefreshTimer: null,
  saveSettingsTimer: null,
};

const el = {
  rootDir: document.getElementById("rootDir"),
  scanBtn: document.getElementById("scanBtn"),
  scanStatus: document.getElementById("scanStatus"),
  folderList: document.getElementById("folderList"),

  videoPattern: document.getElementById("videoPattern"),
  searchVideosBtn: document.getElementById("searchVideosBtn"),
  searchStatus: document.getElementById("searchStatus"),
  fileList: document.getElementById("fileList"),

  settingsStatus: document.getElementById("settingsStatus"),
  algorithm: document.getElementById("algorithm"),
  frameRate: document.getElementById("frameRate"),
  warmupFrames: document.getElementById("warmupFrames"),
  resizeWidth: document.getElementById("resizeWidth"),
  history: document.getElementById("history"),
  varThreshold: document.getElementById("varThreshold"),
  distThreshold: document.getElementById("distThreshold"),
  stablePercent: document.getElementById("stablePercent"),
  motionPercent: document.getElementById("motionPercent"),
  diffBinaryThreshold: document.getElementById("diffBinaryThreshold"),
  diffMotionPercent: document.getElementById("diffMotionPercent"),
  elapsedFrameThreshold: document.getElementById("elapsedFrameThreshold"),
  enableFrameDiffRefine: document.getElementById("enableFrameDiffRefine"),
  autoDetectOrientation: document.getElementById("autoDetectOrientation"),
  removeDuplicates: document.getElementById("removeDuplicates"),
  hashFunc: document.getElementById("hashFunc"),
  hashSize: document.getElementById("hashSize"),
  similarityThreshold: document.getElementById("similarityThreshold"),
  hashQueueLen: document.getElementById("hashQueueLen"),
  keepIntermediate: document.getElementById("keepIntermediate"),

  submitBtn: document.getElementById("submitBtn"),
  submitStatus: document.getElementById("submitStatus"),

  refreshJobsBtn: document.getElementById("refreshJobsBtn"),
  jobsTableBody: document.querySelector("#jobsTable tbody"),

  jobIdInput: document.getElementById("jobIdInput"),
  viewDetailBtn: document.getElementById("viewDetailBtn"),
  detailStatus: document.getElementById("detailStatus"),
  detailTableBody: document.querySelector("#detailTable tbody"),
};

function setHint(target, message, isError = false) {
  target.textContent = message;
  target.style.color = isError ? "#b42318" : "";
}

function toast(message, isError = false) {
  setHint(el.submitStatus, message, isError);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (data.detail) detail = data.detail;
    } catch (err) {
      // ignore
    }
    throw new Error(detail);
  }

  return res.json();
}

function buildChecklist(container, items, selectedSet, onChange) {
  container.innerHTML = "";
  if (!items.length) {
    container.innerHTML = '<p class="hint">无可选项</p>';
    return;
  }

  items.forEach((item) => {
    const wrap = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = item;
    input.checked = selectedSet.has(item);
    input.addEventListener("change", () => onChange(item, input.checked));

    wrap.appendChild(input);
    wrap.appendChild(document.createTextNode(` ${item}`));
    container.appendChild(wrap);
  });
}

function parseNumber(elm) {
  return Number(elm.value);
}

function validateConfig(config) {
  if (config.motion_percent <= config.stable_percent) {
    return "运动阈值必须大于静止阈值";
  }
  if (config.frame_rate <= 0) {
    return "处理帧间隔必须大于 0";
  }
  if (config.hash_size <= 1) {
    return "哈希尺寸必须大于 1";
  }
  return "";
}

function collectConfig() {
  const algorithm = el.algorithm.value;
  const frameDiffRefine = algorithm !== "FrameDiff" && el.enableFrameDiffRefine.checked;

  return {
    algorithm,
    frame_rate: parseNumber(el.frameRate),
    warmup_frames: parseNumber(el.warmupFrames),
    resize_width: parseNumber(el.resizeWidth),
    history: parseNumber(el.history),
    var_threshold: parseNumber(el.varThreshold),
    dist_threshold: parseNumber(el.distThreshold),
    stable_percent: parseNumber(el.stablePercent),
    motion_percent: parseNumber(el.motionPercent),
    diff_binary_threshold: parseNumber(el.diffBinaryThreshold),
    diff_motion_percent: parseNumber(el.diffMotionPercent),
    elapsed_frame_threshold: parseNumber(el.elapsedFrameThreshold),
    enable_frame_diff_refine: frameDiffRefine,
    auto_detect_orientation: el.autoDetectOrientation.checked,
    remove_duplicates: el.removeDuplicates.checked,
    hash_func: el.hashFunc.value,
    hash_size: parseNumber(el.hashSize),
    similarity_threshold: parseNumber(el.similarityThreshold),
    hash_queue_len: parseNumber(el.hashQueueLen),
    keep_intermediate: el.keepIntermediate.checked,
  };
}

function applyConfig(config) {
  if (!config) return;
  el.algorithm.value = config.algorithm;
  el.frameRate.value = config.frame_rate;
  el.warmupFrames.value = config.warmup_frames;
  el.resizeWidth.value = config.resize_width;
  el.history.value = config.history;
  el.varThreshold.value = config.var_threshold;
  el.distThreshold.value = config.dist_threshold;
  el.stablePercent.value = config.stable_percent;
  el.motionPercent.value = config.motion_percent;
  el.diffBinaryThreshold.value = config.diff_binary_threshold;
  el.diffMotionPercent.value = config.diff_motion_percent;
  el.elapsedFrameThreshold.value = config.elapsed_frame_threshold;
  el.enableFrameDiffRefine.checked = !!config.enable_frame_diff_refine;
  el.autoDetectOrientation.checked = !!config.auto_detect_orientation;
  el.removeDuplicates.checked = config.remove_duplicates;
  el.hashFunc.value = config.hash_func;
  el.hashSize.value = config.hash_size;
  el.similarityThreshold.value = config.similarity_threshold;
  el.hashQueueLen.value = config.hash_queue_len;
  el.keepIntermediate.checked = config.keep_intermediate;
  syncAlgorithmDependentControls();
}

function syncAlgorithmDependentControls() {
  const isFrameDiff = el.algorithm.value === "FrameDiff";
  if (isFrameDiff) {
    el.enableFrameDiffRefine.checked = false;
    el.enableFrameDiffRefine.disabled = true;
  } else {
    el.enableFrameDiffRefine.disabled = false;
  }
}

function renderJobs(jobs) {
  el.jobsTableBody.innerHTML = "";
  if (!jobs.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="9">暂无任务</td>';
    el.jobsTableBody.appendChild(row);
    return;
  }

  jobs.forEach((job) => {
    const tr = document.createElement("tr");
    const s = job.summary;
    const canStop = ["queued", "running"].includes(String(job.status));
    const actionHtml = canStop
      ? `<button class="danger stop-job-btn" data-job-id="${job.job_id}">停止</button>`
      : "-";
    const cells = [
      ["Job ID", job.job_id],
      ["Status", job.status],
      ["Total", s.total],
      ["Pending", s.pending],
      ["Running", s.running],
      ["OK", s.ok],
      ["Failed", s.failed],
      ["Created At", job.created_at || ""],
      ["Action", actionHtml],
    ];
    tr.innerHTML = cells
      .map(([label, value]) => `<td data-label="${label}">${value}</td>`)
      .join("");

    tr.addEventListener("click", () => {
      el.jobIdInput.value = job.job_id;
      state.lastJobId = job.job_id;
      loadJobDetail();
    });

    const stopBtn = tr.querySelector(".stop-job-btn");
    if (stopBtn) {
      stopBtn.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        await stopJob(job.job_id);
      });
    }

    el.jobsTableBody.appendChild(tr);
  });
}

async function stopJob(jobId) {
  if (!jobId) return;
  try {
    const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/stop`, { method: "POST" });
    toast(data.message || "已请求停止任务");
    await refreshJobs();
    if (state.lastJobId === jobId || el.jobIdInput.value.trim() === jobId) {
      await loadJobDetail();
    }
  } catch (err) {
    toast(`停止任务失败: ${err.message}`, true);
  }
}

function renderJobDetail(job) {
  const s = job.summary;
  el.detailStatus.textContent = `状态: ${job.status}, 总计 ${s.total}, 运行中 ${s.running}, 成功 ${s.ok}, 失败 ${s.failed}`;

  el.detailTableBody.innerHTML = "";
  if (!job.videos || !job.videos.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="6">无视频详情</td>';
    el.detailTableBody.appendChild(tr);
    return;
  }

  job.videos.forEach((item) => {
    const tr = document.createElement("tr");
    const cells = [
      ["Video", item.video],
      ["Status", item.status],
      ["Progress", `${(item.progress * 100).toFixed(1)}%`],
      ["Slides", item.slide_count],
      ["PDF", item.pdf || ""],
      ["Message", item.message || ""],
    ];
    tr.innerHTML = cells
      .map(([label, value]) => `<td data-label="${label}">${value}</td>`)
      .join("");
    el.detailTableBody.appendChild(tr);
  });
}

async function saveSettingsNow() {
  try {
    const rootDir = el.rootDir.value.trim();
    if (rootDir) {
      state.rootDir = rootDir;
    }

    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        root_dir: state.rootDir,
        selected_folders: Array.from(state.selectedFolders),
        search_pattern: state.searchPattern,
        config: collectConfig(),
      }),
    });
    setHint(el.settingsStatus, "参数已自动保存");
  } catch (err) {
    setHint(el.settingsStatus, `保存参数失败: ${err.message}`, true);
  }
}

function debounceSaveSettings() {
  if (state.saveSettingsTimer) {
    window.clearTimeout(state.saveSettingsTimer);
  }
  state.saveSettingsTimer = window.setTimeout(saveSettingsNow, 500);
}

function refreshConfigStatus() {
  const config = collectConfig();
  const error = validateConfig(config);
  if (error) {
    setHint(el.settingsStatus, `参数错误: ${error}`, true);
  } else {
    setHint(el.settingsStatus, "参数会自动保存。");
  }
}

async function loadDefaults() {
  try {
    const data = await api("/api/defaults");
    const settings = data.settings || {};

    state.rootDir = settings.last_root_dir || data.effective_root_dir || data.mapped_dir;
    state.searchPattern = settings.search_pattern || "";
    el.rootDir.value = state.rootDir;
    el.videoPattern.value = state.searchPattern;

    applyConfig(settings.config || data.default_config || data.config);

    state.selectedFolders = new Set(settings.selected_folders || []);
  } catch (err) {
    setHint(el.scanStatus, `获取默认配置失败: ${err.message}`, true);
  }
}

async function scanDir() {
  const rootDir = el.rootDir.value.trim();
  if (!rootDir) {
    setHint(el.scanStatus, "目录不能为空", true);
    return;
  }

  try {
    const data = await api("/api/scan", {
      method: "POST",
      body: JSON.stringify({ root_dir: rootDir }),
    });

    state.rootDir = data.root_dir;

    const availableFolders = new Set(data.folders);
    const filtered = new Set();
    state.selectedFolders.forEach((item) => {
      if (availableFolders.has(item)) filtered.add(item);
    });
    state.selectedFolders = filtered;

    buildChecklist(el.folderList, data.folders, state.selectedFolders, (item, checked) => {
      if (checked) state.selectedFolders.add(item);
      else state.selectedFolders.delete(item);
      debounceSaveSettings();
    });

    setHint(
      el.scanStatus,
      `已扫描 ${data.root_dir}。发现 ${data.counts.files} 个视频，${data.counts.folders} 个文件夹。`,
    );

    await searchVideos();
    debounceSaveSettings();
  } catch (err) {
    setHint(el.scanStatus, `扫描失败: ${err.message}`, true);
  }
}

async function searchVideos() {
  if (!state.rootDir) {
    setHint(el.searchStatus, "请先扫描目录", true);
    return;
  }

  state.searchPattern = el.videoPattern.value.trim();

  try {
    const data = await api("/api/videos/search", {
      method: "POST",
      body: JSON.stringify({
        root_dir: state.rootDir,
        pattern: state.searchPattern,
        selected_folders: Array.from(state.selectedFolders),
        limit: 300,
      }),
    });

    const available = new Set(data.videos || []);
    const selected = new Set();
    state.selectedFiles.forEach((item) => {
      if (available.has(item)) selected.add(item);
    });
    state.selectedFiles = selected;

    buildChecklist(el.fileList, data.videos || [], state.selectedFiles, (item, checked) => {
      if (checked) state.selectedFiles.add(item);
      else state.selectedFiles.delete(item);
    });

    setHint(
      el.searchStatus,
      data.pattern
        ? `候选 ${data.candidate_count} 个视频，返回 ${data.returned_count} 个（limit=${data.limit}）。`
        : `候选 ${data.candidate_count} 个视频。请输入正则后点击搜索（当前不展示全量视频）。`,
    );

    debounceSaveSettings();
  } catch (err) {
    setHint(el.searchStatus, `搜索失败: ${err.message}`, true);
  }
}

async function submitJob() {
  if (!state.rootDir) {
    toast("请先扫描目录", true);
    return;
  }

  try {
    const config = collectConfig();
    const configError = validateConfig(config);
    if (configError) {
      toast(`提交失败: ${configError}`, true);
      return;
    }

    const payload = {
      root_dir: state.rootDir,
      selected_folders: Array.from(state.selectedFolders),
      selected_files: Array.from(state.selectedFiles),
      config,
    };

    const data = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    toast(data.message);
    state.lastJobId = data.job.job_id;
    el.jobIdInput.value = state.lastJobId;

    await refreshJobs();
    await loadJobDetail();
    startAutoRefresh();
  } catch (err) {
    toast(`提交失败: ${err.message}`, true);
  }
}

async function refreshJobs() {
  try {
    const data = await api("/api/jobs");
    const activeJobs = (data.jobs || []).filter((job) => ["queued", "running"].includes(String(job.status)));
    renderJobs(activeJobs);
  } catch (err) {
    toast(`刷新任务失败: ${err.message}`, true);
  }
}

async function loadJobDetail() {
  const jobId = el.jobIdInput.value.trim();
  if (!jobId) {
    setHint(el.detailStatus, "请输入 Job ID", true);
    return;
  }

  try {
    const qs = new URLSearchParams({ root_dir: state.rootDir || el.rootDir.value.trim() });
    const data = await api(`/api/jobs/${encodeURIComponent(jobId)}?${qs.toString()}`);
    renderJobDetail(data.job);
  } catch (err) {
    setHint(el.detailStatus, `查询失败: ${err.message}`, true);
  }
}

function startAutoRefresh() {
  if (state.autoRefreshTimer) return;
  state.autoRefreshTimer = window.setInterval(async () => {
    await refreshJobs();
    if (state.lastJobId) {
      await loadJobDetail();
    }
  }, 5000);
}

function registerConfigAutoSave() {
  const controls = [
    el.algorithm,
    el.frameRate,
    el.warmupFrames,
    el.resizeWidth,
    el.history,
    el.varThreshold,
    el.distThreshold,
    el.stablePercent,
    el.motionPercent,
    el.diffBinaryThreshold,
    el.diffMotionPercent,
    el.elapsedFrameThreshold,
    el.removeDuplicates,
    el.autoDetectOrientation,
    el.hashFunc,
    el.hashSize,
    el.similarityThreshold,
    el.hashQueueLen,
    el.keepIntermediate,
    el.enableFrameDiffRefine,
  ];

  controls.forEach((item) => {
    item.addEventListener("change", () => {
      refreshConfigStatus();
      debounceSaveSettings();
    });
    item.addEventListener("input", () => {
      refreshConfigStatus();
      debounceSaveSettings();
    });
  });

  el.rootDir.addEventListener("change", debounceSaveSettings);

  el.algorithm.addEventListener("change", () => {
    syncAlgorithmDependentControls();
    refreshConfigStatus();
    debounceSaveSettings();
  });
}

el.scanBtn.addEventListener("click", scanDir);
el.searchVideosBtn.addEventListener("click", searchVideos);
el.submitBtn.addEventListener("click", submitJob);
el.refreshJobsBtn.addEventListener("click", refreshJobs);
el.viewDetailBtn.addEventListener("click", loadJobDetail);
el.videoPattern.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    searchVideos();
  }
});
el.videoPattern.addEventListener("change", debounceSaveSettings);

(async function init() {
  registerConfigAutoSave();
  await loadDefaults();
  refreshConfigStatus();
  await scanDir();
  await refreshJobs();
  startAutoRefresh();
})();
