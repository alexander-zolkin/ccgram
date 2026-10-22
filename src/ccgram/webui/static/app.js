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
const isMobile = () => window.innerWidth <= 820;

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

function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 3500);
}

const shortPath = (p) => {
  const home = String(p || "").replace(/^\/home\/[^/]+/, "~");
  const parts = home.split("/");
  return parts.length > 3 ? "…/" + parts.slice(-2).join("/") : home;
};
const hhmm = (ts) => {
  if (!ts) return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
};
async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    if (btn) { const o = btn.textContent; btn.textContent = "Скопировано"; setTimeout(() => { btn.textContent = o; }, 1200); }
  } catch (_) { toast("Не удалось скопировать"); }
}

const SVG = {
  more: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>',
  pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 17v5M8 3h8l-1 6 3 3v2H6v-2l3-3z"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  tg: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M21.9 4.6l-3.1 14.7c-.2 1-.8 1.3-1.7.8l-4.7-3.5-2.3 2.2c-.3.3-.5.5-1 .5l.3-4.8 8.7-7.9c.4-.3-.1-.5-.6-.2L6.8 13.2 2.2 11.7c-1-.3-1-1 .2-1.5l18-6.9c.8-.3 1.6.2 1.5 1.3z"/></svg>',
  unlink: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1M3 3l18 18"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></svg>',
  term: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/></svg>',
  sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4"/></svg>',
  out: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>',
};

// ═══ popover menu ═══════════════════════════════════════════════════════════
// items: {label, icon, sub, danger, check, href, action} | "sep" | {heading}
function openMenu(items, at, owner) {
  const menu = $("menu");
  menu.innerHTML = "";
  for (const it of items) {
    if (it === "sep") { menu.appendChild(el("div", "sep")); continue; }
    if (it.heading) { menu.appendChild(el("div", "label", esc(it.heading))); continue; }
    const node = el(it.href ? "a" : "button", it.danger ? "danger" : "");
    if (it.href) { node.href = it.href; node.target = "_blank"; node.rel = "noopener"; }
    node.innerHTML = `<span class="ico">${it.icon || ""}</span><span>${esc(it.label)}${it.sub ? `<span class="sub">${esc(it.sub)}</span>` : ""}</span>${it.check ? '<span class="check">✓</span>' : ""}`;
    node.onclick = () => { closeMenu(); if (it.action) it.action(); };
    menu.appendChild(node);
  }
  menu.hidden = false;
  const { width, height } = menu.getBoundingClientRect();
  let x = at.x;
  let y = at.y;
  if (x + width > window.innerWidth - 8) x = window.innerWidth - width - 8;
  if (y + height > window.innerHeight - 8) y = Math.max(8, at.top !== undefined ? at.top - height - 4 : window.innerHeight - height - 8);
  menu.style.left = `${Math.max(8, x)}px`;
  menu.style.top = `${y}px`;
  closeMenu.owner?.classList.remove("menu-open");
  closeMenu.owner = owner || null;
  owner?.classList.add("menu-open");
}
function closeMenu() {
  $("menu").hidden = true;
  closeMenu.owner?.classList.remove("menu-open");
  closeMenu.owner = null;
}
function menuBelow(button) {
  const r = button.getBoundingClientRect();
  return { x: r.left, y: r.bottom + 4, top: r.top };
}
document.addEventListener("mousedown", (ev) => { if (!$("menu").hidden && !$("menu").contains(ev.target)) closeMenu(); });
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeMenu(); });
window.addEventListener("resize", closeMenu);

function confirmDialog(title, text, okLabel) {
  return new Promise((resolve) => {
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    $("confirm-ok").textContent = okLabel;
    const dlg = $("confirm");
    dlg.returnValue = "";
    dlg.onclose = () => resolve(dlg.returnValue === "ok");
    dlg.showModal();
  });
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
    try { if (code.textContent.length < 20000) hljs.highlightElement(code); } catch (_) { /* best effort */ }
  });
  return box;
}

// ═══ tool presentation ══════════════════════════════════════════════════════
const ICONS = {
  term: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 17l6-5-6-5M12 19h8"/></svg>',
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>',
  edit: SVG.edit,
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>',
  globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
  bot: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M9 14h.01M15 14h.01"/></svg>',
  list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/></svg>',
  ask: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.7M12 17h.01"/></svg>',
  gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"/></svg>',
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
  node.innerHTML = `<div class="step-row"><span class="step-ico">${ICONS[m.icon] || ICONS.gear}</span><span class="step-title">${m.title}</span><span class="step-stat">${stepStat(t)}</span></div><div class="step-detail"></div>`;
  node.querySelector(".step-row").onclick = () => {
    node.classList.toggle("open");
    if (node.classList.contains("open")) node.querySelector(".step-detail").innerHTML = toolDetail(t);
  };
  node._tool = t;
  return node;
}

