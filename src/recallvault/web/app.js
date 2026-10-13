"use strict";
const $ = id => document.getElementById(id);
const kinds = {preference: "✦ 偏好", fact: "◇ 事实", episode: "◷ 事件"};
const statuses = {active: "● 有效", superseded: "◌ 历史版本", expired: "◷ 已过期", forgotten: "○ 已遗忘"};
let scope = {namespace: "demo", user_id: "alice"}, generation = 0, memories = [], pendingCorrection = null;
const uid = () => crypto.randomUUID();
const scopeQuery = target => new URLSearchParams(target).toString();
function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
function message(text, error = false) { $("notice").textContent = text; $("notice").className = "notice" + (error ? " error" : ""); $("notice").hidden = false; }
function errorText(error) { return error.message || "请求失败，请检查本地服务。"; }
async function api(path, method = "GET", body) {
  const response = await fetch(path, {method, headers: body ? {"Content-Type": "application/json"} : {}, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(120000)});
  const data = await response.json();
  if (!response.ok) { const detail = Array.isArray(data.detail) ? data.detail.map(item => `${item.loc?.join(".") || "参数"}: ${item.msg}`).join("；") : data.detail; const error = new Error(detail || `请求失败 (${response.status})`); error.status = response.status; throw error; }
  return data;
}
async function busy(button, operation) { const label = button.textContent; button.disabled = true; button.textContent = "处理中…"; try { await operation(); } catch (error) { message(errorText(error), true); } finally { button.disabled = false; button.textContent = label; } }
function date(value) { if (!value) return "永久保留"; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}); }
function resetAnswer() { $("answer").replaceChildren(el("span", "answer-label", "NEW MEMORY SCOPE"), el("h3", "", "当前空间已就绪"), el("p", "", "输入一个问题，观察 Agent 如何召回有效记忆。")); }
async function refresh(expected = generation, target = {...scope}) {
  const [summary, data, traces] = await Promise.all([api(`/api/summary?${scopeQuery(target)}`), api(`/api/memories?${scopeQuery(target)}&include_inactive=true`), api(`/api/traces?${scopeQuery(target)}`)]);
  if (expected !== generation) return;
  memories = data.memories;
  ["active", "superseded", "expired", "forgotten"].forEach(key => $("stat-" + key).textContent = summary[key] ?? 0);
  renderMemories(); renderTraces(traces.traces);
}
function renderMemories() {
  const search = $("filter").value.toLowerCase(), selected = $("status-filter").value;
  const rows = memories.filter(memory => (selected === "all" || memory.status === selected) && [memory.text || "", memory.key].some(value => value.toLowerCase().includes(search)));
  $("memory-count").textContent = rows.length;
  $("memory-list").replaceChildren();
  if (!rows.length) { $("memory-list").append(el("div", "empty", "这个视图还没有记忆。\n添加一段对话，或载入下方示例开始体验。")); return; }
  rows.forEach(memory => {
    const card = el("button", "memory-card"); card.type = "button";
    const top = el("div", "card-top"); top.append(el("span", "kind " + memory.kind, kinds[memory.kind]), el("span", "key", memory.key), el("span", "revision", `v${memory.revision}`));
    const bottom = el("div", "card-bottom"); bottom.append(el("span", "status " + memory.status, statuses[memory.status]), el("span", "", memory.expires_at ? `到期 ${date(memory.expires_at)}` : `创建 ${date(memory.created_at)}`));
    card.append(top, el("div", "memory-text", memory.text || "此记忆内容已清除"), el("div", "memory-source", memory.source_quote ? `“${memory.source_quote}”` : "仅保留墓碑元数据"), bottom);
    card.addEventListener("click", () => showDetail(memory)); $("memory-list").append(card);
  });
}
function renderTraces(traces) {
  $("trace-list").replaceChildren();
  if (!traces.length) { $("trace-list").append(el("div", "empty", "还没有检索轨迹。向记忆助手发送第一个问题。")); return; }
  traces.slice(0, 8).forEach(trace => { const row = el("div", "trace-row"); row.append(el("span", "", trace.kind.toUpperCase()), el("span", "", trace.query || "遗忘操作后，敏感轨迹已清除"), el("time", "", date(trace.created_at))); $("trace-list").append(row); });
}
function showDetail(memory) {
  const content = $("detail-content"); content.replaceChildren(el("div", "detail-text", memory.text || "记忆内容已清除"));
  const table = el("dl", "detail-table");
  const rows = [["记忆 ID", memory.id], ["记忆键", memory.key], ["类型 / 版本", `${kinds[memory.kind]} / v${memory.revision}`], ["状态", statuses[memory.status]], ["所属空间", `${memory.namespace} / ${memory.user_id}`], ["来源会话", memory.session_id || "已清除"], ["来源轮次", memory.source_turn_id || "已清除"], ["来源原文", memory.source_quote || "已清除"], ["到期时间", date(memory.expires_at)]];
  rows.forEach(([key, value]) => table.append(el("dt", "", key), el("dd", "", value))); content.append(table);
  if (memory.status !== "forgotten") {
    const box = el("div", "forget-confirm"), label = el("label"), check = document.createElement("input"), button = el("button", "danger-button", "遗忘这条记忆及其历史"); check.type = "checkbox"; label.append(check, document.createTextNode("确认清除同一记忆键的全部版本、来源会话内容和检索缓存。此操作无法撤销。")); button.disabled = true;
    check.addEventListener("change", () => button.disabled = !check.checked);
    const target = {...scope}, expected = generation;
    button.addEventListener("click", () => busy(button, async () => { await api(`/api/memories/${encodeURIComponent(memory.id)}?${scopeQuery(target)}`, "DELETE"); if (expected !== generation) return; $("detail-dialog").close(); pendingCorrection = null; $("correction-dialog").close(); $("ingest-dialog").close(); $("candidates").replaceChildren(); $("conversation").value = ""; resetAnswer(); await refresh(expected, target); message("记忆与相关历史已清除，下一轮对话无法再召回这些内容。"); }));
    box.append(label, button); content.append(box);
  }
  $("detail-dialog").showModal();
}
function renderAnswer(data, retrievalOnly) {
  const container = $("answer"); container.replaceChildren(el("span", "answer-label", retrievalOnly ? "RETRIEVED CONTEXT" : "MEMORY-AWARE ANSWER"));
  if (retrievalOnly) {
    container.append(el("h3", "", `找到 ${data.hits.length} 条相关记忆`));
    data.hits.forEach(hit => { const citation = el("div", "citation"); citation.append(el("small", "", `${hit.memory.key} · v${hit.memory.revision} · ${hit.channels.join(" + ")}`), el("div", "", hit.memory.text)); container.append(citation); });
  } else {
    container.append(el("h3", "", data.mode === "demo" ? "离线摘录回答" : "助手回答"), el("div", "answer-content", data.answer));
    data.citations.forEach(cite => { const citation = el("div", "citation"); citation.append(el("small", "", `${cite.memory_id} · v${cite.revision}`), el("div", "", cite.quote)); container.append(citation); });
  }
  container.append(el("div", "answer-meta", `TRACE ${data.trace_id}${retrievalOnly ? " · " + data.strategy.toUpperCase() : " · 引用已经过当前版本校验"}`));
}
async function ask(button, retrievalOnly) {
  if (!$("question").reportValidity()) return;
  const target = {...scope}, expected = generation, query = $("question").value.trim();
  if (!query) return;
  await busy(button, async () => { const data = await api(retrievalOnly ? "/api/recall" : "/api/ask", "POST", {...target, ...(retrievalOnly ? {query, limit: 5} : {question: query})}); if (expected !== generation) return; renderAnswer(data, retrievalOnly); await refresh(expected, target); });
}
function candidateForm(candidate, sessionId, target, expected) {
  const form = el("form", "candidate"), fields = el("div", "fields");
  const makeInput = (title, value, type = "text") => { const wrap = el("div"), label = el("label", "", title), input = document.createElement("input"); input.type = type; input.value = value ?? ""; label.append(input); wrap.append(label); return {wrap, input}; };
  const key = makeInput("记忆键", candidate.key); key.input.required = true; key.input.pattern = "[a-z0-9_.-]+"; key.input.maxLength = 64;
  const ttl = makeInput("有效秒数（留空永久）", candidate.ttl_seconds, "number"); ttl.input.min = "1"; ttl.input.max = "31536000"; ttl.input.step = "1";
  fields.append(key.wrap, ttl.wrap);
  const kindLabel = el("label", "", "记忆类型"), kind = document.createElement("select"); Object.entries(kinds).forEach(([value, text]) => { const option = el("option", "", text); option.value = value; option.selected = value === candidate.kind; kind.append(option); }); kindLabel.append(kind);
  const textLabel = el("label", "", "记忆内容"), text = document.createElement("textarea"); text.value = candidate.text; text.required = true; text.maxLength = 2000; text.rows = 2; textLabel.append(text);
  const button = el("button", "primary", "保存记忆"); button.type = "submit";
  form.append(el("h3", "", "候选记忆"), fields, kindLabel, textLabel, el("p", "source", `来源原文：“${candidate.source_quote}”`), button);
  form.addEventListener("submit", event => { event.preventDefault(); const payload = {...target, session_id: sessionId, candidate: {...candidate, key: key.input.value, kind: kind.value, text: text.value, ttl_seconds: ttl.input.value === "" ? null : Number(ttl.input.value)}, expected_revision: null, idempotency_key: uid()}; busy(button, async () => {
    if (expected !== generation) return;
    try { const data = await api("/api/memories", "POST", payload); if (expected !== generation) return; form.replaceChildren(el("p", "", data.duplicate ? "相同记忆已存在，无需重复保存。" : `已保存 ${data.memory.key} · v${data.memory.revision}`)); await refresh(expected, target); message("记忆已保存，可以在新的对话中召回。"); }
    catch (error) { if (error.status !== 409) throw error; await refresh(expected, target); if (expected !== generation) return; const current = memories.filter(memory => memory.key === payload.candidate.key && memory.status !== "forgotten").sort((a, b) => b.revision - a.revision)[0]; if (!current) throw error; pendingCorrection = {payload, current, form, expected, target}; $("correction-content").replaceChildren(); [[`当前版本 v${current.revision}`, current.text], ["新版本", payload.candidate.text]].forEach(([title, value]) => { const box = el("div", "version-block"); box.append(el("small", "", title), el("p", "", value)); $("correction-content").append(box); }); $("correction-dialog").showModal(); }
  }); });
  return form;
}
$("ingest-form").addEventListener("submit", event => { event.preventDefault(); const target = {...scope}, expected = generation, text = $("conversation").value.trim(), sessionId = "session-" + uid(); if (!text) return; busy(event.submitter, async () => {
  const data = await api("/api/ingest", "POST", {...target, session_id: sessionId, turns: [{id: "turn-1", role: "user", text}], idempotency_key: uid()});
  if (expected !== generation) return;
  $("candidates").replaceChildren();
  if (data.candidates.length) data.candidates.forEach(candidate => $("candidates").append(candidateForm(candidate, sessionId, target, expected)));
  else { $("candidates").append(el("p", "micro", "本次没有自动提取到记忆。你可以将原始陈述作为事实手动保存：")); $("candidates").append(candidateForm({kind: "fact", key: "custom." + uid().slice(0, 8), text: text.slice(0, 2000), source_turn_id: "turn-1", source_quote: text.slice(0, 2000), ttl_seconds: null}, sessionId, target, expected)); }
  await refresh(expected, target);
}); });
$("confirm-correction").addEventListener("click", event => busy(event.currentTarget, async () => {
  const item = pendingCorrection; if (!item || item.expected !== generation) return;
  const data = await api("/api/memories", "POST", {...item.payload, expected_revision: item.current.revision, idempotency_key: uid()});
  if (item.expected !== generation) return;
  item.form.replaceChildren(el("p", "", `已更新为 v${data.memory.revision}，旧版本不再参与检索。`)); pendingCorrection = null; $("correction-dialog").close(); resetAnswer(); await refresh(item.expected, item.target); message("记忆更新成功。历史版本仍可在仓库中查看。");
}));
$("scope-form").addEventListener("submit", event => { event.preventDefault(); scope = {namespace: $("namespace").value.trim(), user_id: $("user-id").value.trim()}; generation++; memories = []; pendingCorrection = null; document.querySelectorAll("dialog").forEach(dialog => dialog.close()); $("candidates").replaceChildren(); $("conversation").value = ""; $("question").value = ""; $("filter").value = ""; $("status-filter").value = "active"; resetAnswer(); renderMemories(); renderTraces([]); ["active", "superseded", "expired", "forgotten"].forEach(key => $("stat-" + key).textContent = "—"); busy(event.submitter, async () => { await refresh(); message(`已切换到 ${scope.namespace} / ${scope.user_id}`); }); });
$("filter").addEventListener("input", renderMemories); $("status-filter").addEventListener("change", renderMemories);
$("ask-form").addEventListener("submit", event => { event.preventDefault(); ask(event.submitter, false); });
$("recall").addEventListener("click", event => ask(event.currentTarget, true));
$("new-memory").addEventListener("click", () => $("ingest-dialog").showModal());
$("seed").addEventListener("click", event => busy(event.currentTarget, async () => { const target = {...scope}, expected = generation; const data = await api("/api/demo/seed", "POST", target); if (expected !== generation) return; await refresh(expected, target); message(data.created ? "三条示例记忆已载入。现在可以试试跨会话召回。" : "这个空间已初始化过。现有记忆与遗忘状态已保留。"); }));
$("refresh").addEventListener("click", event => busy(event.currentTarget, () => refresh()));
document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => $(button.dataset.close).close()));
document.querySelectorAll("[data-question]").forEach(button => button.addEventListener("click", () => { $("question").value = button.dataset.question; $("question").focus(); }));
(async () => { try { const health = await api("/api/health"); $("mode").textContent = health.mode === "demo" ? "● 离线演示模式" : "● 模型连接模式"; $("strategy").textContent = health.embeddings ? "BM25 + Embeddings · RRF 融合" : "BM25 中英双语检索"; await refresh(); } catch (error) { $("mode").textContent = "服务连接失败"; message(errorText(error), true); } })();
