"use strict";

// ── tiny helpers ────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));

async function api(path, opts = {}) {
  const init = { credentials: "same-origin", headers: {} };
  if (opts.body !== undefined) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.headers["X-CCGram"] = "1";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (res.status === 401 && path !== "/api/login") { showLogin(); }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// ── markdown (escape first, then a small safe subset) ────────────────────────
function inline(s) {
  return s
    .replace(/`([^`\n]+)`/g, (_, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,!?:;]|$)/g, "$1<i>$2</i>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|\s)(https?:\/\/[^\s<]+)/g,
      '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
}

function renderBlock(text) {
  const lines = esc(text).split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const row = (l, tag) => "<tr>" + l.trim().replace(/^\||\|$/g, "").split("|")
        .map((c) => `<${tag}>${inline(c.trim())}</${tag}>`).join("") + "</tr>";
      let html = "<table>" + row(line, "th");
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) html += row(lines[i++], "td");
      out.push(html + "</table>");
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, ""))}</li>`);
        i++;
      }
      out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4})\s|^\s*([-*•]|\d+[.)])\s+|^\s*\|/.test(lines[i])) {
      para.push(inline(lines[i]));
      i++;
    }
    if (para.length) out.push(`<p>${para.join("<br>")}</p>`);
    else { out.push(`<p>${inline(line)}</p>`); i++; }
  }
  return out.join("");
}

function markdown(text) {
  const parts = String(text || "").split(/```[^\n]*\n?/);
  return parts.map((p, idx) => (idx % 2
    ? `<pre><code>${esc(p.replace(/\n$/, ""))}</code></pre>`
    : renderBlock(p))).join("");
}

// ── state ───────────────────────────────────────────────────────────────────
const state = {
  sessions: [],
  current: null,          // thread_id
  unread: new Set(),
  filter: "",
  paneTimer: null,
  es: null,
};

const STATE_LABEL = { idle: "ждёт тебя", active: "работает", done: "завершена", dead: "спит", unknown: "" };

function ago(ts) {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "сейчас";
  if (s < 3600) return `${Math.floor(s / 60)} мин`;
  if (s < 86400) return `${Math.floor(s / 3600)} ч`;
  return `${Math.floor(s / 86400)} д`;
}

// ── sessions list ───────────────────────────────────────────────────────────
function renderSessions() {
  const ul = $("session-list");
  const f = state.filter.toLowerCase();
  ul.innerHTML = "";
  for (const s of state.sessions) {
    if (f && !s.name.toLowerCase().includes(f)) continue;
    const li = document.createElement("li");
    li.className = [
      s.thread_id === state.current ? "active" : "",
      state.unread.has(s.thread_id) ? "unread" : "",
    ].join(" ");
    li.innerHTML = `<span class="dot ${esc(s.state)}" title="${esc(STATE_LABEL[s.state] || s.state)}"></span>`
      + `<span class="name">${esc(s.name)}${s.web_only ? '<span class="badge" title="Топик в Telegram удалён">веб</span>' : ""}</span>`
      + `<span class="when">${ago(s.last_activity)}</span>`;
    li.onclick = () => openSession(s.thread_id);
    ul.appendChild(li);
  }
  renderHeader();
}

function currentSession() {
  return state.sessions.find((s) => s.thread_id === state.current);
}

function renderHeader() {
  const s = currentSession();
  if (!s) return;
  $("chat-name").textContent = s.name;
  const bits = [s.web_only ? "только веб" : "", STATE_LABEL[s.state] || "", s.provider, s.cwd].filter(Boolean);
  $("chat-meta").textContent = bits.join(" · ");
  $("close-btn").hidden = !s.web_only;
}

// ── messages ────────────────────────────────────────────────────────────────
function messageNode(m) {
  const ct = m.content_type || "text";
  if (ct === "tool_use" || ct === "tool_result" || ct === "thinking" || ct === "local_command") {
    const d = document.createElement("details");
    d.className = "tool";
    const text = String(m.text || "");
    const first = text.split("\n")[0].replace(/\*\*|`/g, "").slice(0, 160);
    const icon = ct === "thinking" ? "💭" : ct === "tool_result" ? "↳" : "🔧";
    const label = ct === "thinking" ? "размышления" : (m.tool_name ? `${m.tool_name}: ${first}` : first);
    d.innerHTML = `<summary>${icon} ${esc(label)}</summary><pre>${esc(text)}</pre>`;
    return d;
  }
  const div = document.createElement("div");
  div.className = `msg ${m.role === "user" ? "user" : "assistant"}`;
  if (m.role === "user") div.textContent = m.text;
  else div.innerHTML = markdown(m.text);
  return div;
}

function nearBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 120;
}

function appendMessage(m) {
  const box = $("messages");
  const stick = nearBottom(box);
  box.querySelector(".empty")?.remove();
  box.querySelector(".typing")?.remove();
  box.appendChild(messageNode(m));
  if (stick) box.scrollTop = box.scrollHeight;
}

async function openSession(threadId) {
  state.current = threadId;
  state.unread.delete(threadId);
  location.hash = `#${threadId}`;
  $("app").classList.remove("drawer");
  $("input").disabled = false;
  $("send-btn").disabled = false;
  $("pane-btn").hidden = false;
  renderSessions();
  const box = $("messages");
  box.innerHTML = '<div class="empty">Загружаю…</div>';
  try {
    const data = await api(`/api/sessions/${threadId}/messages?limit=300`);
    if (state.current !== threadId) return;
    box.innerHTML = "";
    if (!data.messages.length) box.innerHTML = '<div class="empty">Пока пусто</div>';
    for (const m of data.messages) box.appendChild(messageNode(m));
    box.scrollTop = box.scrollHeight;
  } catch (e) {
    box.innerHTML = `<div class="empty">Не загрузилось: ${esc(e.message)}</div>`;
  }
  if (!$("pane").hidden) refreshPane();
}

// ── composer ────────────────────────────────────────────────────────────────
function autosize() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.4) + "px";
}

async function send() {
  const t = $("input");
  const text = t.value.trim();
  if (!text || state.current == null) return;
  const tid = state.current;
  $("send-btn").disabled = true;
  try {
    const s = currentSession();
    if (s && s.state === "dead") showTyping("бужу сессию…");
    await api(`/api/sessions/${tid}/send`, { body: { text } });
    t.value = "";
    autosize();
    showTyping("отправлено…");
  } catch (e) {
    showTyping(`❌ ${e.message}`);
  } finally {
    $("send-btn").disabled = false;
    t.focus();
  }
}

function showTyping(text) {
  const box = $("messages");
  box.querySelector(".typing")?.remove();
  const d = document.createElement("div");
  d.className = "typing";
  d.textContent = text;
  box.appendChild(d);
  box.scrollTop = box.scrollHeight;
}

// ── pane (terminal snapshot + keys, for prompts/menus) ──────────────────────
async function refreshPane() {
  if (state.current == null) return;
  try {
    const data = await api(`/api/sessions/${state.current}/pane`);
    const pre = $("pane-text");
    pre.textContent = data.alive ? data.text.replace(/\s+$/, "") : "(сессия спит)";
    pre.scrollTop = pre.scrollHeight;
  } catch (_) { /* transient */ }
}

function togglePane() {
  const pane = $("pane");
  pane.hidden = !pane.hidden;
  clearInterval(state.paneTimer);
  if (!pane.hidden) {
    refreshPane();
    state.paneTimer = setInterval(refreshPane, 2000);
  }
}

// ── live events ─────────────────────────────────────────────────────────────
function notify(session, m) {
  if (!document.hidden || Notification?.permission !== "granted") return;
  if (m.role !== "assistant" || m.content_type !== "text" || !m.is_complete) return;
  const n = new Notification(session ? session.name : "Кара", {
    body: String(m.text).slice(0, 180), tag: `t${m.thread_id}`, icon: "/static/icon.svg",
  });
  n.onclick = () => { window.focus(); openSession(m.thread_id); n.close(); };
}