const plural = (n, one, few, many) => (n % 10 === 1 && n % 100 !== 11 ? one : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? few : many);

function refreshStepsHead(block) {
  const tools = [...block.querySelectorAll(".step")].map((n) => n._tool);
  const counts = {};
  for (const t of tools) { const l = toolMeta(t).label; counts[l] = (counts[l] || 0) + 1; }
  const names = Object.entries(counts).map(([k, v]) => (v > 1 ? `${k} ×${v}` : k)).join(", ");
  const running = tools.some((t) => !t.result);
  const errors = tools.filter((t) => t.result && t.result.is_error).length;
  const last = tools[tools.length - 1];
  const what = !tools.length ? "Размышляла"
    : running && last ? toolMeta(last).title.replace(/<[^>]+>/g, "")
      : `${tools.length} ${plural(tools.length, "действие", "действия", "действий")}`;
  block.querySelector(".steps-head").innerHTML = `<span class="sum">${esc(what)}${names && !running ? ` · <span class="muted">${esc(names)}</span>` : ""}</span>${running ? '<span class="spin"></span>' : errors ? `<span style="color:var(--err)">${errors} ✕</span>` : ""}${SVG.chev}`;
}

function stepsBlock() {
  const block = el("div", "steps");
  block.innerHTML = '<div class="steps-head"></div><div class="steps-body"></div>';
  block.querySelector(".steps-head").onclick = () => block.classList.toggle("open");
  return block;
}

// ═══ state ══════════════════════════════════════════════════════════════════
const state = {
  sessions: [],
  current: null,          // session id, or null on the home screen
  unread: new Set(),
  filter: "",
  es: null,
  start: 0,
  end: 0,
  structured: true,
  seen: new Set(),
  steps: new Map(),
  loadingOlder: false,
  fetchTimer: null,
  paneTimer: null,
  attachments: [],
  pending: new Map(),     // session id → text shown until the transcript has it
  providers: null,          // [{id, name, models, default}]
  provider: store.get("kara-provider") || "claude",
  model: store.get("kara-model") || "",
  telegram: store.get("kara-telegram") !== "0",
};

// ═══ thread rendering ═══════════════════════════════════════════════════════
function userNode(it) {
  const n = el("div", "u-msg");
  const files = [];
  const text = String(it.text || "").replace(/^I've uploaded (?:an image to (\S+) — please take a look\.|(.+?) to (\S+))\s*$/gm, (_, img, name, path) => {
    files.push(img ? img.split("/").pop().replace(/^\d{8}-\d{6}-/, "") : name);
    return "";
  }).trim();
  if (files.length || it.images) {
    const att = el("div", "att");
    for (const f of files) att.appendChild(el("span", "", `📄 ${esc(f)}`));
    if (it.images) att.appendChild(el("span", "", `🖼 ${it.images}`));
    n.appendChild(att);
  }
  if (text) { const md = renderMarkdown(text); md.className = "u-md"; n.appendChild(md); }
  return n;
}

function assistantNode(it) {
  const n = el("div", "a-msg");
  n.appendChild(renderMarkdown(it.text));
  const actions = el("div", "msg-actions");
  const copy = el("button", "copy-btn", "Копировать");
  copy.type = "button";
  copy.onclick = () => copyText(it.text, copy);
  actions.append(copy, el("span", "msg-time", esc(hhmm(it.ts))));
  n.appendChild(actions);
  return n;
}

function itemKey(it) {
  if (it.kind === "tool") return "t:" + it.id;
  if (it.kind === "tool_result") return null;
  return it.uuid ? `${it.kind}:${it.uuid}` : null;
}

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
      }
      refreshStepsHead(steps);
      continue;
    }
    steps = null;
    if (it.kind === "user") clearPending();
    frag.appendChild(it.kind === "user" ? userNode(it) : assistantNode(it));
  }
  return frag;
}

function clearPending() {
  $("thread").querySelector(".u-msg.pending")?.remove();
  if (state.current != null) state.pending.delete(state.current);
}

function markLast() {
  const thread = $("thread");
  thread.querySelectorAll(".a-msg.last").forEach((n) => n.classList.remove("last"));
  const msgs = thread.querySelectorAll(".a-msg");
  msgs[msgs.length - 1]?.classList.add("last");
}

function nearBottom() {
  const s = $("scroller");
  return s.scrollHeight - s.scrollTop - s.clientHeight < 160;
}
function toBottom() { const s = $("scroller"); s.scrollTop = s.scrollHeight; }

