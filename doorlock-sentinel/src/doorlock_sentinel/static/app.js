const state = {
  csrf: "",
  route: "events",
  events: [],
  selectedEvent: null,
  selectedTrack: 0,
  selectedClusterTracks: {},
  people: [],
  clusters: [],
  pendingOpen: false,
  lastMergeTarget: "",
  modalHandler: null,
  modalRequest: 0,
  viewedPerson: null,
  viewerKind: "face",
  viewerRequest: 0,
};

const $ = (selector) => document.querySelector(selector);
const content = $("#content");
const modal = $("#modal");
const { dayLabel, formatDate } = globalThis.DoorlockTime;
const relationshipLabels = {
  self: "我",
  family: "家人",
  friend: "朋友",
  neighbor: "邻居",
  food_delivery: "外卖",
  courier: "快递员",
  cleaner: "保洁",
  visitor: "访客",
  stranger: "陌生人",
  other: "其他",
};
const routeCopy = {
  events: ["门口记录", "按北京时间排列，训练期不发送身份通知。"],
  people: ["熟悉的人，清晰的记录", "先核对待确认人物，再按类别查看已经认识的人。"],
  operations: ["运行状态", "查看下载、分析、通知与备份；异常会保留记录。"],
  settings: ["让提醒恰到好处", "身份和风险通知默认关闭；运行故障通知始终开启。"],
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
  if (modal.open) modal.close();
  if ($("#person-viewer").open) $("#person-viewer").close();
  state.csrf = "";
  state.pendingOpen = false;
  state.lastMergeTarget = "";
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
  return `<dl class="summary-strip" aria-label="记录概览">
    <div class="summary-item"><dt>录像记录</dt><dd>${counts.events}</dd></div>
    <div class="summary-item"><dt>已确认人物</dt><dd>${counts.people}</dd></div>
    <div class="summary-item"><dt>待确认人物簇</dt><dd>${counts.review_clusters}</dd></div>
    <div class="summary-item"><dt>分析失败</dt><dd>${counts.failed_analysis}</dd></div>
    <div class="summary-item"><dt>待备份回执</dt><dd>${counts.backup_pending}</dd></div>
  </dl>`;
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
  return `<article class="cluster-card" role="listitem" data-cluster-id="${esc(cluster.id)}">
    <div class="cluster-head"><div><h3>未知人物 ${esc(cluster.id.slice(-6))}</h3><p>${cluster.event_count} 次录像 · ${cluster.distinct_days} 天 · ${cluster.high_quality_count} 张高质量样本</p></div><span class="status pending">待确认</span></div>
    <div class="cluster-review"><div class="cluster-primary">${face(selected?.face_url || selected?.preview_url, largeFaceLabel, "cluster-face-large")}<strong>样本 ${sampleNumber || "—"}</strong><span>${quality}</span></div><div class="cluster-video-wrap">${video}<p>${videoMeta}</p></div></div>
    <div class="face-strip" role="group" aria-label="选择人物簇样本">${samples}</div>
    <div class="card-actions"><button class="button primary small" data-action="label-cluster" data-id="${esc(cluster.id)}">核对并确认</button>${mergeAction}${splitAction}<button class="button quiet small" data-action="false-positive" data-id="${esc(cluster.id)}">标记误检</button></div>
  </article>`;
}

function personCard(person) {
  return `<article class="person-card" role="listitem"><button type="button" class="person-avatar-button" data-action="view-person" data-id="${esc(person.id)}" aria-label="查看${esc(person.display_name)}的大图">${face(person.face_url, person.display_name, "person-avatar")}</button><div class="person-copy"><div class="person-name"><h3>${esc(person.display_name)}</h3><span class="relationship-tag">${esc(relationshipLabels[person.relationship] || "其他")}</span></div><p>出现在 ${person.matched_events} 次录像 · ${person.distinct_days} 天</p></div><div class="person-actions"><button class="button small quiet" data-action="rename-person" data-id="${esc(person.id)}">修改</button>${state.people.length > 1 ? `<button class="button small quiet" data-action="merge-person" data-id="${esc(person.id)}">合并到…</button>` : ""}</div></article>`;
}

