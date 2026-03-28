const terminalStatuses = new Set(["done", "done_with_errors", "failed", "stopped"]);

const el = {
  refreshHistoryBtn: document.getElementById("refreshHistoryBtn"),
  historyStatus: document.getElementById("historyStatus"),
  historyTableBody: document.querySelector("#historyTable tbody"),
  jobIdInput: document.getElementById("jobIdInput"),
  viewDetailBtn: document.getElementById("viewDetailBtn"),
  detailStatus: document.getElementById("detailStatus"),
  detailTableBody: document.querySelector("#detailTable tbody"),
};

function setHint(target, message, isError = false) {
  target.textContent = message;
  target.style.color = isError ? "#b42318" : "";
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

function renderHistoryJobs(jobs) {
  el.historyTableBody.innerHTML = "";
  if (!jobs.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="8">暂无历史任务</td>';
    el.historyTableBody.appendChild(row);
    return;
  }

  jobs.forEach((job) => {
    const tr = document.createElement("tr");
    const s = job.summary;
    const cells = [
      ["Job ID", job.job_id],
      ["Status", job.status],
      ["Total", s.total],
      ["Pending", s.pending],
      ["Running", s.running],
      ["OK", s.ok],
      ["Failed", s.failed],
      ["Created At", job.created_at || ""],
    ];
    tr.innerHTML = cells
      .map(([label, value]) => `<td data-label="${label}">${value}</td>`)
      .join("");

    tr.addEventListener("click", () => {
      el.jobIdInput.value = job.job_id;
      loadJobDetail();
    });

    el.historyTableBody.appendChild(tr);
  });
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

async function refreshHistory() {
  try {
    const data = await api("/api/jobs");
    const jobs = (data.jobs || []).filter((job) => terminalStatuses.has(String(job.status)));
    renderHistoryJobs(jobs);
    setHint(el.historyStatus, `历史任务 ${jobs.length} 条`);
  } catch (err) {
    setHint(el.historyStatus, `刷新历史任务失败: ${err.message}`, true);
  }
}

async function loadJobDetail() {
  const jobId = el.jobIdInput.value.trim();
  if (!jobId) {
    setHint(el.detailStatus, "请输入 Job ID", true);
    return;
  }

  try {
    const data = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    renderJobDetail(data.job);
  } catch (err) {
    setHint(el.detailStatus, `查询失败: ${err.message}`, true);
  }
}

el.refreshHistoryBtn.addEventListener("click", refreshHistory);
el.viewDetailBtn.addEventListener("click", loadJobDetail);

(async function init() {
  await refreshHistory();
})();