function appendItems(items) {
  const thread = $("thread");
  const stick = nearBottom();
  thread.appendChild(buildBlocks(items, thread.lastElementChild));
  markLast();
  autoOpenLive();
  if (stick) toBottom(); else if (items.length) $("jump-btn").hidden = false;
}

function prependItems(items) {
  const thread = $("thread");
  const s = $("scroller");
  const before = s.scrollHeight;
  const anchor = thread.querySelector(".loader-row")?.nextSibling || thread.firstChild;
  thread.insertBefore(buildBlocks(items, null), anchor);
  s.scrollTop += s.scrollHeight - before;
}

function autoOpenLive() {
  const s = currentSession();
  const thread = $("thread");
  const last = thread.lastElementChild;
  if (last && last.classList.contains("steps") && s && s.state === "active") last.classList.add("open");
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
  try {
    const data = await api(`/api/sessions/${tid}/entries?limit=150&before=${state.start}`);
    if (tid !== state.current) return;
    state.start = data.start;
    prependItems(data.items);
  } catch (_) { /* retry on next scroll */ }
  finally {
    state.loadingOlder = false;
    const r = $("thread").querySelector(".loader-row");
    if (r && state.start <= 0) r.remove();
  }
}

// ═══ routing: home screen vs a session ══════════════════════════════════════
function route() {
  const id = parseInt(location.hash.slice(1), 10);
  if (!Number.isNaN(id)) openSession(id);
  else showHome();
}

function greeting() {
  const h = new Date().getHours();
  const part = h < 5 ? "Доброй ночи" : h < 12 ? "Доброе утро" : h < 18 ? "Добрый день" : "Добрый вечер";
  return `${part}, Александр`;
}

function showHome() {
  state.current = null;
  $("gone-card").hidden = true;
  clearTimeout(state.paneTimer);
  $("app").classList.add("home-mode");
  $("app").classList.remove("drawer");
  $("home").hidden = false;
  resetThread("");
  $("greet-text").textContent = greeting();
  $("title-btn").hidden = true;
  $("pane-btn").hidden = true;
  $("pane").hidden = true;
  $("chat-state").innerHTML = "";
  $("working").hidden = true;
  $("prompt-card").hidden = true;
  $("tg-toggle").hidden = false;
  $("model-btn").hidden = false;
  $("input").placeholder = "Как я могу помочь?";
  document.title = "Кара";
  renderToggle();
  renderSessions();
  loadModels();
  if (!isMobile()) $("input").focus();
}

async function openSession(tid) {
  $("app").classList.remove("home-mode", "drawer");
  $("home").hidden = true;
  $("tg-toggle").hidden = true;
  $("model-btn").hidden = true;
  $("input").placeholder = "Ответить Каре…";
  state.current = tid;
  state.unread.delete(tid);
  if (location.hash !== `#${tid}`) history.replaceState(null, "", `#${tid}`);
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
    resetThread(state.structured && data.start > 0 ? '<div class="loader-row">Загружаю ранние сообщения при прокрутке…</div>' : "");
    $("thread").appendChild(buildBlocks(data.items, null));
    const pending = state.pending.get(tid);
    if (pending) { const p = userNode({ text: pending }); p.classList.add("pending"); $("thread").appendChild(p); }
    markLast();
    autoOpenLive();
    if (location.search.includes("expand")) {
      const want = new URLSearchParams(location.search).get("expand");
      document.querySelectorAll(".steps").forEach((b) => b.classList.add("open"));
      const steps = [...document.querySelectorAll(".step")].filter((n) => !want || n._tool.name === want).slice(-6);
      steps.forEach((n) => n.querySelector(".step-row").click());
      (steps[0] || document.querySelector(".steps"))?.scrollIntoView();
    } else {
      toBottom();
    }
  } catch (e) {
    resetThread(`<div class="loader-row">Не загрузилось: ${esc(e.message)}</div>`);
  }
  pollPane();
  if (!isMobile()) $("input").focus();
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
    appendItems(data.items);
  } catch (_) { /* next event retries */ }
}

// ═══ sessions sidebar ═══════════════════════════════════════════════════════
const STATE_LABEL = { idle: "ждёт тебя", active: "работает", done: "завершена", dead: "спит", unknown: "" };

function currentSession() { return state.sessions.find((s) => s.thread_id === state.current); }
function sessionById(id) { return state.sessions.find((s) => s.thread_id === id); }