function peopleGroups(people) {
  return Object.entries(relationshipLabels).map(([key, label]) => ({
    key, label,
    people: people.filter((person) => (Object.hasOwn(relationshipLabels, person.relationship) ? person.relationship : "other") === key),
  }));
}

function renderPeopleContent() {
  const peopleHtml = state.people.length
    ? `<div class="category-grid">${peopleGroups(state.people).map((group) => `<details class="person-category" data-category="${group.key}"><summary><span class="category-icon" aria-hidden="true">${group.label.slice(0,1)}</span><span class="category-copy"><strong>${group.label}</strong><small>${group.people.length} 人 · 展开查看</small></span></summary>${group.people.length ? `<div class="people-list" role="list">${group.people.map(personCard).join("")}</div>` : '<div class="empty"><strong>这个类别还没有人物</strong><p>核对人物时可选择此类别。</p></div>'}</details>`).join("")}</div>`
    : '<div class="empty"><strong>还没有已确认人物</strong><p>确认待核对的人物簇后，清晰代表样本会在这里持续积累。</p><button class="button quiet" data-action="scroll-to-clusters">查看待确认人物</button></div>';
  const clustersHtml = state.clusters.length
    ? `<details class="pending-disclosure"${state.pendingOpen ? " open" : ""}><summary><span class="pending-disclosure-title">待确认</span><span class="pending-disclosure-count">${state.clusters.length} 组</span><span class="pending-disclosure-hint">展开查看样本、录像和核对操作</span></summary><div class="pending-clusters" role="list">${state.clusters.map(clusterCard).join("")}</div></details>`
    : '<div class="empty"><strong>还没有待确认的人物</strong><p>清晰人脸会在多次出现后进入这里；不清晰画面不会被强行学习。</p><a class="button quiet" href="#operations">查看运行状态</a></div>';
  content.innerHTML = `<section class="clusters-section" id="review-clusters" aria-labelledby="clusters-heading"><div class="section-head"><div><h2 id="clusters-heading">等待您的确认</h2><p>先核对样本和对应录像；不确定时保持未知。</p></div></div>${clustersHtml}</section><section class="people-section" aria-labelledby="people-heading"><div class="section-head"><div><h2 id="people-heading">已确认人物</h2><p>每个类别一个入口，展开查看人物与原有操作。</p></div><span class="section-count">${state.people.length} 人</span></div>${peopleHtml}</section>`;
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
  content.innerHTML = `<section class="operations-status" aria-labelledby="system-heading"><div class="section-head"><div><h2 id="system-heading">系统状态</h2><p>服务异常会在相应记录旁显示处理方式。</p></div></div><dl class="system-grid">
    <div class="system-cell"><dt>识别服务</dt><dd>${system.service.analysis_ready ? "已就绪" : "待处理"}</dd></div>
    <div class="system-cell"><dt>可用空间</dt><dd>${bytes(system.storage.free_bytes)}</dd></div>
    <div class="system-cell"><dt>模型</dt><dd>${esc(system.model.active ? "已锁定" : "未就绪")}</dd></div>
    <div class="system-cell"><dt>待备份回执</dt><dd>${backup.pending || 0}</dd></div>
    <div class="system-cell"><dt>通知待发送</dt><dd>${outbox.pending || 0}</dd></div>
    <div class="system-cell"><dt>通知死信</dt><dd>${outbox.dead || 0}</dd></div>
  </dl></section>
  <section class="operation-section"><div class="section-head"><div><h2>需要处理的失败</h2><p>分析已经自动按 5、20、60 分钟重试；仍失败时在这里手工重试。</p></div></div>
  <div class="table-scroll" role="region" tabindex="0" aria-label="失败分析记录"><table class="ledger-table"><thead><tr><th>北京时间</th><th>文件</th><th>原因</th><th>操作</th></tr></thead><tbody>${tableRows(system.failed_ingests || [], [
    { render: (row) => esc(formatDate(row.updated_at, true)) },
    { class: "long", render: (row) => esc(row.file_name) },
    { class: "long", render: (row) => esc(row.error_code || row.error || "未知原因") },
    { render: (row) => `<button class="button small quiet" data-action="retry-ingest" data-id="${esc(row.id)}">重新分析</button>` },
  ])}</tbody></table></div></section>
  <section class="operation-section"><div class="section-head"><div><h2>下载器回报</h2><p>本系统只接收下载状态，不读取小米或 Home Assistant 凭据。</p></div></div>
  <div class="table-scroll" role="region" tabindex="0" aria-label="下载器回报记录"><table class="ledger-table"><thead><tr><th>下载器记录时间（北京时间）</th><th>状态</th><th>尝试</th><th>错误</th></tr></thead><tbody>${tableRows(system.download_reports || [], [
    { render: (row) => esc(formatDate(row.event_time, true)) },
    { render: (row) => esc(window.DoorlockUiLabels.stateLabel(row.state)) },
    { render: (row) => esc(row.attempts) },
    { class: "long", render: (row) => esc(window.DoorlockUiLabels.errorLabel(row.error_code)) },
  ])}</tbody></table></div></section>
  <section class="operation-section"><div class="section-head"><div><h2>人工操作与撤销</h2><p>命名、合并、拆分和误检决定均保留审计记录。</p></div></div>
  <div class="table-scroll" role="region" tabindex="0" aria-label="人工操作审计记录"><table class="ledger-table"><thead><tr><th>北京时间</th><th>操作</th><th>对象</th><th>状态</th></tr></thead><tbody>${tableRows(operations.items || [], [
    { render: (row) => esc(formatDate(row.created_at, true)) },
    { render: (row) => esc(row.operation_label || "人工操作") },
    { class: "long", render: (row) => esc(row.subject_label || "人工操作记录") },
    { render: (row) => row.undone_at ? "已撤销" : row.operation === "undo" ? "撤销记录" : `<button class="button small quiet" data-action="undo" data-id="${esc(row.id)}">撤销</button>` },
  ])}</tbody></table></div></section>`;
}

