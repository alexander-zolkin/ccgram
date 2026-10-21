"use strict";

// ═══ helpers ════════════════════════════════════════════════════════════════
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* private mode */ } },
};

async function api(path, opts = {}) {
  const init = { credentials: "same-origin", headers: {} };
  if (opts.form) {
    init.method = "POST";
    init.headers["X-CCGram"] = "1";
    init.body = opts.form;
  } else if (opts.body !== undefined) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.headers["X-CCGram"] = "1";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (res.status === 401 && path !== "/api/login") showLogin();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const shortPath = (p) => {
  const s = String(p || "");
  const home = s.replace(/^\/home\/[^/]+/, "~");
  const parts = home.split("/");
  return parts.length > 3 ? "…/" + parts.slice(-2).join("/") : home;
};
const hhmm = (ts) => {
  if (!ts) return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
};
function ago(ts) {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "сейчас";
  if (s < 3600) return `${Math.floor(s / 60)} мин`;
  if (s < 86400) return `${Math.floor(s / 3600)} ч`;
  const d = new Date(ts * 1000);
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}
async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    if (btn) { const o = btn.textContent; btn.textContent = "Скопировано"; setTimeout(() => { btn.textContent = o; }, 1200); }
  } catch (_) { /* clipboard blocked */ }
}