function tgLink(s) {
  if (!s || s.web_only || !s.chat_id) return null;
  return `https://t.me/c/${String(s.chat_id).replace(/^-100/, "")}/${s.thread_id}`;
}

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
  const list = state.sessions.filter((s) => !f || s.name.toLowerCase().includes(f));
  const pinned = list.filter((s) => s.pinned);
  if (pinned.length) {
    nav.appendChild(el("div", "group-label", "Закреплённые"));
    for (const s of pinned) nav.appendChild(sessionItem(s));
  }
  let group = null;
  for (const s of list.filter((s) => !s.pinned)) {
    const g = groupOf(s.last_activity);
    if (g !== group) { nav.appendChild(el("div", "group-label", esc(g))); group = g; }
    nav.appendChild(sessionItem(s));
  }
  if (!list.length) nav.appendChild(el("div", "group-label", f ? "Ничего не нашлось" : "Чатов пока нет"));
  renderHeader();
}

function sessionItem(s) {
  const item = el("a", `s-item${s.thread_id === state.current ? " active" : ""}${state.unread.has(s.thread_id) ? " unread" : ""}`);
  item.href = `#${s.thread_id}`;
  item.innerHTML = `<span class="dot ${esc(s.state)}" title="${esc(STATE_LABEL[s.state] || "")}"></span><span class="s-name">${esc(s.name)}${s.web_only ? '<span class="badge" title="Без Telegram">веб</span>' : ""}</span>`;
  const more = el("button", "s-more", SVG.more);
  more.type = "button";
  more.title = "Действия";
  more.onclick = (ev) => { ev.preventDefault(); ev.stopPropagation(); openMenu(sessionMenu(s, item), menuBelow(more), item); };
  item.appendChild(more);
  item.oncontextmenu = (ev) => { ev.preventDefault(); openMenu(sessionMenu(s, item), { x: ev.clientX, y: ev.clientY }, item); };
  return item;
}

function sessionMenu(s, item) {
  const items = [
    { label: s.pinned ? "Открепить" : "Закрепить", icon: SVG.pin, action: () => pinSession(s) },
    { label: "Переименовать", icon: SVG.edit, action: () => renameSession(s, item) },
  ];
  const link = tgLink(s);
  if (link) items.push({ label: "Открыть в Telegram", icon: SVG.tg, href: link });
  if (!s.web_only) items.push({ label: "Убрать из Telegram", sub: "Топик удалится, чат останется здесь", icon: SVG.unlink, action: () => detachSession(s) });
  items.push("sep", { label: "Удалить", icon: SVG.trash, danger: true, action: () => deleteSession(s) });
  return items;
}

async function pinSession(s) {
  try {
    await api(`/api/sessions/${s.thread_id}/pin`, { body: { pinned: !s.pinned } });
    s.pinned = !s.pinned;
    renderSessions();
  } catch (e) { toast(e.message); }
}

function renameSession(s, item) {
  // Inline edit in the sidebar row (or the title when the row is hidden).
  const host = item && item.isConnected && !$("app").classList.contains("collapsed") ? item.querySelector(".s-name") : $("chat-name");
  const input = el("input", "rename-input");
  input.value = s.name;
  const original = host.innerHTML;
  host.innerHTML = "";
  host.appendChild(input);
  input.focus();
  input.select();
  input.onclick = (ev) => { ev.preventDefault(); ev.stopPropagation(); };
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const name = input.value.trim();
    host.innerHTML = original;
    if (!save || !name || name === s.name) return;
    try {
      await api(`/api/sessions/${s.thread_id}/rename`, { body: { name } });
      s.name = name;
      renderSessions();
    } catch (e) { toast(e.message); }
  };
  input.onkeydown = (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); finish(true); }
    if (ev.key === "Escape") { ev.preventDefault(); finish(false); }
  };
  input.onblur = () => finish(true);
}

async function detachSession(s) {
  const ok = await confirmDialog(
    "Убрать из Telegram?",
    `Топик «${s.name}» удалится в Telegram вместе с его историей там. Сессия продолжит работать здесь, в вебе.`,
    "Убрать",
  );
  if (!ok) return;
  try {
    await api(`/api/sessions/${s.thread_id}/detach`, { body: {} });
    toast("Топик удалён, чат остался в вебе");
    await refreshSessions();
  } catch (e) { toast(e.message); }
}

async function deleteSession(s) {
  const ok = await confirmDialog(
    "Удалить чат?",
    s.web_only
      ? `«${s.name}» будет остановлен и пропадёт из списка. Это нельзя отменить.`
      : `«${s.name}» будет остановлен, его топик в Telegram удалится. Это нельзя отменить.`,
    "Удалить",
  );
  if (!ok) return;
  try {
    await api(`/api/sessions/${s.thread_id}/delete`, { body: {} });
    toast("Чат удалён");
    if (state.current === s.thread_id) location.hash = "#new";
    await refreshSessions();
  } catch (e) { toast(e.message); }
}

