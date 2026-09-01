const state = {
  csrf: "",
  route: "events",
  events: [],
  selectedEvent: null,
  selectedTrack: 0,
  selectedClusterTracks: {},
  people: [],
  clusters: [],
  modalHandler: null,
};

const $ = (selector) => document.querySelector(selector);
const content = $("#content");
const modal = $("#modal");
const { dayLabel, formatDate } = globalThis.DoorlockTime;
const relationshipLabels = {
  family: "家人",
  neighbor: "邻居",
  courier: "快递员",
  cleaner: "保洁",
  visitor: "访客",
  stranger: "陌生人",
  other: "其他",
};
const routeCopy = {
  events: ["门口发生了什么", "按北京时间排列，训练期不发送身份通知。"],
  people: ["人物与未知簇", "只把清晰、可核对的样本纳入学习；人工决定均可撤销。"],
  operations: ["失败与备份状态", "下载、分析、通知和备份回执都不会静默失败。"],
  settings: ["通知与安全", "身份和风险通知默认关闭；运行故障通知始终开启。"],
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function idempotency() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

function bytes(value) {
  if (!Number.isFinite(value)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size.toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function eventTitle(event) {
  const labels = {
    doorbell: "门铃录像",
    linger: "有人在门前停留",
    passed: "有人经过",
    unlock: "开锁录像",
    video: "门锁录像",
  };
  return labels[event.event_type] || "门锁录像";
}

function eventState(event) {
  if (event.analysis_state === "failed") return ["failed", "分析失败"];
  if (!event.tracks?.length) return ["skipped", "已跳过"];
  if (event.tracks.some((track) => track.person)) return ["known", "已识别"];
  return ["pending", "待确认"];
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (state.csrf && options.method && options.method !== "GET") headers["X-CSRF-Token"] = state.csrf;
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("application/json") ? await response.json() : null;
  if (response.status === 401) {
    showLogin();
    throw new Error(payload?.detail || "登录已失效");
  }
  if (!response.ok) throw new Error(payload?.detail || `请求失败（${response.status}）`);
  return payload;
}

function showLogin() {
  state.csrf = "";
  $("#shell").hidden = true;
  $("#login").hidden = false;
  setTimeout(() => $("#password").focus(), 0);
}

function showShell() {
  $("#login").hidden = true;
  $("#shell").hidden = false;
}

function showLoading() {
  content.innerHTML = '<div class="loading"><span></span>正在整理记录…</div>';
}

function showError(error) {
  content.innerHTML = `<div class="empty"><strong>暂时无法读取记录</strong><p>${esc(error.message)}</p><button class="button quiet" data-action="reload">重新尝试</button></div>`;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 3200);
}

function setHeader(route) {
  const copy = routeCopy[route] || routeCopy.events;
  $("#page-title").textContent = copy[0];
  $("#page-subtitle").textContent = copy[1];
  document.querySelectorAll("nav a[data-route]").forEach((link) => {
    if (link.dataset.route === route) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function summary(data) {
  const counts = data.counts;
  return `<div class="summary-strip">
    <div class="summary-item"><span>录像记录</span><strong>${counts.events}</strong></div>
    <div class="summary-item"><span>已确认人物</span><strong>${counts.people}</strong></div>
    <div class="summary-item"><span>待确认人物簇</span><strong>${counts.review_clusters}</strong></div>
    <div class="summary-item"><span>分析失败</span><strong>${counts.failed_analysis}</strong></div>
    <div class="summary-item"><span>待备份回执</span><strong>${counts.backup_pending}</strong></div>
  </div>`;
}

function eventRow(event) {
  const [className, label] = eventState(event);
  const people = event.tracks?.filter((track) => track.person).map((track) => track.person.display_name) || [];
  const detail = people.length
    ? `已匹配：${people.join("、")} · ${event.duration_seconds} 秒`
    : `${event.track_count} 人 · 跳过 ${event.skipped_face_count} 帧 · ${event.duration_seconds} 秒`;
  return `<button class="event-row" data-action="select-event" data-id="${esc(event.id)}" aria-selected="${state.selectedEvent?.id === event.id}">
    <span class="time">${esc(formatDate(event.occurred_at))}<small>${esc(dayLabel(event.occurred_at))}</small></span>
    <span class="event-copy"><strong>${esc(eventTitle(event))}</strong><small>${esc(detail)}</small></span>
    <span class="status ${className}">${label}</span>
  </button>`;
}

function media(url, kind, label) {
  if (!url) return `<div class="media-placeholder">${esc(label)}</div>`;
  if (kind === "video") return `<video class="media-frame" controls preload="metadata" src="${esc(url)}"></video>`;
  return `<img class="${kind === "face" ? "face-frame" : "media-frame"}" src="${esc(url)}" alt="${esc(label)}" loading="lazy">`;
}

function eventDetail(event) {
  if (!event) return '<article class="detail"><div class="empty"><strong>选择一条录像</strong><p>左侧记录会在这里展开原视频、最佳人脸和判断依据。</p></div></article>';
  const tracks = event.tracks || [];
  const index = Math.min(state.selectedTrack, Math.max(0, tracks.length - 1));
  const track = tracks[index];
  const [, statusLabel] = eventState(event);
  const tabs = tracks.length > 1
    ? `<div class="track-tabs" aria-label="同框人物">${tracks.map((item, i) => `<button class="track-tab" data-action="select-track" data-index="${i}" aria-pressed="${i === index}">人物 ${i + 1}</button>`).join("")}</div>`
    : "";
  const identity = track?.person ? track.person.display_name : track?.cluster_id ? `未知人物 ${track.cluster_id.slice(-6)}` : "未形成可用人物样本";
  const reason = track?.person
    ? `关系：${relationshipLabels[track.person.relationship] || track.person.relationship}。匹配分数 ${track.similarity?.toFixed(3) || "—"}。`
    : track?.cluster_id
      ? "保持未知，等待您确认；同框人物已分别跟踪且不会互相合并。"
      : "未检测到足够清晰的人脸，本次分析已跳过。";
  const actions = track?.cluster_id
    ? `<div class="card-actions"><button class="button primary" data-action="label-cluster" data-id="${esc(track.cluster_id)}">确认并命名</button><button class="button quiet" data-action="false-positive" data-id="${esc(track.cluster_id)}">标记为误检</button></div>`
    : track?.person
      ? `<div class="card-actions"><button class="button quiet" data-action="rename-person" data-id="${esc(track.person.id)}">修正人物信息</button></div>`
      : "";
  return `<article class="detail">
    <div class="detail-top"><div><h2>${esc(dayLabel(event.occurred_at))} ${esc(formatDate(event.occurred_at))} · ${esc(eventTitle(event))}</h2><div class="detail-meta"><span>时长 ${event.duration_seconds} 秒</span><span>检测到 ${event.track_count} 人</span><span>${event.skipped_face_count} 条跳过记录</span></div></div><span class="stamp">${esc(statusLabel)}</span></div>
    ${tabs}
    <p class="evidence-title">系统为何这样判断</p>
    <div class="evidence">
      <div class="evidence-item">${media(event.video_url, "video", "录像不在本地")}<span class="evidence-label">原录像</span><span class="evidence-value">${esc(eventTitle(event))} · ${event.duration_seconds} 秒</span></div>
      <div class="evidence-item">${media(track?.face_url, "face", "无清晰人脸")}<span class="evidence-label">最佳人脸</span><span class="evidence-value">${track ? `质量 ${Math.round(track.quality_score * 100)}%` : "已跳过"}</span></div>
      <div class="evidence-item decision"><span class="evidence-label">人物决定</span><strong>${esc(identity)}</strong><p>${esc(reason)}</p></div>
    </div>
    <div class="timeline"><div class="step"><strong>${esc(formatDate(event.downloaded_at, true))} · 录像就绪</strong>下载器完成校验后写入只读目录</div><div class="step"><strong>NAS 分析完成</strong>${event.track_count ? `${event.track_count} 条独立人物轨迹` : "画面不清晰，保守跳过"}</div><div class="step"><strong>${track?.person ? "人工结果已生效" : "等待人工确认"}</strong>所有人工决定都会留痕并可撤销</div></div>
    ${actions}<p class="footnote">识别结果只用于整理和后续通知，永远不会触发开锁。</p>
  </article>`;
}

async function renderEvents() {
  const [bootstrap, result] = await Promise.all([api("/api/bootstrap"), api("/api/events?limit=100")]);
  state.events = result.items;
  if (!state.selectedEvent || !state.events.some((item) => item.id === state.selectedEvent.id)) state.selectedEvent = state.events[0] || null;
  $("#rail-health").textContent = bootstrap.analysis.ready ? "运行正常" : "模型待就绪";
  $("#rail-note").textContent = bootstrap.analysis.ready ? "持续核对新录像" : "请查看运行页";
  if (!state.events.length) {
    content.innerHTML = `${summary(bootstrap)}<div class="empty"><strong>还没有可查看的门锁录像</strong><p>下载器写入第一段录像后会自动出现在这里。</p><a class="button quiet" href="#operations">查看系统状态</a></div>`;
    return;
  }
  content.innerHTML = `${summary(bootstrap)}<section class="workspace" aria-label="事件与证据"><div class="ledger"><div class="ledger-head"><span>时间</span><span>事件</span><span>结果</span></div>${state.events.map(eventRow).join("")}</div>${eventDetail(state.selectedEvent)}</section>`;
}

function face(url, label, className = "face-frame") {
  if (!url) return `<div class="${esc(className)} media-placeholder">无图</div>`;
  return `<img class="${esc(className)}" src="${esc(url)}" alt="${esc(label)}" loading="lazy">`;
}

function selectedClusterTrack(cluster) {
  const selectedId = state.selectedClusterTracks[cluster.id];
  return cluster.tracks.find((track) => track.id === selectedId) || cluster.tracks[0] || null;
}

function clusterCard(cluster) {
  const selected = selectedClusterTrack(cluster);
  const selectedIndex = selected ? cluster.tracks.findIndex((track) => track.id === selected.id) : -1;
  const sampleNumber = selectedIndex >= 0 ? selectedIndex + 1 : null;
  const poster = selected?.preview_url ? ` poster="${esc(selected.preview_url)}"` : "";
  const video = selected?.video_url
    ? `<video class="cluster-video" controls preload="none" playsinline src="${esc(selected.video_url)}"${poster} aria-label="未知人物样本 ${sampleNumber} 对应录像"></video>`
    : '<div class="cluster-video media-placeholder">对应录像已不在本地</div>';
  const samples = cluster.tracks.map((track, index) => {
    const label = `未知人物样本 ${index + 1}`;
    return `<button type="button" class="face-choice" data-action="select-cluster-track" data-id="${esc(cluster.id)}" data-track-id="${esc(track.id)}" aria-pressed="${track.id === selected?.id}" aria-label="查看样本 ${index + 1} 的大图和录像">${face(track.face_url || track.preview_url, label, "cluster-thumb")}</button>`;
  }).join("");
  const mergeAction = state.people.length > 0 || state.clusters.length > 1
    ? `<button class="button quiet small" data-action="merge-cluster" data-id="${esc(cluster.id)}">合并到…</button>`
    : "";
  const splitAction = cluster.tracks.length > 1
    ? `<button class="button quiet small" data-action="split-cluster" data-id="${esc(cluster.id)}">拆分错分样本</button>`
    : "";
  const largeFaceLabel = selected ? `未知人物样本 ${sampleNumber} 大图` : "暂无可用人物大图";
  const quality = selected ? `人脸质量 ${Math.round(selected.quality_score * 100)}%` : "暂无可用样本";
  const videoMeta = selected ? `${esc(formatDate(selected.occurred_at, true))} · ${selected.duration_seconds} 秒` : "没有对应录像";
  return `<article class="cluster-card" data-cluster-id="${esc(cluster.id)}">
    <div class="cluster-head"><div><h3>未知人物 ${esc(cluster.id.slice(-6))}</h3><p>${cluster.event_count} 次录像 · ${cluster.distinct_days} 天 · ${cluster.high_quality_count} 张高质量样本</p></div><span class="status pending">待确认</span></div>
    <div class="cluster-review"><div class="cluster-primary">${face(selected?.face_url || selected?.preview_url, largeFaceLabel, "cluster-face-large")}<strong>样本 ${sampleNumber || "—"}</strong><span>${quality}</span></div><div class="cluster-video-wrap">${video}<p>${videoMeta}</p></div></div>
    <div class="face-strip" role="group" aria-label="选择人物簇样本">${samples}</div>
    <div class="card-actions"><button class="button primary small" data-action="label-cluster" data-id="${esc(cluster.id)}">核对并确认</button>${mergeAction}${splitAction}<button class="button quiet small" data-action="false-positive" data-id="${esc(cluster.id)}">标记误检</button></div>
  </article>`;
}

function renderPeopleContent() {
  const peopleHtml = state.people.length
    ? `<div class="person-grid">${state.people.map((person) => `<article class="person-card">${face(person.face_url, person.display_name)}<h3>${esc(person.display_name)}</h3><p>${esc(relationshipLabels[person.relationship] || person.relationship)} · ${person.matched_events} 次 · ${person.distinct_days} 天</p><div class="card-actions"><button class="button small quiet" data-action="rename-person" data-id="${esc(person.id)}">修改</button>${state.people.length > 1 ? `<button class="button small quiet" data-action="merge-person" data-id="${esc(person.id)}">合并到…</button>` : ""}</div></article>`).join("")}</div>`
    : '<div class="empty"><strong>还没有已确认人物</strong><p>确认下方未知人物簇后，会在这里持续积累清晰代表样本。</p></div>';
  const clustersHtml = state.clusters.length
    ? state.clusters.map(clusterCard).join("")
    : '<div class="empty"><strong>还没有待确认的人物</strong><p>清晰人脸会在多次出现后进入这里；不清晰画面不会被强行学习。</p></div>';
  content.innerHTML = `<div class="section-head"><div><h2>已确认人物</h2><p>永久保留高质量代表样本，数量不设硬上限。</p></div></div>${peopleHtml}<div class="section-head"><div><h2>待确认人物簇</h2><p>选择样本查看大图和对应录像；多人同框仍保持独立。</p></div></div>${clustersHtml}`;
}

async function renderPeople() {
  const [peopleData, clusterData] = await Promise.all([api("/api/people"), api("/api/clusters")]);
  state.people = peopleData.items;
  state.clusters = clusterData.items;
  renderPeopleContent();
}

function tableRows(items, columns) {
  if (!items.length) return `<tr><td colspan="${columns.length}">暂无记录</td></tr>`;
  return items.map((item) => `<tr>${columns.map((column) => `<td class="${column.class || ""}">${column.render(item)}</td>`).join("")}</tr>`).join("");
}

async function renderOperations() {
  const [system, operations] = await Promise.all([api("/api/system"), api("/api/operations")]);
  const backup = system.backup_counts || {};
  const outbox = system.outbox_counts || {};
  content.innerHTML = `<div class="system-grid">
    <div class="system-cell"><span>识别服务</span><strong>${system.service.analysis_ready ? "已就绪" : "待处理"}</strong></div>
    <div class="system-cell"><span>可用空间</span><strong>${bytes(system.storage.free_bytes)}</strong></div>
    <div class="system-cell"><span>模型</span><strong>${esc(system.model.active ? "已锁定" : "未就绪")}</strong></div>
    <div class="system-cell"><span>待备份回执</span><strong>${backup.pending || 0}</strong></div>
    <div class="system-cell"><span>通知待发送</span><strong>${outbox.pending || 0}</strong></div>
    <div class="system-cell"><span>通知死信</span><strong>${outbox.dead || 0}</strong></div>
  </div>
  <div class="section-head"><div><h2>需要处理的失败</h2><p>分析已经自动按 5、20、60 分钟重试；仍失败时在这里手工重试。</p></div></div>
  <table class="ledger-table"><thead><tr><th>北京时间</th><th>文件</th><th>原因</th><th>操作</th></tr></thead><tbody>${tableRows(system.failed_ingests || [], [
    { render: (row) => esc(formatDate(row.updated_at, true)) },
    { class: "long", render: (row) => esc(row.file_name) },
    { class: "long", render: (row) => esc(row.error_code || row.error || "未知原因") },
    { render: (row) => `<button class="button small quiet" data-action="retry-ingest" data-id="${esc(row.id)}">重新分析</button>` },
  ])}</tbody></table>
  <div class="section-head"><div><h2>下载器回报</h2><p>本系统只接收下载状态，不读取小米或 Home Assistant 凭据。</p></div></div>
  <table class="ledger-table"><thead><tr><th>下载器记录时间（北京时间）</th><th>状态</th><th>尝试</th><th>错误</th></tr></thead><tbody>${tableRows(system.download_reports || [], [
    { render: (row) => esc(formatDate(row.event_time, true)) },
    { render: (row) => esc(row.state) },
    { render: (row) => esc(row.attempts) },
    { class: "long", render: (row) => esc(row.error_code || "—") },
  ])}</tbody></table>
  <div class="section-head"><div><h2>人工操作与撤销</h2><p>命名、合并、拆分和误检决定均保留审计记录。</p></div></div>
  <table class="ledger-table"><thead><tr><th>北京时间</th><th>操作</th><th>对象</th><th>状态</th></tr></thead><tbody>${tableRows(operations.items || [], [
    { render: (row) => esc(formatDate(row.created_at, true)) },
    { render: (row) => esc(row.operation_label || "人工操作") },
    { class: "long", render: (row) => esc(row.subject_label || "人工操作记录") },
    { render: (row) => row.undone_at ? "已撤销" : row.operation === "undo" ? "撤销记录" : `<button class="button small quiet" data-action="undo" data-id="${esc(row.id)}">撤销</button>` },
  ])}</tbody></table>`;
}

async function renderSettings() {
  const data = await api("/api/bootstrap");
  const notifications = data.notifications;
  content.innerHTML = `<section class="settings-sheet">
    <div class="setting-row"><div><h3>身份识别通知</h3><p>训练稳定后可开启；当前人物识别结果仍会保存。</p></div><button class="switch" data-setting="identity_notifications_enabled" role="switch" aria-checked="${notifications.identity_notifications_enabled}" aria-label="身份识别通知"></button></div>
    <div class="setting-row"><div><h3>风险事件通知</h3><p>开启后，仅对达到风险阈值的记录发送通知。</p></div><button class="switch" data-setting="risk_notifications_enabled" role="switch" aria-checked="${notifications.risk_notifications_enabled}" aria-label="风险事件通知"></button></div>
    <div class="setting-row"><div><h3>运行故障通知</h3><p>下载、分析或企业微信持续失败时通知；为防止静默丢失，始终开启。</p></div><button class="switch" role="switch" aria-checked="true" aria-label="运行故障通知" disabled></button></div>
    <div class="setting-row"><div><h3>退出当前设备</h3><p>撤销当前 12 小时服务端会话。</p></div><button class="button quiet" data-action="logout">退出</button></div>
    <div class="setting-row"><div><h3>撤销所有登录</h3><p>所有已登录设备需要重新输入密码。</p></div><button class="button danger" data-action="revoke-all">全部退出</button></div>
  </section>`;
}

async function loadRoute() {
  const route = location.hash.replace(/^#/, "") || "events";
  state.route = routeCopy[route] ? route : "events";
  setHeader(state.route);
  showLoading();
  try {
    if (state.route === "events") await renderEvents();
    else if (state.route === "people") await renderPeople();
    else if (state.route === "operations") await renderOperations();
    else await renderSettings();
  } catch (error) {
    if (!$("#login").hidden) return;
    showError(error);
  }
}

function relationshipSelect(selected = "other") {
  return `<select id="modal-relationship">${Object.entries(relationshipLabels).map(([value, label]) => `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`).join("")}</select>`;
}

function optionalNameField(value = "") {
  return `<div class="field"><label for="modal-name">人物名称（可选）</label><input id="modal-name" maxlength="128" value="${esc(value)}" autocomplete="off" aria-describedby="modal-name-hint"><p class="field-hint" id="modal-name-hint"></p></div>`;
}

function updateAutomaticNameHint() {
  const relationship = $("#modal-relationship");
  const hint = $("#modal-name-hint");
  if (!relationship || !hint) return;
  const update = () => {
    const label = relationshipLabels[relationship.value] || "人物";
    hint.textContent = `可以留空；系统会按顺序自动命名为“${label} 1”“${label} 2”等。`;
  };
  relationship.addEventListener("change", update);
  update();
}

function openModal(title, body, submitLabel, handler) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = body;
  $("#modal-submit").textContent = submitLabel;
  $("#modal-error").textContent = "";
  state.modalHandler = handler;
  modal.showModal();
  updateAutomaticNameHint();
  setTimeout(() => modal.querySelector("input, select")?.focus(), 0);
}

async function mutate(path, method, body, message) {
  await api(path, { method, body: body ? JSON.stringify(body) : undefined });
  modal.close();
  toast(message);
  await loadRoute();
}

function labelCluster(clusterId) {
  openModal("确认这个人物", `${optionalNameField()}<div class="field"><label for="modal-relationship">与您家的关系</label>${relationshipSelect("other")}</div>`, "保存并开始学习", async () => {
    await mutate(`/api/clusters/${clusterId}/label`, "POST", { display_name: $("#modal-name").value, relationship: $("#modal-relationship").value, idempotency_key: idempotency() }, "人物已确认，后续清晰样本会继续积累");
  });
}

function renamePerson(personId) {
  const person = state.people.find((item) => item.id === personId) || state.selectedEvent?.tracks?.find((track) => track.person?.id === personId)?.person;
  openModal("修改人物信息", `${optionalNameField(person?.display_name || "")}<div class="field"><label for="modal-relationship">与您家的关系</label>${relationshipSelect(person?.relationship || "other")}</div>`, "保存修改", async () => {
    await mutate(`/api/people/${personId}/rename`, "POST", { display_name: $("#modal-name").value, relationship: $("#modal-relationship").value, idempotency_key: idempotency() }, "人物信息已更新");
  });
}

function mergePerson(sourceId) {
  const targets = state.people.filter((item) => item.id !== sourceId);
  openModal("合并人物", `<p>源人物的录像与代表样本会并入目标人物；同一录像中同时出现过的两人禁止合并。</p><div class="field"><label for="modal-target">目标人物</label><select id="modal-target">${targets.map((item) => `<option value="${esc(item.id)}">${esc(item.display_name)}</option>`).join("")}</select></div>`, "确认合并", async () => {
    await mutate("/api/people/merge", "POST", { source_person_id: sourceId, target_person_id: $("#modal-target").value, idempotency_key: idempotency() }, "人物已合并，可在运行记录中撤销");
  });
}

function mergeCluster(sourceId) {
  const peopleOptions = state.people.map((item) => `<option value="person:${esc(item.id)}">${esc(item.display_name)} · ${esc(relationshipLabels[item.relationship] || item.relationship)}</option>`).join("");
  const clusterOptions = state.clusters.filter((item) => item.id !== sourceId).map((item) => `<option value="cluster:${esc(item.id)}">待确认人物 ${esc(item.id.slice(-6).toUpperCase())} · ${item.event_count} 次</option>`).join("");
  const groups = `${peopleOptions ? `<optgroup label="已确认人物（优先）">${peopleOptions}</optgroup>` : ""}${clusterOptions ? `<optgroup label="待确认人物">${clusterOptions}</optgroup>` : ""}`;
  openModal("合并人物", `<p>已核对的人物优先显示；仅在您确认是同一个人时合并，同框冲突会被系统拒绝。</p><div class="field"><label for="modal-target">合并到</label><select id="modal-target" aria-describedby="modal-target-hint">${groups}</select><p class="field-hint" id="modal-target-hint">并入已确认人物后，后续清晰样本会继续归入该人物。</p></div>`, "确认合并", async () => {
    const [targetType, targetId] = $("#modal-target").value.split(":", 2);
    if (targetType === "person") {
      await mutate(`/api/clusters/${sourceId}/assign-person`, "POST", { target_person_id: targetId, idempotency_key: idempotency() }, "待确认人物已并入已确认人物，可在运行记录中撤销");
      return;
    }
    await mutate("/api/clusters/merge", "POST", { source_cluster_id: sourceId, target_cluster_id: targetId, idempotency_key: idempotency() }, "待确认人物已合并，可在运行记录中撤销");
  });
}

function splitCluster(clusterId) {
  const cluster = state.clusters.find((item) => item.id === clusterId);
  const checks = cluster.tracks.map((track) => `<label class="check-item"><input type="checkbox" name="split-track" value="${esc(track.id)}">${track.face_url ? `<img src="${esc(track.face_url)}" alt="样本">` : ""}<span>样本质量 ${Math.round(track.quality_score * 100)}%</span></label>`).join("");
  openModal("拆分错分样本", `<p>勾选不属于当前人物的样本，把它们移到一个新的未知人物簇。</p><div class="check-list">${checks}</div>`, "拆分所选样本", async () => {
    const trackIds = [...document.querySelectorAll('input[name="split-track"]:checked')].map((node) => node.value);
    if (!trackIds.length) throw new Error("请至少选择一个需要拆分的样本");
    await mutate(`/api/clusters/${clusterId}/split`, "POST", { track_ids: trackIds, idempotency_key: idempotency() }, "所选样本已拆分，可在运行记录中撤销");
  });
}

function confirmAction(title, copy, label, handler) {
  openModal(title, `<p>${esc(copy)}</p>`, label, handler);
}

async function toggleSetting(button) {
  const key = button.dataset.setting;
  const bootstrap = await api("/api/bootstrap");
  const values = bootstrap.notifications;
  values[key] = button.getAttribute("aria-checked") !== "true";
  await api("/api/settings/notifications", { method: "PUT", body: JSON.stringify({ identity_notifications_enabled: values.identity_notifications_enabled, risk_notifications_enabled: values.risk_notifications_enabled }) });
  button.setAttribute("aria-checked", String(values[key]));
  toast(values[key] ? "通知已开启" : "通知已关闭");
}

document.addEventListener("click", async (event) => {
  const close = event.target.closest("[data-close-modal]");
  if (close) { modal.close(); return; }
  const switchButton = event.target.closest("[data-setting]");
  if (switchButton) { try { await toggleSetting(switchButton); } catch (error) { toast(error.message); } return; }
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  const id = target.dataset.id;
  try {
    if (action === "reload") await loadRoute();
    else if (action === "select-event") { state.selectedEvent = state.events.find((item) => item.id === id); state.selectedTrack = 0; await renderEvents(); }
    else if (action === "select-track") { state.selectedTrack = Number(target.dataset.index); await renderEvents(); }
    else if (action === "select-cluster-track") {
      const cluster = state.clusters.find((item) => item.id === id);
      const card = target.closest(".cluster-card");
      if (!cluster || !card || !cluster.tracks.some((track) => track.id === target.dataset.trackId)) return;
      state.selectedClusterTracks[id] = target.dataset.trackId;
      const template = document.createElement("template");
      template.innerHTML = clusterCard(cluster).trim();
      const replacement = template.content.firstElementChild;
      card.replaceWith(replacement);
      const selectedButton = [...replacement.querySelectorAll('[data-action="select-cluster-track"]')].find((button) => button.dataset.trackId === target.dataset.trackId);
      selectedButton?.focus({ preventScroll: true });
    }
    else if (action === "label-cluster") labelCluster(id);
    else if (action === "rename-person") renamePerson(id);
    else if (action === "merge-person") mergePerson(id);
    else if (action === "merge-cluster") mergeCluster(id);
    else if (action === "split-cluster") splitCluster(id);
    else if (action === "false-positive") confirmAction("标记为误检", "该人物簇将不再参与学习。此操作会保留记录并可撤销。", "确认误检", () => mutate(`/api/clusters/${id}/false-positive`, "POST", { idempotency_key: idempotency() }, "已标记为误检"));
    else if (action === "undo") confirmAction("撤销人工操作", "系统会先检查后续数据是否仍允许安全撤销。", "确认撤销", () => mutate(`/api/operations/${id}/undo`, "POST", { idempotency_key: idempotency() }, "操作已撤销"));
    else if (action === "retry-ingest") await mutate(`/api/ingest/${id}/retry`, "POST", null, "已立即安排重新分析");
    else if (action === "logout") await mutate("/api/session/logout", "POST", null, "已退出");
    else if (action === "revoke-all") confirmAction("撤销所有登录", "包括当前设备在内的所有登录会话都会立即失效。", "全部退出", async () => { await api("/api/session/revoke-all", { method: "POST" }); modal.close(); showLogin(); });
  } catch (error) { toast(error.message); }
});

$("#modal-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.modalHandler) return;
  const submit = $("#modal-submit");
  submit.disabled = true;
  $("#modal-error").textContent = "";
  try { await state.modalHandler(); }
  catch (error) { $("#modal-error").textContent = error.message; }
  finally { submit.disabled = false; }
});

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const errorNode = $("#login-error");
  const button = event.currentTarget.querySelector("button");
  errorNode.textContent = "";
  button.disabled = true;
  try {
    const result = await api("/api/session/login", { method: "POST", body: JSON.stringify({ password: $("#password").value }) });
    state.csrf = result.csrf_token;
    $("#password").value = "";
    showShell();
    await loadRoute();
  } catch (error) { errorNode.textContent = error.message; }
  finally { button.disabled = false; }
});

$("#refresh").addEventListener("click", loadRoute);
window.addEventListener("hashchange", loadRoute);

async function init() {
  try {
    const session = await api("/api/session");
    if (!session.authenticated) { showLogin(); return; }
    state.csrf = session.csrf_token;
    showShell();
    if (!location.hash) location.hash = "events";
    await loadRoute();
  } catch { showLogin(); }
}

init();