function connect() {
  state.es?.close();
  const es = new EventSource("/api/events");
  state.es = es;
  es.onopen = () => $("conn").classList.add("on");
  es.onerror = () => {
    $("conn").classList.remove("on");
    // EventSource retries on its own; a 401 means the cookie is gone.
    api("/api/me").catch(() => {});
  };
  es.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    if (e.type === "sessions") {
      state.sessions = e.sessions;
      renderSessions();
    } else if (e.type === "message") {
      if (!e.is_complete && e.content_type === "text") return;
      const s = state.sessions.find((x) => x.thread_id === e.thread_id);
      if (e.thread_id === state.current) appendMessage(e);
      else if (e.role === "assistant" && e.content_type === "text") {
        state.unread.add(e.thread_id);
        renderSessions();
      }
      notify(s, e);
    } else if (e.type === "resync" && state.current != null) {
      openSession(state.current);
    }
  };
}

// ── new session ─────────────────────────────────────────────────────────────
async function createSession(ev) {
  ev.preventDefault();
  if (ev.submitter?.value === "cancel") { $("new-dialog").close(); return; }
  const btn = $("new-submit");
  btn.disabled = true;
  $("new-error").textContent = "";
  try {
    const res = await api("/api/sessions", {
      body: { name: $("new-name").value, text: $("new-text").value },
    });
    $("new-dialog").close();
    $("new-name").value = "";
    $("new-text").value = "";
    state.sessions = (await api("/api/sessions")).sessions;
    openSession(res.thread_id);
  } catch (e) {
    $("new-error").textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

// ── auth / boot ─────────────────────────────────────────────────────────────
function showLogin() {
  state.es?.close();
  $("app").hidden = true;
  $("login").hidden = false;
  $("password").focus();
}

async function boot() {
  $("login").hidden = true;
  $("app").hidden = false;
  if ("Notification" in window && Notification.permission === "default") $("notify-btn").hidden = false;
  state.sessions = (await api("/api/sessions")).sessions;
  renderSessions();
  connect();
  const fromHash = parseInt(location.hash.slice(1), 10);
  if (!Number.isNaN(fromHash) && state.sessions.some((s) => s.thread_id === fromHash)) openSession(fromHash);
  else if (window.innerWidth <= 760) $("app").classList.add("drawer");
}

$("login-form").onsubmit = async (ev) => {
  ev.preventDefault();
  $("login-error").textContent = "";
  try {
    await api("/api/login", { body: { password: $("password").value } });
    $("password").value = "";
    boot();
  } catch (e) {
    $("login-error").textContent = e.message === "wrong password" ? "Неверный пароль"
      : e.message === "too many attempts" ? "Слишком много попыток, подожди 15 минут" : e.message;
  }
};
$("composer").onsubmit = (ev) => { ev.preventDefault(); send(); };
$("input").addEventListener("input", autosize);
$("input").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && window.innerWidth > 760) {
    ev.preventDefault();
    send();
  }
});
$("filter").addEventListener("input", (ev) => { state.filter = ev.target.value; renderSessions(); });
$("menu-btn").onclick = () => $("app").classList.toggle("drawer");
$("pane-btn").onclick = togglePane;
$("close-btn").onclick = async () => {
  const s = currentSession();
  if (!s || !confirm(`Завершить «${s.name}»? Процесс будет остановлен, сессия пропадёт из списка.`)) return;
  try {
    await api(`/api/sessions/${s.thread_id}/close`, { body: {} });
    state.current = null;
    $("messages").innerHTML = '<div class="empty">Сессия завершена</div>';
    $("close-btn").hidden = true;
    state.sessions = (await api("/api/sessions")).sessions;
    renderSessions();
  } catch (e) { alert(e.message); }
};
document.querySelectorAll(".keys button").forEach((b) => {
  b.onclick = async () => {
    if (state.current == null) return;
    try { await api(`/api/sessions/${state.current}/key`, { body: { key: b.dataset.key } }); } catch (_) { /* shown via pane */ }
    setTimeout(refreshPane, 300);
  };
});
$("new-btn").onclick = () => { $("new-error").textContent = ""; $("new-dialog").showModal(); $("new-name").focus(); };
$("new-form").addEventListener("submit", createSession);
$("logout-btn").onclick = async () => { await api("/api/logout", { body: {} }).catch(() => {}); showLogin(); };
$("notify-btn").onclick = async () => {
  await Notification.requestPermission();
  $("notify-btn").hidden = Notification.permission !== "default";
};
setInterval(() => { if (state.sessions.length) renderSessions(); }, 60_000);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
api("/api/me").then(boot).catch(() => showLogin());