async function refreshSessions() {
  state.sessions = (await api("/api/sessions")).sessions;
  renderSessions();
}

function renderHeader() {
  const s = currentSession();
  if (!s) return;
  $("title-btn").hidden = false;
  if (!$("chat-name").querySelector("input")) $("chat-name").textContent = s.name;
  $("chat-badge").hidden = !s.web_only;
  const label = STATE_LABEL[s.state] || "";
  $("chat-state").innerHTML = label ? `<span class="dot ${esc(s.state)}"></span><span>${esc(label)}</span>` : "";
  const working = s.state === "active";
  $("working").hidden = !working;
  $("gone-card").hidden = !s.topic_deleted;
  $("disclaimer").textContent = s.web_only ? "Этот чат только в вебе — в Telegram не дублируется." : "Этот чат дублируется в Telegram-топик.";
  document.title = `${working ? "⏳ " : ""}${s.name} — Кара`;
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
  try { await api(`/api/sessions/${state.current}/key`, { body: { key } }); } catch (e) { toast(e.message); }
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
          $("prompt-title").textContent = inter.name === "AskUserQuestion" ? "Кара задаёт вопрос" : /Permission/i.test(inter.name) ? "Кара просит разрешения" : "Кара ждёт выбора";
          $("prompt-text").textContent = inter.content;
        }
      }
    } catch (_) { /* transient */ }
  }
  const fast = !$("pane").hidden || (s && s.state === "active") || !$("prompt-card").hidden;
  state.paneTimer = setTimeout(pollPane, fast ? 2000 : 6000);
}

// ═══ composer ═══════════════════════════════════════════════════════════════
function autosize() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.4) + "px";
  $("send-btn").disabled = !t.value.trim() && !state.attachments.length;
}

function renderAttachments() {
  const box = $("attachments");
  box.innerHTML = "";
  state.attachments.forEach((a, idx) => {
    const ext = (a.name.split(".").pop() || "").slice(0, 4);
    const card = el("div", "att-card");
    const thumb = el("span", "ext", a.preview ? `<img src="${a.preview}" alt="">` : a.uploading ? '<span class="spin"></span>' : esc(ext || "file"));
    card.append(thumb, el("span", "nm", esc(a.name)));
    const x = el("button", "", "✕");
    x.type = "button";
    x.onclick = () => { state.attachments.splice(idx, 1); renderAttachments(); };
    card.appendChild(x);
    box.appendChild(card);
  });
  autosize();
}

async function uploadFiles(files) {
  for (const file of files) {
    const a = { name: file.name || "image.png", uploading: true };
    if (file.type.startsWith("image/") && file.size < 8e6) a.preview = URL.createObjectURL(file);
    state.attachments.push(a);
    renderAttachments();
    const form = new FormData();
    form.append("file", file, a.name);
    const url = state.current == null ? "/api/uploads" : `/api/sessions/${state.current}/upload`;
    try {
      Object.assign(a, await api(url, { form }), { uploading: false });
    } catch (e) {
      state.attachments.splice(state.attachments.indexOf(a), 1);
      toast(`Файл не загрузился: ${e.message}`);
    }
    renderAttachments();
  }
}

function composeMessage() {
  const text = $("input").value.trim();
  const files = state.attachments.map((a) => (a.image ? `I've uploaded an image to ${a.path} — please take a look.` : `I've uploaded ${a.name} to ${a.path}`));
  return [...files, text].filter(Boolean).join("\n\n");
}

function clearComposer() {
  $("input").value = "";
  state.attachments = [];
  renderAttachments();
}

async function send() {
  if (state.attachments.some((a) => a.uploading)) { toast("Файлы ещё загружаются…"); return; }
  const message = composeMessage();
  if (!message) return;
  $("send-btn").disabled = true;
  try {
    if (state.current == null) {
      $("working").hidden = false;
      $("working-text").textContent = state.telegram ? "Создаю чат и топик…" : "Создаю чат…";
      const c = currentChoice();
      const res = await api("/api/sessions", { body: { text: message, web_only: !state.telegram, provider: c?.provider.id, model: c?.model?.id || undefined } });
      clearComposer();
      state.pending.set(res.thread_id, message);
      await refreshSessions();
      location.hash = `#${res.thread_id}`;
      return;
    }
    const s = currentSession();
    if (s && s.state === "dead") { $("working").hidden = false; $("working-text").textContent = "Бужу сессию…"; }
    await api(`/api/sessions/${state.current}/send`, { body: { text: message } });
    clearComposer();
    const p = userNode({ text: message });
    p.classList.add("pending");
    $("thread").appendChild(p);
    state.pending.set(state.current, message);
    toBottom();
    if (s) { s.state = "active"; renderHeader(); }
  } catch (e) {
    toast(`❌ ${e.message}`);
  } finally {
    $("working-text").textContent = "Кара работает…";
    if (state.current == null) $("working").hidden = true;
    autosize();
    $("input").focus();
  }
}