async function renderSettings() {
  const data = await api("/api/bootstrap");
  const notifications = data.notifications;
  content.innerHTML = `<div class="settings-page"><section class="settings-group" aria-labelledby="notification-settings-heading"><div class="section-head"><div><h2 id="notification-settings-heading">通知偏好</h2><p>训练期间身份与风险提醒默认关闭，运行故障提醒保持开启。</p></div></div><div class="settings-sheet">
    <div class="setting-row"><div><h3>身份识别通知</h3><p>训练稳定后可开启；当前人物识别结果仍会保存。</p></div><button class="switch" data-setting="identity_notifications_enabled" role="switch" aria-checked="${notifications.identity_notifications_enabled}" aria-label="身份识别通知"></button></div>
    <div class="setting-row"><div><h3>风险事件通知</h3><p>开启后，仅对达到风险阈值的记录发送通知。</p></div><button class="switch" data-setting="risk_notifications_enabled" role="switch" aria-checked="${notifications.risk_notifications_enabled}" aria-label="风险事件通知"></button></div>
    <div class="setting-row"><div><h3>运行故障通知</h3><p>下载、分析或企业微信持续失败时通知；为防止静默丢失，始终开启。</p></div><button class="switch" role="switch" aria-checked="true" aria-label="运行故障通知" disabled></button></div>
  </div></section><section class="settings-group" aria-labelledby="session-settings-heading"><div class="section-head"><div><h2 id="session-settings-heading">登录会话</h2><p>服务端会话最长保留 12 小时，您可以随时撤销。</p></div></div><div class="settings-sheet">
    <div class="setting-row"><div><h3>退出当前设备</h3><p>撤销当前 12 小时服务端会话。</p></div><button class="button quiet" data-action="logout">退出</button></div>
    <div class="setting-row"><div><h3>撤销所有登录</h3><p>所有已登录设备需要重新输入密码。</p></div><button class="button danger" data-action="revoke-all">全部退出</button></div>
  </div></section></div>`;
}

