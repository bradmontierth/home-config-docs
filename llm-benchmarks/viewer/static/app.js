"use strict";

const state = { catalog: null, payloads: {}, active: "overview", selected: new Set(), query: "", status: "all" };
const icons = { doorbell: "◉", voice: "⌁", podcast: "◫", knowledge: "?" };
const subtitles = {
  doorbell: "13 hand-labelled doorstep frames · exact deployed vision prompt",
  voice: "Household utterances · model-only and production-route scoring",
  podcast: "4 diverse episodes · 20 human-approved advertising blocks",
  knowledge: "6 household questions · short-answer latency before TTS · provisional agent review, not human-approved truth"
};

const $ = (selector) => document.querySelector(selector);
function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = text;
  return item;
}
function append(parent, ...children) { children.filter(Boolean).forEach(child => parent.append(child)); return parent; }
function fmt(value, format) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (format === "percent") return `${(Number(value) * 100).toFixed(1)}%`;
  if (format === "ms") return Number(value) >= 1000 ? `${(Number(value) / 1000).toFixed(2)}s` : `${Number(value).toFixed(0)}ms`;
  if (format === "seconds") return Number(value) >= 60 ? `${(Number(value) / 60).toFixed(1)}m` : `${Number(value).toFixed(1)}s`;
  if (format === "integer") return String(Math.round(Number(value)));
  if (format === "usd") return `$${Number(value).toFixed(4)}`;
  return String(value);
}
function shortTime(seconds) {
  const s = Math.max(0, Number(seconds) || 0), m = Math.floor(s / 60), rem = Math.floor(s % 60);
  return `${m}:${String(rem).padStart(2, "0")}`;
}
function jsonBlock(value, className = "expected-json") {
  return node("pre", className, JSON.stringify(value, null, 2));
}
async function getJSON(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function init() {
  try {
    state.catalog = await getJSON("/api/catalog");
    $("#health-dot").className = "health-dot ok";
    $("#health-text").textContent = "Live catalog";
    $("#generated-at").textContent = `Catalog ${new Date(state.catalog.generated_at).toLocaleString()}`;
    buildNav(); renderOverview();
    route();
  } catch (error) {
    $("#health-dot").className = "health-dot bad";
    $("#health-text").textContent = "Viewer unavailable";
    $("#overview-grid").textContent = error.message;
  }
}

function buildNav() {
  const nav = $("#nav"); nav.replaceChildren();
  [{id: "overview", title: "Overview"}, ...state.catalog.benchmarks].forEach(item => {
    const link = node("a", "", item.title); link.href = `#${item.id}`; link.dataset.view = item.id; nav.append(link);
  });
}

function usableRuns(benchmark) {
  return state.catalog.runs.filter(run => run.benchmark === benchmark && !run.superseded);
}

function primaryRun(benchmark) { return usableRuns(benchmark)[0]; }

function renderOverview() {
  const cases = state.catalog.benchmarks.reduce((sum, bench) => sum + bench.case_count, 0);
  const runs = state.catalog.runs.filter(run => !run.superseded).length;
  $("#total-cases").textContent = cases;
  $("#total-runs").textContent = runs;
  const grid = $("#overview-grid"); grid.classList.remove("loading-card"); grid.replaceChildren();
  state.catalog.benchmarks.forEach(bench => {
    const latest = primaryRun(bench.id);
    const card = node("article", "overview-card"); card.style.setProperty("--card-accent", bench.accent);
    append(card, node("div", "card-icon", icons[bench.id]), node("h2", "", bench.title),
      node("p", "card-meta", `${bench.case_count} cases · ${bench.run_count} comparable run${bench.run_count === 1 ? "" : "s"}`));
    const metrics = node("div", "card-metrics");
    (latest?.metrics || []).slice(0, 3).forEach(metric => {
      const box = node("div", "mini-metric"); append(box, node("strong", "", fmt(metric.value, metric.format)), node("small", "", metric.label)); metrics.append(box);
    });
    card.append(metrics);
    const link = node("a", "card-link"); link.href = `#${bench.id}`;
    append(link, node("span", "", latest ? latest.label : "No results yet"), node("b", "", "Inspect →")); card.append(link);
    grid.append(card);
  });
}

async function route() {
  const target = location.hash.replace("#", "") || "overview";
  document.querySelectorAll(".nav a").forEach(link => link.classList.toggle("active", link.dataset.view === target));
  if (target === "overview" || !state.catalog.benchmarks.some(b => b.id === target)) {
    state.active = "overview"; $("#overview").classList.remove("hidden"); $("#benchmark-view").classList.add("hidden"); return;
  }
  state.active = target; $("#overview").classList.add("hidden"); $("#benchmark-view").classList.remove("hidden");
  if (!state.payloads[target]) state.payloads[target] = await getJSON(`/api/benchmark/${target}`);
  const runs = state.payloads[target].runs;
  state.selected = new Set(runs.slice(0, Math.min(2, runs.length)).map(run => run.id));
  state.query = ""; state.status = "all"; $("#search").value = ""; $("#status-filter").value = "all";
  renderBenchmark(); window.scrollTo({top: 0, behavior: "instant"});
}

function selectedRuns() {
  const payload = state.payloads[state.active];
  return (payload?.runs || []).filter(run => state.selected.has(run.id));
}

function renderBenchmark() {
  const payload = state.payloads[state.active], bench = payload.benchmark;
  document.documentElement.style.setProperty("--accent", bench.accent);
  $("#bench-eyebrow").textContent = "REAL-WORLD DATASET";
  $("#bench-title").textContent = bench.title;
  $("#bench-subtitle").textContent = subtitles[state.active];
  $("#dataset-title").textContent = state.active === "podcast" ? "Episode timelines" : "Case review";
  $("#run-count").textContent = `${payload.runs.length} run${payload.runs.length === 1 ? "" : "s"}`;
  renderRunPicker(); renderComparison(); renderCases();
}

function renderRunPicker() {
  const picker = $("#run-picker"); picker.replaceChildren();
  state.payloads[state.active].runs.forEach(run => {
    const label = node("label", "run-option"), input = document.createElement("input"); input.type = "checkbox"; input.checked = state.selected.has(run.id);
    input.addEventListener("change", () => {
      if (input.checked) state.selected.add(run.id); else if (state.selected.size > 1) state.selected.delete(run.id); else input.checked = true;
      renderComparison(); renderCases();
    });
    const copy = node("span"); append(copy, node("strong", "", run.label), node("small", "", `${run.model} · ${run.benchmark_version || "unversioned"}${run.scoring_status ? ` · ${run.scoring_status.replaceAll("_", " ")}` : ""}`));
    append(label, input, copy); picker.append(label);
  });
}

function renderComparison() {
  const runs = selectedRuns(), host = $("#comparison"); host.replaceChildren();
  if (!runs.length) { host.textContent = "Select a run."; return; }
  const labels = [...new Set(runs.flatMap(run => run.metrics.map(metric => metric.label)))];
  const table = node("table", "metric-table"), head = node("thead"), headRow = node("tr");
  append(headRow, node("th", "", "Metric"), ...runs.map(run => node("th", "", run.label))); head.append(headRow); table.append(head);
  const body = node("tbody");
  labels.forEach(label => {
    const row = node("tr"); row.append(node("td", "", label));
    runs.forEach(run => { const metric = run.metrics.find(item => item.label === label); row.append(node("td", "", metric ? fmt(metric.value, metric.format) : "—")); }); body.append(row);
  });
  table.append(body); host.append(table);
  if (new Set(runs.map(run => run.case_sha256)).size > 1) {
    host.append(node("p", "answer", "These runs use different question sets. Compare matching cases before interpreting aggregate scores."));
  }
}

function isPass(caseId, run) {
  const row = run.cases[caseId]; if (!row) return false;
  if (state.active === "doorbell") return Boolean(row.role_correct);
  if (state.active === "voice") return Boolean(row.production_exact);
  if (state.active === "knowledge") return row.review?.status === "pass";
  const metrics = row.metrics || {};
  return (metrics.blocks_missed || 0) === 0 && (metrics.missed_seconds || 0) <= 1.5 && (metrics.false_positive_seconds || 0) <= 1.5;
}

function caseHaystack(item) {
  return JSON.stringify([item.id, item.text, item.title, item.podcast, item.expected, item.expected_role, item.expected_carrier]).toLowerCase();
}

function renderCases() {
  const payload = state.payloads[state.active], runs = selectedRuns(), query = state.query.trim().toLowerCase();
  const visible = payload.dataset.filter(item => {
    if (query && !caseHaystack(item).includes(query)) return false;
    const passes = runs.map(run => isPass(item.id, run));
    if (state.status === "fail" && !passes.some(pass => !pass)) return false;
    if (state.status === "pass" && !passes.every(Boolean)) return false;
    return true;
  });
  $("#case-count").textContent = `Showing ${visible.length} of ${payload.dataset.length} cases`;
  const host = $("#cases"); host.replaceChildren();
  visible.forEach(item => host.append(state.active === "doorbell" ? doorbellCard(item, runs) : state.active === "voice" ? voiceCard(item, runs) : state.active === "knowledge" ? knowledgeCard(item, runs) : podcastCard(item, runs)));
}

function knowledgeCard(item, runs) {
  const card = cardShell(item, item.text, "Provisional review · short answer ready before synthesis", runs);
  const checks = node("p", "answer", item.checks.join(" · ")); card.append(checks);
  const panes = node("div", "run-results"); panes.style.setProperty("--run-cols", Math.max(runs.length, 1));
  runs.forEach(run => {
    const row = run.cases[item.id], pane = node("div", "case-run");
    if (!row) { pane.textContent = "Not in this run"; panes.append(pane); return; }
    pane.append(node("strong", "case-run-name", run.label));
    pane.append(node("p", "answer", row.spoken || row.error || "No answer"));
    pane.append(node("p", "latency", `${fmt(row.spoken_ready_ms, "ms")} short answer · ${fmt(row.usage?.cost, "usd")}`));
    pane.append(node("p", "answer", row.review ? `${row.review.status}: ${row.review.notes}` : "Review pending"));
    const details = document.createElement("details");
    details.append(node("summary", "", "Full answer, citations and usage"), jsonBlock({answer: row.raw_text, citations: row.annotations, usage: row.usage, tts: row.tts}, "raw-json"));
    pane.append(details); panes.append(pane);
  });
  card.append(panes); return card;
}

function statusBadge(pass, label) { return node("span", `badge ${pass ? "pass" : "fail"}`, label || (pass ? "PASS" : "MISS")); }

function cardShell(item, title, subtitle, runs, extraClass = "") {
  const card = node("article", `case-card ${extraClass}`), head = node("div", "case-head"), left = node("div");
  append(left, node("span", "case-id", item.id), node("h3", "", title), node("p", "", subtitle));
  const allPass = runs.length && runs.every(run => isPass(item.id, run)); append(head, left, statusBadge(allPass, allPass ? "ALL PASS" : "REVIEW")); card.append(head); return card;
}

function doorbellCard(item, runs) {
  const card = cardShell(item, item.id.replaceAll("_", " "), `${item.file}`, runs), body = node("div", "case-body"), fixture = node("div", "fixture");
  const image = node("img", "fixture-image"); image.src = item.image_url; image.loading = "lazy"; image.alt = item.id.replaceAll("_", " "); fixture.append(image, node("div", "fixture-label", "EXPECTED"));
  const chips = node("div", "chips"); chips.append(node("span", "chip", item.expected_role)); if (item.expected_carrier) chips.append(node("span", "chip", item.expected_carrier)); fixture.append(chips);
  const results = node("div", "run-results"); results.style.setProperty("--run-cols", Math.max(runs.length, 1));
  runs.forEach(run => {
    const row = run.cases[item.id], pane = node("div", "case-run"); if (!row) { pane.textContent = "Not in this run"; results.append(pane); return; }
    const head = node("div", "case-run-head"); append(head, node("span", "case-run-name", run.label), statusBadge(row.role_correct)); pane.append(head);
    pane.append(node("p", "answer", row.raw_response || row.error || "No response"));
    const got = node("div", "chips"); got.append(node("span", "chip", row.predicted_role || "invalid")); if (row.predicted_carrier) got.append(node("span", "chip", row.predicted_carrier)); pane.append(got, node("p", "latency", row.latency_ms == null ? "no latency" : `${row.latency_ms.toFixed(1)} ms`)); results.append(pane);
  });
  append(body, fixture, results); card.append(body); return card;
}

function voiceCard(item, runs) {
  const mode = item.mode || "fresh", card = cardShell(item, `“${item.text}”`, `${mode} turn · expected ${item.expected.intent}`, runs), body = node("div", "case-body"), fixture = node("div", "fixture");
  fixture.append(node("div", "fixture-label", "EXPECTED SUBSET"), jsonBlock(item.expected));
  const route = node("div", "chips"); route.append(node("span", "chip", `${item.expected_route} route`)); if (item.risk) route.append(node("span", "chip", item.risk)); fixture.append(node("div", "fixture-label", "ROUTING"), route);
  if (item.context) fixture.append(node("div", "fixture-label", "CONTEXT"), node("p", "answer", item.context));
  const results = node("div", "run-results"); results.style.setProperty("--run-cols", Math.max(runs.length, 1));
  runs.forEach(run => {
    const row = run.cases[item.id], pane = node("div", "case-run"); if (!row) { pane.textContent = "Not in this run"; results.append(pane); return; }
    const head = node("div", "case-run-head"); append(head, node("span", "case-run-name", run.label), statusBadge(row.production_exact)); pane.append(head);
    const prod = row.production_prediction || {}, model = row.model_prediction || {};
    pane.append(node("p", "prediction", prod.intent || "invalid"));
    if (model.intent !== prod.intent) pane.append(node("p", "subprediction", `model-only: ${model.intent || "invalid"} → production fast path`));
    const chips = node("div", "chips"); chips.append(node("span", "chip", row.actual_route || "unknown route")); if (row.production_failed_fields?.length) chips.append(node("span", "chip", `failed: ${row.production_failed_fields.join(", ")}`)); pane.append(chips, node("p", "latency", `${Number(row.latency_ms || 0).toFixed(1)} ms`));
    const details = document.createElement("details"), summary = node("summary", "", "Prediction and raw JSON"); append(details, summary, jsonBlock({production: prod, model_only: model, raw: row.raw_response}, "raw-json")); pane.append(details); results.append(pane);
  });
  append(body, fixture, results); card.append(body); return card;
}

function timelineRow(label, intervals, duration, className = "") {
  const row = node("div", `timeline-row ${className}`), bar = node("div", "timeline");
  (intervals || []).forEach(interval => { const span = node("span", "timeline-span"); span.style.left = `${Math.max(0, interval[0] / duration * 100)}%`; span.style.width = `${Math.max(.12, (interval[1] - interval[0]) / duration * 100)}%`; span.title = `${shortTime(interval[0])}–${shortTime(interval[1])}`; bar.append(span); });
  append(row, node("div", "timeline-label", label), bar); return row;
}

function podcastCard(item, runs) {
  const truth = item.truth_data, duration = Number(truth.duration_seconds), card = cardShell(item, item.title, `${item.podcast} · ${shortTime(duration)} · ${truth.ad_intervals.length} approved blocks`, runs, "podcast-card");
  const timeline = node("div", "timeline-wrap"); timeline.append(timelineRow("Approved truth", truth.ad_intervals, duration, "truth-row"));
  runs.forEach(run => { const row = run.cases[item.id]; timeline.append(timelineRow(run.label, row?.predicted_intervals || [], duration)); });
  const axis = node("div", "timeline-axis"); append(axis, node("span", "", "0:00"), node("span", "", shortTime(duration))); timeline.append(axis); card.append(timeline);
  const panes = node("div", "podcast-runs"); panes.style.setProperty("--run-cols", Math.max(runs.length, 1));
  runs.forEach(run => {
    const row = run.cases[item.id], pane = node("div", "case-run"); if (!row) { pane.textContent = "Not in this run"; panes.append(pane); return; }
    const metrics = row.metrics || {}, pass = isPass(item.id, run), head = node("div", "case-run-head"); append(head, node("span", "case-run-name", run.label), statusBadge(pass)); pane.append(head);
    const grid = node("div", "podcast-metric-grid"); [["F1", metrics.f1, "percent"], ["Recall", metrics.recall, "percent"], ["Precision", metrics.precision, "percent"]].forEach(([label, value, format]) => { const box = node("div", "mini-metric"); append(box, node("strong", "", fmt(value, format)), node("small", "", label)); grid.append(box); }); pane.append(grid);
    pane.append(node("p", "latency", `${metrics.blocks_detected || 0}/${metrics.ad_blocks || 0} blocks · ${(metrics.missed_seconds || 0).toFixed(2)}s missed · ${(metrics.false_positive_seconds || 0).toFixed(2)}s extra`)); panes.append(pane);
  });
  card.append(panes);
  const evidence = document.createElement("details"); evidence.className = "evidence"; evidence.append(node("summary", "", "Approved boundary evidence"));
  item.boundary_evidence.forEach(block => {
    const group = node("div", "evidence-block"), texts = [...block.opening, ...block.closing].map(seg => seg.text).filter(Boolean);
    append(group, node("div", "evidence-time", `${shortTime(block.start)}–${shortTime(block.end)}`), node("p", "", texts.join(" "))); evidence.append(group);
  }); card.append(evidence); return card;
}

$("#search").addEventListener("input", event => { state.query = event.target.value; renderCases(); });
$("#status-filter").addEventListener("change", event => { state.status = event.target.value; renderCases(); });
window.addEventListener("hashchange", () => route().catch(console.error));
init();