function renderToggle() {
  $("tg-toggle").classList.toggle("on", state.telegram);
  $("tg-toggle-text").textContent = state.telegram ? "Telegram" : "Без Telegram";
  $("tg-toggle").title = state.telegram ? "Чат появится и в Telegram-топике" : "Чат будет только здесь";
  $("disclaimer").textContent = state.telegram ? "Создам топик в группе «Кара» — переписка будет видна и там." : "Чат будет только в вебе, в Telegram ничего не появится.";
}

function currentChoice() {
  const providers = state.providers || [];
  const p = providers.find((x) => x.id === state.provider) || providers[0];
  if (!p) return null;
  const m = p.models.find((x) => x.id === state.model) || p.default;
  return { provider: p, model: m };
}

function choiceLabel(c) {
  if (!c) return "Модель";
  const name = c.model?.name || c.model?.id || c.provider.name;
  return name.toLowerCase().includes(c.provider.name.toLowerCase()) ? name : `${c.provider.name} · ${name}`;
}

async function loadModels() {
  if (!state.providers) {
    try {
      state.providers = (await api("/api/models")).providers;
    } catch (_) { state.providers = []; }
  }
  const c = currentChoice();
  if (c && c.provider.id !== state.provider) { state.provider = c.provider.id; state.model = ""; }
  $("model-name").textContent = choiceLabel(c);
}

function pickModel(provider, model) {
  state.provider = provider;
  state.model = model;
  store.set("kara-provider", provider);
  store.set("kara-model", model);
  loadModels();
}

// ═══ live events ════════════════════════════════════════════════════════════
function notify(s, e) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (!document.hidden && e.thread_id === state.current) return;
  if (e.role !== "assistant" || e.content_type !== "text" || !e.is_complete) return;
  const n = new Notification(s ? s.name : "Кара", { body: String(e.text).slice(0, 180), tag: `t${e.thread_id}`, icon: "/static/icon.svg" });
  n.onclick = () => { window.focus(); location.hash = `#${e.thread_id}`; n.close(); };
}

function connect() {
  state.es?.close();
  const es = new EventSource("/api/events");
  state.es = es;
  const online = (on) => { $("conn").classList.toggle("on", on); $("conn-text").textContent = on ? "на связи" : "нет связи"; };
  es.onopen = () => online(true);
  es.onerror = () => { online(false); api("/api/me").catch(() => {}); };
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
      notify(sessionById(e.thread_id), e);
    } else if (e.type === "resync" && state.current != null) {
      openSession(state.current);
    }
  };
}

// ═══ theme / profile ════════════════════════════════════════════════════════
function applyTheme(mode) {
  if (mode === "light" || mode === "dark") document.documentElement.dataset.theme = mode;
  else delete document.documentElement.dataset.theme;
  const dark = mode === "dark" || (mode !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  $("hljs-dark").disabled = !dark;
  $("hljs-light").disabled = dark;
}
let theme = store.get("kara-theme") || "system";
applyTheme(theme);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(theme));
const setTheme = (mode) => { theme = mode; store.set("kara-theme", mode); applyTheme(mode); };

function profileMenu() {
  const items = [
    { heading: "Тема" },
    { label: "Как в системе", icon: SVG.sun, check: theme === "system", action: () => setTheme("system") },
    { label: "Светлая", icon: SVG.sun, check: theme === "light", action: () => setTheme("light") },
    { label: "Тёмная", icon: SVG.sun, check: theme === "dark", action: () => setTheme("dark") },
    "sep",
  ];
  if ("Notification" in window && Notification.permission !== "granted") {
    items.push({ label: "Включить уведомления", icon: SVG.bell, action: () => Notification.requestPermission() });
  }
  items.push({ label: "Выйти", icon: SVG.out, action: async () => { await api("/api/logout", { body: {} }).catch(() => {}); showLogin(); } });
  return items;
}

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
  if (store.get("kara-collapsed") === "1" && !isMobile()) $("app").classList.add("collapsed");
  await refreshSessions();
  connect();
  route();
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
window.addEventListener("hashchange", route);
$("composer").onsubmit = (ev) => { ev.preventDefault(); send(); };
$("input").addEventListener("input", autosize);
$("input").addEventListener("keydown", (ev) => {
  if (handleFormatKey(ev)) return;
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && !isMobile()) { ev.preventDefault(); send(); }
});
// ═══ composer formatting (Telegram Desktop shortcuts → Markdown) ════════════
// ev.code, not ev.key: the shortcuts must work in the Russian layout too.
const FORMAT_KEYS = {
  KeyB: { mark: "**" },
  KeyI: { mark: "_" },
  KeyE: { mark: "`" },
  "shift+KeyM": { mark: "`", block: "```" },
  "shift+KeyX": { mark: "~~" },
  "shift+KeyP": { mark: "||" },
  KeyK: { link: true },
  "shift+Period": { quote: true },
  "shift+KeyN": { clear: true },
};