async function loadRoute() {
  const pending = state.route === "people" && $(".pending-disclosure");
  if (pending && $("#login").hidden) state.pendingOpen = pending.open;
  const route = location.hash.replace(/^#/, "") || "events";
  state.route = routeCopy[route] ? route : "events";
  setHeader(state.route);
  content.setAttribute("aria-busy", "true");
  showLoading();
  try {
    if (state.route === "events") await renderEvents();
    else if (state.route === "people") await renderPeople();
    else if (state.route === "operations") await renderOperations();
    else await renderSettings();
  } catch (error) {
    if (!$("#login").hidden) return;
    showError(error);
  } finally {
    content.setAttribute("aria-busy", "false");
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
  state.modalRequest++;
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

function restoreMergeTarget(prefix = "") {
  const select = $("#modal-target");
  const previous = [...select.options].find((option) => prefix + option.value === state.lastMergeTarget);
  if (previous) select.value = previous.value;
}

function mergePerson(sourceId) {
  const targets = state.people.filter((item) => item.id !== sourceId);
  openModal("合并人物", `<p>源人物的录像与代表样本会并入目标人物；同一录像中同时出现过的两人禁止合并。</p><div class="field"><label for="modal-target">目标人物</label><select id="modal-target">${targets.map((item) => `<option value="${esc(item.id)}">${esc(item.display_name)}</option>`).join("")}</select></div>`, "确认合并", async () => {
    const targetId = $("#modal-target").value;
    await mutate("/api/people/merge", "POST", { source_person_id: sourceId, target_person_id: targetId, idempotency_key: idempotency() }, "人物已合并，可在运行记录中撤销");
    if (state.csrf) state.lastMergeTarget = `person:${targetId}`;
  });
  restoreMergeTarget("person:");
}

function mergeCluster(sourceId) {
  const peopleOptions = state.people.map((item) => `<option value="person:${esc(item.id)}">${esc(item.display_name)} · ${esc(relationshipLabels[item.relationship] || item.relationship)}</option>`).join("");
  const clusterOptions = state.clusters.filter((item) => item.id !== sourceId).map((item) => `<option value="cluster:${esc(item.id)}">待确认人物 ${esc(item.id.slice(-6).toUpperCase())} · ${item.event_count} 次</option>`).join("");
  const groups = `${peopleOptions ? `<optgroup label="已确认人物（优先）">${peopleOptions}</optgroup>` : ""}${clusterOptions ? `<optgroup label="待确认人物">${clusterOptions}</optgroup>` : ""}`;
  openModal("合并人物", `<p>已核对的人物优先显示；仅在您确认是同一个人时合并，同框冲突会被系统拒绝。</p><div class="field"><label for="modal-target">合并到</label><select id="modal-target" aria-describedby="modal-target-hint">${groups}</select><p class="field-hint" id="modal-target-hint">并入已确认人物后，后续清晰样本会继续归入该人物。</p></div>`, "确认合并", async () => {
    const [targetType, targetId] = $("#modal-target").value.split(":", 2);
    if (targetType === "person") {
      const request = state.modalRequest;
      const review = await api(`/api/clusters/${encodeURIComponent(sourceId)}/person-conflicts?target_person_id=${encodeURIComponent(targetId)}`);
      if (!modal.open || request !== state.modalRequest || !state.csrf) return;
      if (review.items.length) {
        reviewMergeConflicts(sourceId, targetId, review);
        return;
      }
      await mutate(`/api/clusters/${sourceId}/assign-person`, "POST", { target_person_id: targetId, idempotency_key: idempotency() }, "待确认人物已并入已确认人物，可在运行记录中撤销");
      if (state.csrf) state.lastMergeTarget = `person:${targetId}`;
      return;
    }
    await mutate("/api/clusters/merge", "POST", { source_cluster_id: sourceId, target_cluster_id: targetId, idempotency_key: idempotency() }, "待确认人物已合并，可在运行记录中撤销");
    if (state.csrf) state.lastMergeTarget = `cluster:${targetId}`;
  });
  restoreMergeTarget();
}

function reviewMergeConflicts(sourceId, targetId, review) {
  const person = review.target_person || state.people.find(item => item.id === targetId);
  const sample = (track, label) => `<figure><figcaption>${label} · 代表场景</figcaption>${track.preview_url ? `<img src="${esc(track.preview_url)}" alt="${label}代表场景（不保证是冲突帧）">` : '<p>代表场景不可用，请核对原始录像。</p>'}</figure>`;
  const items = review.items.map((item, index) => `<section class="conflict-item"><h3>冲突 ${index + 1}</h3><div class="conflict-evidence">${sample(item.left, "样本 A")}${sample(item.right, "样本 B")}</div>${item.video_url ? `<video controls preload="metadata" src="${esc(item.video_url)}" aria-label="冲突 ${index + 1} 的原始录像"></video>` : '<p>原始录像不在本地。证据不足时请取消。</p>'}<div class="field"><label for="conflict-reason-${index}">这条冲突的误判原因</label><select id="conflict-reason-${index}"><option value="">请选择原因</option><option value="reflection">同一个人的倒影</option><option value="duplicate_detection">同一个人被重复检测</option></select></div><label class="conflict-confirm"><input type="checkbox" id="conflict-confirm-${index}"><span>我已核对：这是同一个人，不是同框中的两个人</span></label></section>`).join("");
  openModal("核查同框冲突", `<div class="conflict-review"><p>准备并入：${esc(person?.display_name || "所选人物")}。只纠正下列约束，不关闭其他同框保护。</p><p class="muted">以下是代表样本，可能不是触发冲突的同一帧。旧记录未保存精确冲突帧位置，请结合原始录像逐项核对；不确定时取消。</p>${items}<p>纠错与合并会一起保存，可在运行记录中撤销。</p></div>`, "纠错并合并", async () => {
    const corrections = review.items.map((item, index) => {
      const reason = $(`#conflict-reason-${index}`).value;
      if (!reason || !$(`#conflict-confirm-${index}`).checked) throw new Error(`请先选择冲突 ${index + 1} 的原因，并确认已核对是同一个人`);
      return { id: item.id, reason };
    });
    await mutate(`/api/clusters/${sourceId}/assign-person`, "POST", { target_person_id: targetId, conflict_corrections: corrections, review_revision: review.review_revision, idempotency_key: idempotency() }, "误判已纠正并合并，可在运行记录中撤销");
    if (state.csrf) state.lastMergeTarget = `person:${targetId}`;
  });
  document.querySelectorAll('.conflict-evidence img').forEach(img => img.addEventListener('error', () => {
    const message = document.createElement('p');
    message.textContent = '图片无法加载，请核对原始录像；证据不足时取消。';
    img.replaceWith(message);
  }, { once: true }));
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

function loadPersonImage() {
  const person = state.viewedPerson;
  if (!person) return;
  const request = ++state.viewerRequest;
  const scene = state.viewerKind === "scene";
  const url = scene ? person.preview_url : person.face_url;
  const mediaNode = $("#person-viewer-media");
  const status = $("#person-viewer-status");
  const retry = $('[data-action="retry-person-viewer"]');
  mediaNode.replaceChildren();
  mediaNode.setAttribute("aria-busy", "false");
  retry.hidden = true;
  $('[data-action="person-viewer-face"]').setAttribute("aria-pressed", String(!scene));
  $('[data-action="person-viewer-scene"]').setAttribute("aria-pressed", String(scene));
  if (!url) { status.textContent = scene ? "暂无对应场景原图。" : "暂无人脸图片，可切换场景原图查看。"; return; }
  status.textContent = "正在加载图片…";
  mediaNode.setAttribute("aria-busy", "true");
  const img = document.createElement("img");
  img.alt = person.display_name + (scene ? "的带人脸框场景原图" : "的人脸大图");
  img.className = scene ? "viewer-scene" : "viewer-face";
  img.hidden = true;
  img.onload = () => {
    if (request !== state.viewerRequest) return;
    img.hidden = false;
    mediaNode.setAttribute("aria-busy", "false");
    status.textContent = scene ? "场景原图 · 保留完整画面和人脸框" : "人脸大图 · 放大不会增加原图细节";
  };
  img.onerror = () => {
    if (request !== state.viewerRequest) return;
    mediaNode.setAttribute("aria-busy", "false");
    status.textContent = "图片加载失败，请重新加载；登录失效时请刷新页面重新登录。";
    retry.hidden = false;
  };
  mediaNode.append(img);
  img.src = url;
}

function viewPerson(id) {
  const person = state.people.find((item) => item.id === id);
  if (!person) return;
  state.viewedPerson = person;
  state.viewerKind = person.face_url ? "face" : "scene";
  $("#person-viewer-title").textContent = person.display_name + " · 人物大图";
  $("#person-viewer").showModal();
  loadPersonImage();
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
    if (action === "view-person") viewPerson(id);
    else if (action === "close-person-viewer") $("#person-viewer").close();
    else if (action === "person-viewer-face" || action === "person-viewer-scene") {
      state.viewerKind = action === "person-viewer-face" ? "face" : "scene";
      loadPersonImage();
    }
    else if (action === "retry-person-viewer") loadPersonImage();
    else if (action === "reload") await loadRoute();
    else if (action === "scroll-to-clusters") {
      const section = document.querySelector("#review-clusters");
      const disclosure = section?.querySelector("details");
      if (disclosure) disclosure.open = true;
      section?.scrollIntoView();
      disclosure?.querySelector("summary")?.focus({ preventScroll: true });
    }
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

$("#person-viewer").addEventListener("close", () => {
  state.viewerRequest++;
  state.viewedPerson = null;
  $("#person-viewer-media").replaceChildren();
});

$("#modal-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.modalHandler) return;
  const submit = $("#modal-submit");
  const label = submit.textContent;
  const handler = state.modalHandler;
  submit.disabled = true;
  submit.textContent = "正在保存…";
  $("#modal-error").textContent = "";
  try { await handler(); }
  catch (error) { if (state.modalHandler === handler && modal.open) $("#modal-error").textContent = error.message; }
  finally { submit.disabled = false; if (state.modalHandler === handler) submit.textContent = label; }
});

modal.addEventListener("close", () => { state.modalRequest++; state.modalHandler = null; });

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const errorNode = $("#login-error");
  const button = event.currentTarget.querySelector("button");
  const label = button.textContent;
  errorNode.textContent = "";
  button.disabled = true;
  button.textContent = "正在核对…";
  try {
    const result = await api("/api/session/login", { method: "POST", body: JSON.stringify({ password: $("#password").value }) });
    state.csrf = result.csrf_token;
    $("#password").value = "";
    showShell();
    await loadRoute();
  } catch (error) { errorNode.textContent = error.message; }
  finally { button.disabled = false; button.textContent = label; }
});

$("#refresh").addEventListener("click", loadRoute);
window.addEventListener("hashchange", async () => {
  await loadRoute();
  if (!$("#shell").hidden) $("#page-title").focus({ preventScroll: true });
});

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