// ═══ markdown ═══════════════════════════════════════════════════════════════
marked.setOptions({ gfm: true, breaks: true });
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") { node.setAttribute("target", "_blank"); node.setAttribute("rel", "noopener noreferrer"); }
});
function renderMarkdown(text) {
  const box = el("div");
  box.innerHTML = DOMPurify.sanitize(marked.parse(String(text || "")));
  box.querySelectorAll("pre > code").forEach((code) => {
    const pre = code.parentElement;
    const lang = (code.className.match(/language-([\w+#-]+)/) || [])[1] || "";
    const wrap = el("div", "codebox");
    const head = el("div", "codebox-head", `<span>${esc(lang || "код")}</span>`);
    const btn = el("button", "copy-btn", "Копировать");
    btn.type = "button";
    btn.onclick = () => copyText(code.textContent, btn);
    head.appendChild(btn);
    pre.replaceWith(wrap);
    wrap.append(head, pre);
    try { if (lang && hljs.getLanguage(lang)) hljs.highlightElement(code); else if (code.textContent.length < 20000) hljs.highlightElement(code); } catch (_) { /* best effort */ }
  });
  return box;
}

// ═══ tool presentation ══════════════════════════════════════════════════════
const ICONS = {
  term: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 17l6-5-6-5M12 19h8"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
  globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
  bot: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M9 14h.01M15 14h.01"/></svg>',
  list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/></svg>',
  ask: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.7M12 17h.01"/></svg>',
  gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
};

function toolMeta(t) {
  const i = t.input || {};
  const n = t.name || "";
  const code = (s) => `<code>${esc(String(s).split("\n")[0].slice(0, 160))}</code>`;
  switch (n) {
    case "Bash": return { icon: "term", title: i.description ? esc(i.description) : code(i.command || ""), label: "Bash" };
    case "BashOutput": return { icon: "term", title: "Вывод фоновой команды", label: "Bash" };
    case "Read": return { icon: "file", title: `Прочитала ${code(shortPath(i.file_path))}`, label: "Read" };
    case "Edit": case "MultiEdit": return { icon: "edit", title: `Изменила ${code(shortPath(i.file_path))}`, label: "Edit" };
    case "Write": return { icon: "edit", title: `Записала ${code(shortPath(i.file_path))}`, label: "Write" };
    case "NotebookEdit": return { icon: "edit", title: `Изменила ${code(shortPath(i.notebook_path))}`, label: "Notebook" };
    case "Grep": return { icon: "search", title: `Искала ${code(i.pattern || "")}${i.path ? " в " + code(shortPath(i.path)) : ""}`, label: "Grep" };
    case "Glob": return { icon: "search", title: `Искала файлы ${code(i.pattern || "")}`, label: "Glob" };
    case "WebSearch": return { icon: "globe", title: `Искала в сети: ${esc(i.query || "")}`, label: "Поиск" };
    case "WebFetch": {
      let host = i.url || "";
      try { host = new URL(i.url).host; } catch (_) { /* keep raw */ }
      return { icon: "globe", title: `Открыла ${esc(host)}`, label: "Web" };
    }
    case "Agent": case "Task": return { icon: "bot", title: `Субагент: ${esc(i.description || i.subagent_type || "")}`, label: "Агент" };
    case "TodoWrite": return { icon: "list", title: "Обновила план", label: "План" };
    case "AskUserQuestion": return { icon: "ask", title: "Задала вопрос", label: "Вопрос" };
    case "ToolSearch": return { icon: "gear", title: "Подключила инструменты", label: "Инструменты" };
    default: {
      if (n.startsWith("mcp__")) {
        const [, server = "", tool = ""] = n.split("__");
        const label = server.replace(/^claude_ai_/, "").replace(/[_-]+/g, " ").trim() || "MCP";
        return { icon: "gear", title: `${esc(label)}: ${esc(tool.replace(/_/g, " "))}`, label };
      }
      return { icon: "gear", title: esc(n.replace(/_/g, " ")), label: n };
    }
  }
}

function diffHtml(patch) {
  const rows = [];
  let add = 0;
  let del = 0;
  for (const h of patch) {
    rows.push(`<div class="hunk">@@ −${h.old_start ?? "?"} +${h.new_start ?? "?"} @@</div>`);
    for (const line of h.lines || []) {
      const c = line[0];
      if (c === "+") add++;
      if (c === "-") del++;
      rows.push(`<div class="${c === "+" ? "add" : c === "-" ? "del" : ""}">${esc(line) || " "}</div>`);
    }
  }
  return { html: `<div class="diff">${rows.join("")}</div>`, add, del };
}

function toolDetail(t) {
  const i = t.input || {};
  const r = t.result;
  const parts = [];
  if (t.name === "Bash") {
    parts.push(`<pre class="term cmdbox"><span class="cmd">$</span> ${esc(i.command || "")}</pre>`);
    if (r) {
      const out = r.stdout ?? r.text ?? "";
      if (String(out).trim()) parts.push(`<div class="lbl">Вывод</div><pre class="term">${esc(out)}</pre>`);
      if (r.stderr && String(r.stderr).trim()) parts.push(`<div class="lbl">stderr</div><pre class="term err">${esc(r.stderr)}</pre>`);
      if (!String(out).trim() && !(r.stderr || "").trim()) parts.push('<div class="lbl">Без вывода</div>');
    }
    return parts.join("");
  }
  if ((t.name === "Edit" || t.name === "MultiEdit" || t.name === "Write") && r && r.patch) {
    parts.push(diffHtml(r.patch).html);
  } else if (t.name === "Write" && i.content) {
    parts.push(`<pre>${esc(i.content)}</pre>`);
  } else if (t.name === "TodoWrite" && Array.isArray(i.todos)) {
    const mark = { completed: "☑", in_progress: "◐", pending: "☐" };
    parts.push(`<ul class="todo">${i.todos.map((x) => `<li class="${x.status === "completed" ? "done" : ""}"><span>${mark[x.status] || "☐"}</span><span>${esc(x.content || x.activeForm || "")}</span></li>`).join("")}</ul>`);
  } else if (t.name === "AskUserQuestion" && Array.isArray(i.questions)) {
    for (const q of i.questions) {
      parts.push(`<div><b>${esc(q.question || "")}</b></div>`);
      if (Array.isArray(q.options)) parts.push(`<ul class="todo">${q.options.map((o) => `<li>• ${esc(o.label || o)}${o.description ? ` <span class="muted">— ${esc(o.description)}</span>` : ""}</li>`).join("")}</ul>`);
    }
  } else if (t.name === "Agent" || t.name === "Task") {
    if (i.prompt) parts.push(`<div class="lbl">Задание</div><pre>${esc(i.prompt)}</pre>`);
  } else {
    const shown = Object.fromEntries(Object.entries(i).filter(([k]) => k !== "description"));
    if (Object.keys(shown).length) parts.push(`<pre>${esc(JSON.stringify(shown, null, 2))}</pre>`);
  }
  if (r && !(t.name === "Edit" && r.patch) && !(t.name === "Write" && r.patch)) {
    const out = r.stdout !== undefined ? r.stdout + (r.stderr ? "\n" + r.stderr : "") : r.text;
    if (out && String(out).trim()) parts.push(`<div class="lbl">${r.is_error ? "Ошибка" : "Результат"}</div><pre>${esc(out)}</pre>`);
  }
  return parts.join("") || '<div class="muted">Нет подробностей</div>';
}

function stepStat(t) {
  if (!t.result) return '<span class="spin"></span>';
  if (t.result.is_error) return '<span class="bad">✕</span>';
  if (t.result.patch) {
    const { add, del } = diffHtml(t.result.patch);
    return `<span class="ok">+${add}</span><span class="bad">−${del}</span>`;
  }
  return '<span class="ok">✓</span>';
}

function stepNode(t) {
  const m = toolMeta(t);
  const node = el("div", "step");
  node.dataset.id = t.id || "";
  node.innerHTML = `<div class="step-row"><span class="step-ico">${ICONS[m.icon] || ICONS.gear}</span><span class="step-title">${m.title}</span><span class="step-stat">${stepStat(t)}</span></div><div class="step-detail"></div>`;
  node.querySelector(".step-row").onclick = () => {
    node.classList.toggle("open");
    if (node.classList.contains("open")) node.querySelector(".step-detail").innerHTML = toolDetail(t);
  };
  node._tool = t;
  return node;
}

function refreshStepsHead(block) {
  const tools = [...block.querySelectorAll(".step")].map((n) => n._tool);
  const thinking = block._thinking || 0;
  const counts = {};
  for (const t of tools) { const l = toolMeta(t).label; counts[l] = (counts[l] || 0) + 1; }
  const names = Object.entries(counts).map(([k, v]) => (v > 1 ? `${k} ×${v}` : k)).join(", ");
  const running = tools.some((t) => !t.result);
  const errors = tools.filter((t) => t.result && t.result.is_error).length;
  const word = (n) => (n % 10 === 1 && n % 100 !== 11 ? "шаг" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "шага" : "шагов");
  const what = tools.length ? `${tools.length} ${word(tools.length)}` : "размышляла";
  const extra = [names, thinking && tools.length ? "размышления" : ""].filter(Boolean).join(" · ");
  block.querySelector(".steps-head").innerHTML = `<span class="chev">▶</span><span class="sum">${esc(what)}${extra ? ` · <span class="muted">${esc(extra)}</span>` : ""}</span>${running ? '<span class="spin"></span>' : errors ? `<span class="bad" style="color:var(--err)">${errors} ✕</span>` : ""}`;
}

function stepsBlock() {
  const block = el("div", "steps");
  block.innerHTML = '<div class="steps-head"></div><div class="steps-body"></div>';
  block.querySelector(".steps-head").onclick = () => block.classList.toggle("open");
  block._thinking = 0;
  return block;
}

// ═══ thread rendering ═══════════════════════════════════════════════════════
const state = {
  sessions: [],
  current: null,
  unread: new Set(),
  filter: "",
  es: null,
  start: 0,
  end: 0,
  structured: true,
  seen: new Set(),
  steps: new Map(),        // tool id → step node
  loadingOlder: false,
  fetchTimer: null,
  paneTimer: null,
  attachments: [],
};

function userNode(it) {
  const n = el("div", "u-msg");
  n.textContent = it.text || "";
  if (it.images) n.appendChild(el("span", "att", `🖼 изображений: ${it.images}`));
  return n;
}

function assistantNode(it) {
  const n = el("div", "a-msg");
  n.appendChild(el("div", "avatar", "К"));
  const body = el("div", "a-body");
  body.appendChild(renderMarkdown(it.text));
  const actions = el("div", "msg-actions");
  const copy = el("button", "copy-btn", "Копировать");
  copy.type = "button";
  copy.onclick = () => copyText(it.text, copy);
  actions.append(copy, el("span", "msg-time", esc(hhmm(it.ts))));
  body.appendChild(actions);
  n.appendChild(body);
  return n;
}

function itemKey(it) {
  if (it.kind === "tool") return "t:" + it.id;
  if (it.kind === "tool_result") return null;
  return it.uuid ? `${it.kind}:${it.uuid}` : null;
}

// Build DOM blocks for a list of items. Returns a fragment; trailing tool
// runs are grouped into one steps block. ``container`` is where the blocks
// will land, so a run can continue the steps block already at its end.
function buildBlocks(items, continueFrom) {
  const frag = document.createDocumentFragment();
  let steps = continueFrom && continueFrom.classList.contains("steps") ? continueFrom : null;
  for (const it of items) {
    if (it.kind === "tool_result") {
      const node = state.steps.get(it.id);
      if (node) {
        node._tool.result = it;
        node.querySelector(".step-stat").innerHTML = stepStat(node._tool);
        if (node.classList.contains("open")) node.querySelector(".step-detail").innerHTML = toolDetail(node._tool);
        refreshStepsHead(node.closest(".steps"));
      }
      continue;
    }
    const key = itemKey(it);
    if (key) {
      if (state.seen.has(key)) continue;
      state.seen.add(key);
    }
    if (it.kind === "tool" || it.kind === "thinking") {
      if (!steps) { steps = stepsBlock(); frag.appendChild(steps); }
      if (it.kind === "tool") {
        const node = stepNode(it);
        state.steps.set(it.id, node);
        steps.querySelector(".steps-body").appendChild(node);
      } else {
        steps._thinking++;
      }
      refreshStepsHead(steps);
      continue;
    }
    steps = null;
    frag.appendChild(it.kind === "user" ? userNode(it) : assistantNode(it));
  }
  return frag;
}

function nearBottom() {
  const s = $("scroller");
  return s.scrollHeight - s.scrollTop - s.clientHeight < 160;
}
function toBottom() { const s = $("scroller"); s.scrollTop = s.scrollHeight; }

function appendItems(items) {
  const thread = $("thread");
  const stick = nearBottom();
  const last = thread.lastElementChild;
  thread.appendChild(buildBlocks(items, last));
  autoOpenLive();
  if (stick) toBottom(); else if (items.length) $("jump-btn").hidden = false;
}

function prependItems(items) {
  const thread = $("thread");
  const s = $("scroller");
  const before = s.scrollHeight;
  const anchor = thread.querySelector(".loader-row")?.nextSibling || thread.firstChild;
  const frag = buildBlocks(items, null);
  thread.insertBefore(frag, anchor);
  s.scrollTop += s.scrollHeight - before;
}

// While the session works, keep the newest steps block open so progress shows.
function autoOpenLive() {
  const s = currentSession();
  const blocks = $("thread").querySelectorAll(".steps");
  const lastBlock = blocks[blocks.length - 1];
  if (lastBlock && s && s.state === "active" && lastBlock === $("thread").lastElementChild) lastBlock.classList.add("open");
}

function resetThread(html) {
  state.seen = new Set();
  state.steps = new Map();
  $("thread").innerHTML = html || "";
}

async function loadOlder() {
  if (state.loadingOlder || !state.structured || state.start <= 0 || state.current == null) return;
  state.loadingOlder = true;
  const tid = state.current;
  const row = $("thread").querySelector(".loader-row");
  if (row) row.textContent = "Загружаю…";
  try {
    const data = await api(`/api/sessions/${tid}/entries?limit=150&before=${state.start}`);
    if (tid !== state.current) return;
    state.start = data.start;
    prependItems(data.items);
  } catch (_) { /* retry on next scroll */ }
  finally {
    state.loadingOlder = false;
    const r = $("thread").querySelector(".loader-row");
    if (r) { if (state.start <= 0) r.remove(); else r.textContent = "↑ раньше"; }
  }
}

async function openSession(tid) {
  state.current = tid;
  state.unread.delete(tid);
  if (location.hash !== `#${tid}`) history.replaceState(null, "", `#${tid}`);
  $("app").classList.remove("drawer");
  $("input").disabled = false;
  $("send-btn").disabled = false;
  $("pane-btn").hidden = false;
  $("prompt-card").hidden = true;
  renderSessions();
  resetThread('<div class="loader-row">Загружаю…</div>');
  try {
    const data = await api(`/api/sessions/${tid}/entries?limit=200`);
    if (state.current !== tid) return;
    state.start = data.start;
    state.end = data.end;
    state.structured = data.structured;
    resetThread(state.structured && data.start > 0 ? '<div class="loader-row">↑ раньше</div>' : "");
    if (!data.items.length) $("thread").innerHTML = '<div class="welcome"><p class="muted">Пока пусто — напиши первое сообщение.</p></div>';
    $("thread").appendChild(buildBlocks(data.items, null));
    autoOpenLive();
    if (location.search.includes("expand")) {
      // Debug aid: open every steps block and the first few steps.
      document.querySelectorAll(".steps").forEach((b) => b.classList.add("open"));
      const want = new URLSearchParams(location.search).get("expand");
      const steps = [...document.querySelectorAll(".step")].filter((n) => !want || n._tool.name === want).slice(-6);
      steps.forEach((n) => n.querySelector(".step-row").click());
      (steps[0] || document.querySelector(".steps:last-of-type"))?.scrollIntoView();
      return;
    }
    toBottom();
  } catch (e) {
    resetThread(`<div class="loader-row">Не загрузилось: ${esc(e.message)}</div>`);
  }
  pollPane();
  if (window.innerWidth > 820) $("input").focus();
}

function scheduleFetch() {
  clearTimeout(state.fetchTimer);
  state.fetchTimer = setTimeout(fetchNew, 250);
}

async function fetchNew() {
  const tid = state.current;
  if (tid == null) return;
  if (!state.structured) { openSession(tid); return; }
  try {
    const data = await api(`/api/sessions/${tid}/entries?after=${state.end}`);
    if (tid !== state.current) return;
    state.end = data.end;
    $("thread").querySelector(".welcome")?.remove();
    appendItems(data.items);
  } catch (_) { /* next event retries */ }
}

// ═══ sessions sidebar ═══════════════════════════════════════════════════════
const STATE_LABEL = { idle: "ждёт тебя", active: "работает", done: "завершена", dead: "спит", unknown: "" };

function currentSession() { return state.sessions.find((s) => s.thread_id === state.current); }

function groupOf(ts) {
  if (!ts) return "Раньше";
  const d = new Date(ts * 1000);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = (today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000;
  if (diff <= 0) return "Сегодня";
  if (diff === 1) return "Вчера";
  if (diff < 7) return "На этой неделе";
  if (diff < 31) return "В этом месяце";
  return "Раньше";
}

function renderSessions() {
  const nav = $("session-list");
  const f = state.filter.trim().toLowerCase();
  nav.innerHTML = "";
  let group = null;
  for (const s of state.sessions) {
    if (f && !s.name.toLowerCase().includes(f)) continue;
    const g = groupOf(s.last_activity);
    if (g !== group) { nav.appendChild(el("div", "group-label", esc(g))); group = g; }
    const item = el("div", `s-item${s.thread_id === state.current ? " active" : ""}${state.unread.has(s.thread_id) ? " unread" : ""}`);
    item.innerHTML = `<span class="dot ${esc(s.state)}" title="${esc(STATE_LABEL[s.state] || "")}"></span><span class="s-name">${esc(s.name)}${s.web_only ? '<span class="badge" title="Топик в Telegram удалён">веб</span>' : ""}</span><span class="s-when">${ago(s.last_activity)}</span>`;
    item.onclick = () => openSession(s.thread_id);
    nav.appendChild(item);
  }
  renderHeader();
}

function tgLink(s) {
  if (!s || s.web_only || !s.chat_id) return null;
  return `https://t.me/c/${String(s.chat_id).replace(/^-100/, "")}/${s.thread_id}`;
}

function renderHeader() {
  const s = currentSession();
  if (!s) return;
  const nameEl = $("chat-name");
  if (nameEl.getAttribute("contenteditable") !== "true") nameEl.textContent = s.name;
  const bits = [STATE_LABEL[s.state] || "", s.web_only ? "только веб" : "", shortPath(s.cwd)].filter(Boolean);
  $("chat-meta").innerHTML = `<span class="dot ${esc(s.state)}" style="display:inline-block;margin-right:6px"></span>${esc(bits.join(" · "))}`;
  $("close-btn").hidden = !s.web_only;
  const link = tgLink(s);
  $("tg-link").hidden = !link;
  if (link) $("tg-link").href = link;
  const working = s.state === "active";
  $("working").hidden = !working;
  document.title = `${working ? "⏳ " : ""}${s.name} — Кара`;
}

// ═══ rename ═════════════════════════════════════════════════════════════════
function startRename() {
  const s = currentSession();
  if (!s) return;
  const n = $("chat-name");
  n.setAttribute("contenteditable", "true");
  n.focus();
  document.getSelection().selectAllChildren(n);
  const finish = async (save) => {
    n.removeAttribute("contenteditable");
    n.onkeydown = null;
    n.onblur = null;
    const name = n.textContent.trim();
    if (!save || !name || name === s.name) { n.textContent = s.name; return; }
    try {
      await api(`/api/sessions/${s.thread_id}/rename`, { body: { name } });
      s.name = name;
      renderSessions();
    } catch (e) { n.textContent = s.name; alert(e.message); }
  };
  n.onkeydown = (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
    if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  };
  n.onblur = () => finish(true);
}

// ═══ pane / prompts ═════════════════════════════════════════════════════════
const KEYS = [["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"], ["Up", "↑"], ["Down", "↓"], ["Enter", "⏎ Enter"], ["Escape", "Esc"], ["Tab", "Tab"]];
document.querySelectorAll("[data-keys]").forEach((box) => {
  for (const [key, label] of KEYS) {
    const b = el("button", "", esc(label));
    b.type = "button";
    b.onclick = () => sendKey(key);
    box.appendChild(b);
  }
});

async function sendKey(key) {
  if (state.current == null) return;
  try { await api(`/api/sessions/${state.current}/key`, { body: { key } }); } catch (e) { flash(e.message); }
  setTimeout(pollPane, 350);
}

async function pollPane() {
  clearTimeout(state.paneTimer);
  const tid = state.current;
  const s = currentSession();
  if (tid == null || !s) return;
  if (!document.hidden && s.state !== "dead") {
    try {
      const data = await api(`/api/sessions/${tid}/pane`);
      if (tid === state.current) {
        if (!$("pane").hidden) {
          const pre = $("pane-text");
          pre.textContent = data.alive ? data.text.replace(/\s+$/, "") : "(сессия спит)";
          pre.scrollTop = pre.scrollHeight;
        }
        const inter = data.interactive;
        $("prompt-card").hidden = !inter;
        if (inter) {
          $("prompt-title").textContent = inter.name === "AskUserQuestion" ? "Кара задаёт вопрос" : inter.name === "PermissionPrompt" ? "Кара просит разрешения" : "Кара ждёт выбора";
          $("prompt-text").textContent = inter.content;
        }
      }
    } catch (_) { /* transient */ }
  }
  const fast = !$("pane").hidden || (s && s.state === "active") || !$("prompt-card").hidden;
  state.paneTimer = setTimeout(pollPane, fast ? 2000 : 6000);
}

function flash(text) {
  const w = $("working");
  w.hidden = false;
  $("working-text").textContent = text;
  setTimeout(() => { $("working-text").textContent = "Кара работает…"; renderHeader(); }, 3000);
}

// ═══ composer ═══════════════════════════════════════════════════════════════
function autosize() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.4) + "px";
}

function renderAttachments() {
  const box = $("attachments");
  box.innerHTML = "";
  state.attachments.forEach((a, idx) => {
    const chip = el("span", "att-chip", `<span>${a.uploading ? "⏳" : a.image ? "🖼" : "📄"} ${esc(a.name)}</span>`);
    const x = el("button", "", "✕");
    x.type = "button";
    x.onclick = () => { state.attachments.splice(idx, 1); renderAttachments(); };
    chip.appendChild(x);
    box.appendChild(chip);
  });
}

async function uploadFiles(files) {
  if (state.current == null) return;
  for (const file of files) {
    const a = { name: file.name || "file", uploading: true };
    state.attachments.push(a);
    renderAttachments();
    const form = new FormData();
    form.append("file", file, file.name || "paste.png");
    try {
      const res = await api(`/api/sessions/${state.current}/upload`, { form });
      Object.assign(a, res, { uploading: false });
    } catch (e) {
      state.attachments.splice(state.attachments.indexOf(a), 1);
      flash(`Файл не загрузился: ${e.message}`);
    }
    renderAttachments();
  }
}

async function send() {
  const t = $("input");
  const text = t.value.trim();
  if (state.current == null) return;
  if (state.attachments.some((a) => a.uploading)) { flash("Файлы ещё загружаются…"); return; }
  const files = state.attachments.map((a) => (a.image ? `I've uploaded an image to ${a.path} — please take a look.` : `I've uploaded ${a.name} to ${a.path}`));
  const message = [...files, text].filter(Boolean).join("\n\n");
  if (!message) return;
  $("send-btn").disabled = true;
  try {
    const s = currentSession();
    if (s && s.state === "dead") flash("Бужу сессию…");
    await api(`/api/sessions/${state.current}/send`, { body: { text: message } });
    t.value = "";
    state.attachments = [];
    renderAttachments();
    autosize();
    if (s) { s.state = "active"; renderHeader(); }
  } catch (e) {
    flash(`❌ ${e.message}`);
  } finally {
    $("send-btn").disabled = false;
    t.focus();
  }
}

// ═══ live events ════════════════════════════════════════════════════════════
function notify(s, e) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (!document.hidden && e.thread_id === state.current) return;
  if (e.role !== "assistant" || e.content_type !== "text" || !e.is_complete) return;
  const n = new Notification(s ? s.name : "Кара", { body: String(e.text).slice(0, 180), tag: `t${e.thread_id}`, icon: "/static/icon.svg" });
  n.onclick = () => { window.focus(); openSession(e.thread_id); n.close(); };
}

function connect() {
  state.es?.close();
  const es = new EventSource("/api/events");
  state.es = es;
  es.onopen = () => { $("conn").classList.add("on"); $("conn").title = "На связи"; };
  es.onerror = () => { $("conn").classList.remove("on"); $("conn").title = "Нет связи"; api("/api/me").catch(() => {}); };
  es.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    if (e.type === "sessions") {
      const prev = currentSession()?.state;
      state.sessions = e.sessions;
      renderSessions();
      const now = currentSession()?.state;
      if (prev !== now) { autoOpenLive(); if (now === "idle") scheduleFetch(); }
    } else if (e.type === "message") {
      if (e.thread_id === state.current) scheduleFetch();
      else if (e.role === "assistant" && e.content_type === "text" && e.is_complete) { state.unread.add(e.thread_id); renderSessions(); }
      notify(state.sessions.find((x) => x.thread_id === e.thread_id), e);
    } else if (e.type === "resync" && state.current != null) {
      openSession(state.current);
    }
  };
}

// ═══ new session ════════════════════════════════════════════════════════════
async function createSession(ev) {
  ev.preventDefault();
  if (ev.submitter?.value === "cancel") { $("new-dialog").close(); return; }
  const btn = $("new-submit");
  btn.disabled = true;
  btn.textContent = "Запускаю…";
  $("new-error").textContent = "";
  try {
    const res = await api("/api/sessions", { body: { name: $("new-name").value, text: $("new-text").value, web_only: $("new-webonly").checked } });
    $("new-dialog").close();
    $("new-name").value = "";
    $("new-text").value = "";
    state.sessions = (await api("/api/sessions")).sessions;
    openSession(res.thread_id);
  } catch (e) {
    $("new-error").textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Создать";
  }
}

// ═══ theme ══════════════════════════════════════════════════════════════════
function applyTheme(mode) {
  if (mode === "light" || mode === "dark") document.documentElement.dataset.theme = mode;
  else delete document.documentElement.dataset.theme;
  const dark = mode === "dark" || (mode !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  $("hljs-dark").disabled = !dark;
  $("hljs-light").disabled = dark;
  $("theme-btn").title = { light: "Тема: светлая", dark: "Тема: тёмная" }[mode] || "Тема: как в системе";
}
let theme = store.get("kara-theme") || "system";
applyTheme(theme);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(theme));

// ═══ auth / boot ════════════════════════════════════════════════════════════
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
  else if (window.innerWidth <= 820) $("app").classList.add("drawer");
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
      : e.message === "too many attempts" ? "Слишком много попыток — подожди 15 минут" : e.message;
  }
};
$("composer").onsubmit = (ev) => { ev.preventDefault(); send(); };
$("input").addEventListener("input", autosize);
$("input").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && window.innerWidth > 820) { ev.preventDefault(); send(); }
});
$("input").addEventListener("paste", (ev) => {
  const files = [...(ev.clipboardData?.files || [])];
  if (files.length) { ev.preventDefault(); uploadFiles(files); }
});
$("file-input").onchange = (ev) => { uploadFiles([...ev.target.files]); ev.target.value = ""; };
const composer = $("composer");
["dragenter", "dragover"].forEach((t) => composer.addEventListener(t, (ev) => { ev.preventDefault(); composer.classList.add("drag"); }));
["dragleave", "drop"].forEach((t) => composer.addEventListener(t, () => composer.classList.remove("drag")));
composer.addEventListener("drop", (ev) => { ev.preventDefault(); uploadFiles([...(ev.dataTransfer?.files || [])]); });