function replaceRange(ta, start, end, text, selStart, selEnd) {
  ta.focus();
  ta.setSelectionRange(start, end);
  // execCommand keeps Ctrl+Z working; setRangeText is the fallback.
  if (!document.execCommand("insertText", false, text)) ta.setRangeText(text, start, end, "end");
  ta.setSelectionRange(selStart, selEnd);
  autosize();
}

function toggleMark(ta, mark) {
  const { selectionStart: a, selectionEnd: b, value: v } = ta;
  const n = mark.length;
  // Already wrapped (markers just outside or just inside the selection) → unwrap.
  if (v.slice(a - n, a) === mark && v.slice(b, b + n) === mark) {
    return replaceRange(ta, a - n, b + n, v.slice(a, b), a - n, b - n);
  }
  const sel = v.slice(a, b);
  if (sel.length >= 2 * n && sel.startsWith(mark) && sel.endsWith(mark)) {
    return replaceRange(ta, a, b, sel.slice(n, -n), a, b - 2 * n);
  }
  // Markers must hug the text: leave edge whitespace outside.
  const core = sel.trim();
  const lead = sel.slice(0, sel.length - sel.trimStart().length);
  const trail = sel.slice(sel.trimEnd().length);
  const start = a + lead.length;
  replaceRange(ta, a, b, `${lead}${mark}${core}${mark}${trail}`, start + n, start + n + core.length);
}

function codeBlock(ta, fence) {
  const { selectionStart: a, selectionEnd: b, value: v } = ta;
  const sel = v.slice(a, b).replace(/^\n+|\n+$/g, "");
  const pre = a > 0 && v[a - 1] !== "\n" ? "\n" : "";
  const post = b < v.length && v[b] !== "\n" ? "\n" : "";
  const head = `${pre}${fence}\n`;
  replaceRange(ta, a, b, `${head}${sel}\n${fence}${post}`, a + head.length, a + head.length + sel.length);
}

function insertLink(ta) {
  const { selectionStart: a, selectionEnd: b, value: v } = ta;
  const sel = v.slice(a, b);
  if (/^https?:\/\/\S+$/.test(sel)) return replaceRange(ta, a, b, `[](${sel})`, a + 1, a + 1);
  const text = `[${sel}](https://)`;
  const urlAt = a + sel.length + 3;
  replaceRange(ta, a, b, text, sel ? urlAt : a + 1, sel ? urlAt + 8 : a + 1);
}

function toggleQuote(ta) {
  const { selectionStart: a, selectionEnd: b, value: v } = ta;
  const start = v.lastIndexOf("\n", a - 1) + 1;
  let end = v.indexOf("\n", b);
  if (end < 0) end = v.length;
  const lines = v.slice(start, end).split("\n");
  const quoted = lines.every((l) => l.startsWith("> "));
  const out = lines.map((l) => (quoted ? l.slice(2) : `> ${l}`)).join("\n");
  replaceRange(ta, start, end, out, start, start + out.length);
}

