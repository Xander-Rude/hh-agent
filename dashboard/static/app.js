"use strict";
const $ = (id) => document.getElementById(id);
const display = (value) => value === null || value === undefined || value === "" ? "N/A" : String(value);
const count = (value) => value === null || value === undefined ? "N/A" : Number(value).toLocaleString("ru-RU");
const labels = {pipeline: "Pipeline", apply: "Apply worker", telegram: "Telegram", resume_raise: "Resume raise"};
const statuses = {starting: "Запуск", running: "Выполняется", ok: "Завершён", failed: "Ошибка", skipped: "Пропущен", stopped: "Остановлен"};
const svgNS = "http://www.w3.org/2000/svg";
function drawBars(target, items, color) {
  if (items === null || items === undefined) { $(target).textContent = "N/A · Данные не сохраняются"; return; }
  const svg = document.createElementNS(svgNS, "svg");
  const width = Math.max(380, items.length * 40);
  svg.setAttribute("viewBox", `0 0 ${width} 210`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", items.map((item) => `${item.label}: ${item.count}`).join("; "));
  const max = Math.max(1, ...items.map((item) => item.count));
  const step = width / items.length;
  for (const [index, item] of items.entries()) {
    const x = step * index + step / 2;
    const height = item.count / max * 142;
    const rect = document.createElementNS(svgNS, "rect");
    for (const [key, value] of Object.entries({x: x - step * .28, y: 174 - height, width: step * .56, height, rx: 2, fill: color, stroke: "none", opacity: .8})) rect.setAttribute(key, value);
    const title = document.createElementNS(svgNS, "title"); title.textContent = `${item.label}: ${item.count}`; rect.append(title);
    svg.append(rect);
    for (const [value, y, className] of [[item.count, 164 - height, "value"], [item.label, 198, "label"]]) {
      const text = document.createElementNS(svgNS, "text");
      text.setAttribute("x", x); text.setAttribute("y", y); text.setAttribute("text-anchor", "middle");
      text.setAttribute("class", className); text.textContent = value; svg.append(text);
    }
  }
  $(target).replaceChildren(svg);
}
const age = (seconds) => seconds === null || seconds === undefined ? "N/A" : seconds < 60 ? `${seconds} с назад` : seconds < 3600 ? `${Math.floor(seconds / 60)} мин назад` : seconds < 86400 ? `${Math.floor(seconds / 3600)} ч назад` : `${Math.floor(seconds / 86400)} дн назад`;
function timestamp(value) {
  if (!value) return "N/A";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? display(value) : date.toLocaleString("ru-RU", {timeZone: "Europe/Moscow"}) + " МСК";
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function renderWorkers(workers) {
  $("workers").replaceChildren(...workers.map((worker) => {
    const state = worker.state || {};
    const card = element("article", undefined, "worker");
    card.dataset.status = state.status || "unknown";
    card.append(element("h3", labels[worker.name]), element("span", statuses[state.status] || display(state.status), "status"));
    for (const line of [
      `Этап: ${display(state.stage)}`, `Запись: ${timestamp(state.updated_at)}`,
      `Возраст: ${age(worker.age_seconds)} · PID: ${display(state.pid)}`,
      state.next_due_at ? `Следующая проверка по runtime: ${timestamp(state.next_due_at)}` : null,
      state.last_error ? `Ошибка: ${state.last_error}` : null,
      worker.reason || null,
    ]) if (line) card.append(element("p", line));
    return card;
  }));
}
function render(data) {
  const db = data.database;
  $("snapshot-time").textContent = timestamp(data.sampled_at);
  $("source-root").textContent = `Источники: ${data.source_root}`;
  $("apply-today").textContent = count(db.apply_today);
  $("apply-day").textContent = `${db.day} · Москва · applied в БД`;
  const statusCount = (name) => db.application_statuses === null ? null : (db.application_statuses.find((group) => group.status === name)?.count ?? 0);
  for (const [id, value] of Object.entries({"live-vacancies": db.counters.vacancies, "live-evaluations": db.counters.evaluations, "live-attention": statusCount("manual_required"), "node-collect": db.counters.vacancies, "node-score": db.counters.evaluations, "node-review": statusCount("approved"), "node-apply": statusCount("applied")})) $(id).textContent = count(value);
  for (const source of ["hh", "tbank", "yandex", "vk"]) {
    $("source-" + source).textContent = count(db.source_counts == null ? null : (db.source_counts.find((group) => group.source === source)?.count ?? 0));
  }
  for (const worker of data.workers.filter((item) => item.name !== "pipeline")) {
    const moduleState = worker.state || {};
    $("module-" + worker.name).dataset.status = moduleState.status || "unknown";
    $("module-" + worker.name + "-stage").textContent = display(moduleState.stage);
    $("module-" + worker.name + "-status").textContent = (statuses[moduleState.status] || "N/A") + " · запись";
  }
  const pipeline = data.workers.find((worker) => worker.name === "pipeline");
  const state = pipeline?.state || {};
  $("pipeline-status").textContent = statuses[state.status] || display(state.status);
  $("pipeline-stage").textContent = `Этап: ${display(state.stage)}`;
  $("pipeline-age").textContent = age(pipeline?.age_seconds);
  $("pipeline-updated").textContent = timestamp(state.updated_at);
  const recentPipeline = state.status === "running" && pipeline.age_seconds !== null && pipeline.age_seconds < 120;
  const applyWorker = data.workers.find((worker) => worker.name === "apply");
  const recentApply = applyWorker?.state?.status === "running" && applyWorker.age_seconds !== null && applyWorker.age_seconds < 120;
  for (const node of document.querySelectorAll(".flow-node")) {
    const stage = node.dataset.stage;
    const recorded = (stage === "collect" && recentPipeline && String(state.stage).startsWith("collect")) || (["filter", "score"].includes(stage) && recentPipeline && state.stage === "process") || (stage === "apply" && recentApply);
    node.classList.toggle("recorded", Boolean(recorded));
  }
  renderWorkers(data.workers);
  for (const [key, value] of Object.entries(db.counters)) $("count-" + key).textContent = count(value);
  $("db-time").textContent = `БД прочитана: ${timestamp(db.sampled_at)}`;
  $("db-issues").textContent = db.issues.length ? db.issues.join(" · ") : "Источник: data/hh_agent.db · Счётчики обновляются каждые 30 секунд.";
  const groups = db.application_statuses;
  if (groups === null) $("status-counts").textContent = "N/A · Статусы недоступны";
  else if (!groups.length) $("status-counts").textContent = "0 · В таблице applications пока нет записей";
  else {
    const max = Math.max(1, ...groups.map((group) => group.count));
    $("status-counts").replaceChildren(...groups.map((group) => {
      const row = element("div", undefined, "status-row");
      const bar = element("meter");
      bar.min = 0; bar.max = max; bar.value = group.count;
      bar.setAttribute("aria-label", `${display(group.status)}: ${group.count}`);
      row.append(element("span", display(group.status)), bar, element("strong", count(group.count)));
      return row;
    }));
  }
  $("experiment-applied").textContent = count(db.experiment.applied_since_start);
  $("resume-name").textContent = db.experiment.title;
  $("resume-id").textContent = db.experiment.resume_id;
  drawBars("daily-chart", db.application_daily?.map((item) => ({label: item.day.slice(8) + "." + item.day.slice(5, 7), count: item.count})) ?? null, "#9448ff");
  drawBars("score-chart", db.evaluation_scores == null ? null : Array.from({length: 10}, (_, band) => ({label: `${band * 10}–${band === 9 ? 100 : band * 10 + 9}`, count: db.evaluation_scores.find((item) => item.band === band)?.count ?? 0})), "#ff449a");
  if ($("log-select").options.length !== data.logs.length) {
    const selected = $("log-select").value;
    $("log-select").replaceChildren(...data.logs.map((name) => {
      const option = element("option", name); option.value = name; return option;
    }));
    $("log-select").value = selected;
  }
  window.pipelineVisual?.refresh();
}
async function getJSON(url) {
  const response = await fetch(url, {cache: "no-store", signal: AbortSignal.timeout(8000)});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
let busy = false;
let timer;
let lastLog;
async function refresh() {
  if (busy) return;
  clearTimeout(timer);
  busy = true; $("refresh").disabled = true;
  const selected = $("log-select").value;
  try {
    const [snapshot, log] = await Promise.all([
      getJSON("/api/snapshot"), getJSON(`/api/logs/${encodeURIComponent(selected)}?lines=80`),
    ]);
    render(snapshot);
    if (selected === $("log-select").value) {
      const output = $("log-lines");
      const followTail = lastLog !== selected || output.scrollTop + output.clientHeight >= output.scrollHeight - 32;
      $("log-source").textContent = log.source;
      $("log-time").textContent = `Файл изменён: ${timestamp(log.modified_at)}`;
      output.textContent = log.lines === null ? `N/A · ${log.reason}` : log.lines.length ? log.lines.join("\n") : log.truncated ? "N/A · В последних 64 KiB нет полных строк" : "Лог пуст";
      if (followTail) output.scrollTop = output.scrollHeight;
      lastLog = selected;
    }
    $("connection").textContent = "Панель подключена";
    $("error").hidden = true;
    document.body.classList.remove("stale");
  } catch (_) {
    $("connection").textContent = "Нет связи с панелью";
    $("error").textContent = "Не удалось обновить данные. Показан последний полученный снимок; он может быть устаревшим.";
    $("error").hidden = false;
    document.body.classList.add("stale");
  } finally {
    busy = false; $("refresh").disabled = false;
    if (!document.hidden) timer = setTimeout(refresh, selected === $("log-select").value ? 5000 : 0);
  }
}
for (const button of document.querySelectorAll("[data-panel]")) button.addEventListener("click", () => {
  for (const panel of document.querySelectorAll(".panel")) panel.hidden = panel.id !== button.dataset.panel;
  for (const tab of document.querySelectorAll("[data-panel]")) {
    tab.classList.toggle("active", tab === button);
    tab.setAttribute("aria-pressed", String(tab === button));
  }
  $("page-title").textContent = {live: "Поток работы агента", analytics: "Накопленная статистика", resume: "Эксперимент с резюме"}[button.dataset.panel];
  window.pipelineVisual?.refresh();
});
for (const node of document.querySelectorAll(".flow-node")) node.addEventListener("click", () => {
  for (const other of document.querySelectorAll(".flow-node")) other.classList.toggle("selected", node === other);
  $("log-select").value = node.dataset.log;
  $("log-lines").textContent = "Загрузка…";
  refresh();
});
$("refresh").addEventListener("click", refresh);
$("log-select").addEventListener("change", () => { $("log-lines").textContent = "Загрузка…"; refresh(); });
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { clearTimeout(timer); }
  else refresh();
});
refresh();
