const byId = (id) => document.getElementById(id);
let selected = null;
let signature = null;
let pending = false;

async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

const milliseconds = (value) => value == null ? "—" : `${value} ms`;

function pointRows(tests) {
  return tests.map((point) => {
    const row = document.createElement("tr");
    const rss = point.mem_bytes == null || point.memory_source !== "rss_peak"
      ? "—" : `${(point.mem_bytes / 1048576).toFixed(1)} MiB`;
    for (const value of [point.test_id, point.node_id, point.verdict, point.reason,
      milliseconds(point.wall_ms), milliseconds(point.cpu_ms), rss]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    return row;
  });
}

async function showContest() {
  const data = await request("/api/contest");
  const item = data.items[0];
  if (!item) {
    byId("contest-state").textContent = "暂无新版竞赛题实测记录。";
    return;
  }
  const passed = item.tests.filter((point) => point.verdict === "AC").length;
  byId("contest-state").textContent = `${item.verdict} · ${passed}/${item.dispatched} 点通过 · 证据 ${data.evidence}`;
  const counts = new Map();
  for (const point of item.tests) counts.set(point.node_id, (counts.get(point.node_id) || 0) + 1);
  byId("node-counts").replaceChildren(...[...counts.entries()].sort().map(([node, count]) => {
    const label = document.createElement("span");
    label.textContent = `${node} 完成 ${count} 点`;
    return label;
  }));
  byId("contest-tests").hidden = false;
  byId("contest-points").replaceChildren(...pointRows(item.tests));
}

async function show(id) {
  selected = id;
  for (const button of byId("cards").children) {
    button.setAttribute("aria-pressed", String(button.dataset.id === id));
  }
  const item = await request("/api/submissions/" + encodeURIComponent(id));
  if (selected !== id) return;
  byId("summary").textContent = `${item.id} · 编译 ${item.compile_status} · 最终 ${item.verdict} · 派发 ${item.dispatched} 点`
    + (item.tests.length ? "" : ` · 未派发评测（${item.reason}）`);
  byId("tests").hidden = !item.tests.length;
  byId("points").replaceChildren(...pointRows(item.tests));
  byId("diagnostic").hidden = !item.diagnostic;
  byId("diagnostic").textContent = item.diagnostic;
}

async function refresh() {
  if (pending) return;
  pending = true;
  try {
    const data = await request("/api/submissions");
    byId("run").disabled = data.state === "running";
    const mode = data.mode === "live" ? "本机内置样例实跑" : "本机实测记录回放";
    byId("state").textContent = data.state === "running" ? "正在构建并运行样例，当前保留上一批结果。"
      : data.state === "failed" ? data.detail : data.items.length ? mode : "暂无记录，请运行内置样例。";
    byId("evidence").textContent = data.evidence ? `证据：artifacts/system/${data.evidence}` : "";
    if (signature === data.evidence) return;
    signature = data.evidence;
    byId("cards").replaceChildren(...data.items.map((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.id = item.id;
      button.textContent = `${item.verdict} · ${item.dispatched} 点 · ${item.sample}`;
      button.addEventListener("click", () => show(item.id).catch(report));
      return button;
    }));
    const first = data.items.find((item) => item.id === selected) || data.items[0];
    if (first) await show(first.id);
  } catch (error) {
    report(error);
  } finally {
    pending = false;
  }
}

function report(error) {
  byId("state").textContent = error.message;
}

byId("run").addEventListener("click", async () => {
  byId("run").disabled = true;
  try { await request("/api/demo/run", {method: "POST"}); await refresh(); }
  catch (error) { report(error); byId("run").disabled = false; }
});
refresh();
showContest().catch((error) => { byId("contest-state").textContent = error.message; });
setInterval(refresh, 1500);