function clearFormatting(ta) {
  const { selectionStart: a, selectionEnd: b, value: v } = ta;
  const out = v.slice(a, b)
    .replace(/^```[^\n]*\n?|\n?```$/gm, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^> /gm, "")
    .replace(/\*\*|~~|\|\||`|(?<!\w)_|_(?!\w)/g, "");
  replaceRange(ta, a, b, out, a, a + out.length);
}

function handleFormatKey(ev) {
  if (!(ev.ctrlKey || ev.metaKey) || ev.altKey) return false;
  const f = FORMAT_KEYS[(ev.shiftKey ? "shift+" : "") + ev.code];
  if (!f) return false;
  ev.preventDefault();
  const ta = ev.currentTarget;
  if (f.link) insertLink(ta);
  else if (f.quote) toggleQuote(ta);
  else if (f.clear) clearFormatting(ta);
  else if (f.block && ta.value.slice(ta.selectionStart, ta.selectionEnd).includes("\n")) codeBlock(ta, f.block);
  else toggleMark(ta, f.mark);
  return true;
}

$("input").addEventListener("paste", (ev) => {
  const files = [...(ev.clipboardData?.files || [])];
  if (files.length) { ev.preventDefault(); uploadFiles(files); }
});
$("file-input").onchange = (ev) => { uploadFiles([...ev.target.files]); ev.target.value = ""; };
const dropZone = $("dock");
["dragenter", "dragover"].forEach((t) => document.addEventListener(t, (ev) => { if (ev.dataTransfer?.types?.includes("Files")) { ev.preventDefault(); $("composer").classList.add("drag"); } }));
["dragleave", "drop"].forEach((t) => document.addEventListener(t, (ev) => { if (t === "drop" || ev.target === document.documentElement) $("composer").classList.remove("drag"); }));
document.addEventListener("drop", (ev) => {
  if (!ev.dataTransfer?.files?.length) return;
  ev.preventDefault();
  $("composer").classList.remove("drag");
  uploadFiles([...ev.dataTransfer.files]);
});
void dropZone;

$("filter").addEventListener("input", (ev) => { state.filter = ev.target.value; renderSessions(); });
$("collapse-btn").onclick = () => { $("app").classList.add("collapsed"); store.set("kara-collapsed", "1"); };
$("menu-btn").onclick = () => {
  if (isMobile()) $("app").classList.add("drawer");
  else { $("app").classList.remove("collapsed"); store.set("kara-collapsed", ""); }
};
$("scrim").onclick = () => $("app").classList.remove("drawer");
$("session-list").addEventListener("click", (ev) => { if (ev.target.closest(".s-item") && isMobile()) $("app").classList.remove("drawer"); });
$("new-btn").onclick = () => { if (isMobile()) $("app").classList.remove("drawer"); };
$("pane-btn").onclick = () => { $("pane").hidden = !$("pane").hidden; pollPane(); };
$("stop-btn").onclick = () => sendKey("Escape");
$("gone-keep").onclick = async () => {
  const s = currentSession();
  if (!s) return;
  try {
    await api(`/api/sessions/${s.thread_id}/keep`, { body: {} });
    s.topic_deleted = false;
    renderHeader();
    toast("Чат остаётся в вебе");
  } catch (e) { toast(e.message); }
};
$("gone-delete").onclick = async () => {
  const s = currentSession();
  if (!s) return;
  try {
    await api(`/api/sessions/${s.thread_id}/delete`, { body: {} });
    toast("Чат удалён");
    location.hash = "#new";
    await refreshSessions();
  } catch (e) { toast(e.message); }
};
$("title-btn").onclick = () => {
  const s = currentSession();
  if (s) openMenu(sessionMenu(s, null), menuBelow($("title-btn")), $("title-btn"));
};
$("profile-btn").onclick = () => {
  const r = $("profile-btn").getBoundingClientRect();
  openMenu(profileMenu(), { x: r.left, y: r.top - 8, top: r.top });
};
$("tg-toggle").onclick = () => { state.telegram = !state.telegram; store.set("kara-telegram", state.telegram ? "1" : "0"); renderToggle(); };
$("model-btn").onclick = async () => {
  await loadModels();
  const c = currentChoice();
  const items = [];
  for (const p of state.providers || []) {
    if (items.length) items.push("sep");
    items.push({ heading: p.name });
    const models = p.models.length ? p.models : [p.default];
    for (const m of models) {
      const isDefault = m.id === p.default.id;
      items.push({
        label: m.name || m.id,
        sub: isDefault ? "по умолчанию" : "",
        check: c && c.provider.id === p.id && c.model?.id === m.id,
        action: () => pickModel(p.id, m.id),
      });
    }
  }
  if (!items.length) items.push({ label: "Агенты недоступны" });
  openMenu(items, menuBelow($("model-btn")));
};
$("jump-btn").onclick = () => { toBottom(); $("jump-btn").hidden = true; };
$("scroller").addEventListener("scroll", () => {
  const s = $("scroller");
  if (nearBottom()) $("jump-btn").hidden = true;
  if (s.scrollTop < 400) loadOlder();
  closeMenu();
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) { pollPane(); if (state.current != null) scheduleFetch(); } });
document.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && ev.key.toLowerCase() === "o") { ev.preventDefault(); location.hash = "#new"; }
});

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
api("/api/me").then(boot).catch(() => showLogin());