$("filter").addEventListener("input", (ev) => { state.filter = ev.target.value; renderSessions(); });
$("menu-btn").onclick = () => $("app").classList.add("drawer");
$("collapse-btn").onclick = () => $("app").classList.remove("drawer");
$("scrim").onclick = () => $("app").classList.remove("drawer");
$("pane-btn").onclick = () => { $("pane").hidden = !$("pane").hidden; pollPane(); };
$("stop-btn").onclick = () => sendKey("Escape");
$("chat-name").onclick = startRename;
$("jump-btn").onclick = () => { toBottom(); $("jump-btn").hidden = true; };
$("scroller").addEventListener("scroll", () => {
  const s = $("scroller");
  document.querySelector(".topbar").classList.toggle("scrolled", s.scrollTop > 4);
  if (nearBottom()) $("jump-btn").hidden = true;
  if (s.scrollTop < 300) loadOlder();
});
$("close-btn").onclick = async () => {
  const s = currentSession();
  if (!s || !confirm(`Завершить «${s.name}»? Процесс остановится, сессия пропадёт из списка.`)) return;
  try {
    await api(`/api/sessions/${s.thread_id}/close`, { body: {} });
    state.current = null;
    resetThread('<div class="welcome"><p class="muted">Сессия завершена.</p></div>');
    $("close-btn").hidden = true;
    state.sessions = (await api("/api/sessions")).sessions;
    renderSessions();
  } catch (e) { alert(e.message); }
};
$("new-btn").onclick = () => { $("new-error").textContent = ""; $("new-dialog").showModal(); $("new-name").focus(); };
$("new-form").addEventListener("submit", createSession);
$("new-webonly").onchange = (ev) => {
  $("new-hint").textContent = ev.target.checked
    ? "Запущу Claude только здесь: в Telegram ничего не появится."
    : "Создам топик в группе «Кара» и запущу Claude с настройками по умолчанию.";
  store.set("kara-new-webonly", ev.target.checked ? "1" : "");
};
$("new-webonly").checked = store.get("kara-new-webonly") === "1";
$("logout-btn").onclick = async () => { await api("/api/logout", { body: {} }).catch(() => {}); showLogin(); };
$("notify-btn").onclick = async () => { await Notification.requestPermission(); $("notify-btn").hidden = Notification.permission !== "default"; };
$("theme-btn").onclick = () => {
  theme = { system: "light", light: "dark", dark: "system" }[theme] || "system";
  store.set("kara-theme", theme);
  applyTheme(theme);
};
document.addEventListener("visibilitychange", () => { if (!document.hidden) { pollPane(); if (state.current != null) scheduleFetch(); } });
setInterval(() => { if (state.sessions.length) renderSessions(); }, 60_000);

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
api("/api/me").then(boot).catch(() => showLogin());
