const byId = (id) => document.getElementById(id);
let selected = null;
let known = new Set();

async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
  return data;
}

const cell = (text, className) => {
  const td = document.createElement("td");
  td.textContent = text == null ? "—" : String(text);
  if (className) td.className = className;
  return td;
};

function verdictCell(verdict) {
  return cell(verdict || "—", verdict ? `verdict-${verdict}` : "");
}

async function loadProblems() {
  const data = await request("/api/v1/problems");
  byId("problem").replaceChildren(...data.items.map((problem) => {
    const option = document.createElement("option");
    option.value = problem.problem_id;
    option.textContent = `${problem.problem_id} · ${problem.title} · ${problem.time_limit_ms}ms`;
    return option;
  }));
}

async function loadSubmissions() {
  const data = await request("/api/v1/submissions?limit=50");
  byId("submissions").replaceChildren(...data.items.map((item) => {
    const row = document.createElement("tr");
    row.append(cell(item.file_id.slice(0, 8)), cell(item.problem_id), cell(item.status),
                verdictCell(item.verdict), cell((item.submitted_at || "").replace("T", " ").slice(0, 19)));
    row.addEventListener("click", () => show(item.file_id).catch(report));
    return row;
  }));
  known = new Set(data.items.map((item) => item.file_id));
}

async function show(fileId) {
  selected = fileId;
  const item = await request(`/api/v1/submissions/${fileId}`);
  if (selected !== fileId) return;
  byId("detail-panel").hidden = false;
  byId("detail-title").textContent = item.file_id;
  byId("summary").textContent =
    `题目 ${item.problem_id} · 状态 ${item.status} · 判定 ${item.verdict || "—"}` +
    ` · 策略 ${item.evaluation_mode} · 取消 ${item.cancel_reason || "无"}`;
  byId("points").replaceChildren(...item.per_test.map((point) => {
    const row = document.createElement("tr");
    row.append(cell(point.test_id), cell(point.node_id || "—"), cell(point.state),
                verdictCell(point.verdict), cell(point.reason || "—"),
                cell(point.cpu_ms == null ? "—" : `${point.cpu_ms} ms`),
                cell(point.wall_ms == null ? "—" : `${point.wall_ms} ms`),
                cell(point.mem_bytes == null ? "—" : `${(point.mem_bytes / 1048576).toFixed(1)} MiB`),
                cell(point.short_circuited ? "是" : "否"));
    return row;
  }));
  byId("diagnostic").hidden = !item.compile_log;
  byId("diagnostic").textContent = item.compile_log || "";
}

async function loadNodes() {
  const data = await request("/api/v1/nodes");
  byId("nodes").replaceChildren(...data.items.map((node) => {
    const row = document.createElement("tr");
    row.append(cell(node.node_id), cell(node.role), `${node.slots_used}/${node.slots_total}`,
                cell(node.cached_hashes), cell(node.status), cell(node.last_heartbeat.slice(0, 19)));
    return row;
  }));
}

async function runExperiment() {
  const payload = {
    profile: byId("profile").value,
    count: Number(byId("count").value),
    nodes: Number(byId("sim-nodes").value),
    seed: Number(byId("seed").value),
    strategies: ["fcfs", "sjf", "aging_sjf", "earliest_finish", "cache_aware"],
  };
  const button = byId("run-experiment");
  button.disabled = true;
  try {
    const data = await request("/api/v1/experiments/simulation", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const metrics = data.summary.metrics;
    byId("experiment-table").hidden = false;
    byId("experiment-metrics").replaceChildren(...Object.entries(metrics).map(([name, values]) => {
      const row = document.createElement("tr");
      row.append(cell(name), cell(values.avg_wait_ms.toFixed(1)),
                  cell(values.p95_wait_ms.toFixed(1)), cell(values.max_wait_ms.toFixed(1)),
                  cell(values.avg_turnaround_ms.toFixed(1)),
                  cell(values.throughput_per_second.toFixed(2)),
                  cell((values.cache_hit_rate * 100).toFixed(1) + "%"),
                  cell(values.fairness_jain.toFixed(3)));
      return row;
    }));
  } finally {
    button.disabled = false;
  }
}

async function submitSolution() {
  const button = byId("submit");
  button.disabled = true;
  byId("submit-status").textContent = "提交中…";
  try {
    const response = await request("/api/v1/submissions", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        problem_id: byId("problem").value,
        source: byId("source").value,
        strategy: byId("strategy").value,
        evaluation_mode: byId("mode").value,
      }),
    });
    byId("submit-status").textContent = `已提交 ${response.file_id}`;
    known.add(response.file_id);
    await loadSubmissions();
    await show(response.file_id);
  } catch (error) {
    byId("submit-status").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function report(error) {
  byId("submit-status").textContent = error.message;
}

async function health() {
  try {
    const data = await request("/healthz");
    byId("health").textContent = `控制面在线 · ${data.node}`;
    byId("health").className = "badge";
  } catch (error) {
    byId("health").textContent = "控制面离线";
byId("health").className = "badge";
  }
}

byId("submit").addEventListener("click", submitSolution);
byId("run-experiment").addEventListener("click", () => runExperiment().catch(report));

async function refresh() {
  try {
    await Promise.all([loadSubmissions(), loadNodes(), health()]);
    if (selected) await show(selected);
  } catch (error) {
    report(error);
  }
}

loadProblems().catch(report);
refresh();
setInterval(refresh, 2000);
