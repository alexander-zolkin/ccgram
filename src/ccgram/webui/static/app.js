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
  t.classList.remove("out");
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    t.classList.add("out");
    toast.timer = setTimeout(() => { t.hidden = true; t.classList.remove("out"); }, 200);
  }, 3500);
}

const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

// Restart a one-shot CSS animation class on an element.
function replay(node, cls) {
  node.classList.remove(cls);
  void node.offsetWidth;
  node.classList.add(cls);
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
  bellOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M8.7 3.6A6 6 0 0 1 18 8c0 3 .6 5 1.3 6.4M17 17H3s3-2 3-9c0-.6.1-1.2.3-1.8M10 21h4M3 3l18 18"/></svg>',
  bellRing: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4M2.5 7.5A9 9 0 0 1 5 3M21.5 7.5A9 9 0 0 0 19 3"/></svg>',
  bellAuto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4"/><path d="M10 12.5l2-5 2 5M10.6 11h2.8" stroke-width="1.5"/></svg>',
  out: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>',
  key: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="8" cy="15" r="4"/><path d="M10.8 12.2L20 3M16 7l3 3M14 9l2 2"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4" stroke-linecap="round"/></svg>',
  globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
  tgGone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 5L3 11.5l5 1.8M21 5l-3 14-6.5-4.5M21 5L8 13.3v5.2l3.5-3.2M3 3l18 18"/></svg>',
  check2: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  branch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="7" r="2"/><path d="M6 7v10M18 9c0 5-12 3-12 8"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>',
};

// ═══ popover menu ═══════════════════════════════════════════════════════════
// items: {label, icon, sub, danger, check, href, action} | "sep" | {heading}
function openMenu(items, at, owner, cls = "") {
  const menu = $("menu");
  menu.className = `menu ${cls}`.trim();
  menu.innerHTML = "";
  for (const it of items) {
    if (it === "sep") { menu.appendChild(el("div", "sep")); continue; }
    if (it.heading) { menu.appendChild(el("div", "label", esc(it.heading))); continue; }
    const node = el(it.href ? "a" : "button", [it.danger ? "danger" : "", it.cls || ""].join(" ").trim());
    if (it.disabled) node.disabled = true;
    if (it.href) { node.href = it.href; node.target = "_blank"; node.rel = "noopener"; }
    node.innerHTML = `<span class="ico">${it.icon || ""}</span><span>${esc(it.label)}${it.sub ? `<span class="sub">${esc(it.sub)}</span>` : ""}</span>${it.check ? '<span class="check">✓</span>' : ""}`;
    node.onclick = () => { closeMenu(); if (it.action) it.action(); };
    menu.appendChild(node);
  }
  menu.hidden = false;
  replay(menu, "pop");
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

function confirmDialog(title, text, okLabel, danger = true) {
  return new Promise((resolve) => {
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    $("confirm-ok").textContent = okLabel;
    $("confirm-ok").className = `btn ${danger ? "danger-fill" : "primary"}`;
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
  // Thinking text is redacted in transcripts: a thinking-only block has
  // nothing to expand, so it is a plain label without the chevron.
  block.classList.toggle("thought-only", !tools.length);
  block.querySelector(".steps-head").innerHTML = `<span class="sum">${esc(what)}${names && !running ? ` · <span class="muted">${esc(names)}</span>` : ""}</span>${running ? '<span class="spin"></span>' : errors ? `<span style="color:var(--err)">${errors} ✕</span>` : ""}${tools.length ? SVG.chev : ""}`;
}

function stepsBlock() {
  const block = el("div", "steps");
  block.innerHTML = '<div class="steps-head"></div><div class="steps-body"><div class="steps-inner"></div></div>';
  block.querySelector(".steps-head").onclick = () => { if (!block.classList.contains("thought-only")) block.classList.toggle("open"); };
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
  pending: new Map(),     // session id → {text, at} shown until the transcript has it
  providers: null,          // [{id, name, models, default}]
  provider: store.get("kara-provider") || "claude",
  model: store.get("kara-model") || "",
  telegram: store.get("kara-telegram") !== "0",
  effort: store.get("kara-effort") || "",   // "" = ccgram default (xhigh)
};

const EFFORTS = [
  ["", "По умолчанию", "xhigh — как в Telegram"],
  ["low", "Низкое", "быстро и дёшево"],
  ["medium", "Среднее", ""],
  ["high", "Высокое", ""],
  ["xhigh", "Очень высокое", ""],
  ["max", "Максимальное", "дольше всех думает"],
];

// ═══ thread rendering ═══════════════════════════════════════════════════════
function userNode(it) {
  const n = el("div", "u-msg");
  const files = [];
  const text = String(it.text || "").replace(/^I've uploaded (?:an image to (\S+) — please take a look\.|(.+?) to (\S+))\s*$/gm, (_, img, name, path) => {
    files.push(img ? { name: img.split("/").pop().replace(/^\d{8}-\d{6}-/, ""), path: img } : { name, path });
    return "";
  }).trim();
  if (files.length || it.images) {
    const att = el("div", "att");
    for (const f of files) {
      if (f.path && state.current != null && /\.(png|jpe?g|gif|webp)$/i.test(f.path)) {
        const img = el("img", "att-img");
        img.loading = "lazy";
        img.alt = f.name;
        img.src = `/api/sessions/${state.current}/file?path=${encodeURIComponent(f.path)}`;
        img.onerror = () => img.replaceWith(el("span", "", `🖼 ${esc(f.name)}`));
        img.onclick = () => window.open(img.src, "_blank");
        att.appendChild(img);
      } else att.appendChild(el("span", "", `📄 ${esc(f.name)}`));
    }
    if (it.images) att.appendChild(el("span", "", `🖼 ${it.images}`));
    n.appendChild(att);
  }
  if (text) { const md = renderMarkdown(text); md.className = "u-md"; n.appendChild(md); }
  n.appendChild(el("div", "u-meta", `<span class="msg-time">${esc(hhmm(it.ts))}</span><span class="tick">${TICK.read}</span>`));
  return n;
}

const TICK = {
  clock: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.2 1.4"/></svg>',
  sent: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 8.5l3 3 7-7"/></svg>',
  read: '<svg viewBox="0 0 20 16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M1.5 8.5l3 3 7-7M8.5 11.5l7-7"/></svg>',
};

// A just-sent message: shown at once, before the server answers (Telegram style).
function pendingNode(text, at = Date.now()) {
  const n = userNode({ text });
  n.classList.add("pending");
  n.querySelector(".msg-time").textContent = hhmm(at);
  n.querySelector(".tick").innerHTML = TICK.clock;
  return n;
}

// The bubble flies up out of the composer into its place in the thread.
function flyIn(node) {
  if (reducedMotion() || !node.animate) return;
  const from = $("input").getBoundingClientRect();
  const to = node.getBoundingClientRect();
  const dx = Math.min(0, from.left - to.left);
  const dy = from.top - to.top;
  node.animate(
    [
      { transform: `translate(${dx}px, ${dy}px) scale(.94)`, opacity: 0 },
      { opacity: 1, offset: 0.35 },
      { transform: "none", opacity: 1 },
    ],
    { duration: 380, easing: "cubic-bezier(.2,.9,.25,1)" },
  );
}

function assistantNode(it) {
  const n = el("div", "a-msg");
  n.appendChild(renderMarkdown(it.text));
  const actions = el("div", "msg-actions");
  const copy = el("button", "copy-btn", "Копировать");
  copy.type = "button";
  copy.onclick = () => copyText(it.text, copy);
  actions.append(copy);
  if (state.tts) {
    const listen = el("button", "copy-btn listen-btn", "Прослушать");
    listen.type = "button";
    listen.onclick = () => speak(n, listen);
    actions.append(listen);
  }
  actions.append(el("span", "msg-time", esc(hhmm(it.ts))));
  n.appendChild(actions);
  return n;
}

// Claude Code stores shell text HTML-escaped (`-&gt;`): decode once for display.
const unentity = (str) => { const t = document.createElement("textarea"); t.innerHTML = String(str || ""); return t.value; };

// "!cmd" typed in Claude Code (or sent from here / Telegram) — a terminal card.
function bashNode(raw) {
  const it = { ...raw, command: unentity(raw.command), stdout: unentity(raw.stdout), stderr: unentity(raw.stderr) };
  const n = el("div", "bash-card");
  const head = el("div", "bash-head");
  head.innerHTML = `<span class="bash-prompt">$</span><code class="bash-cmd">${esc(it.command || "")}</code>`;
  const copy = el("button", "copy-btn", "Копировать");
  copy.type = "button";
  copy.onclick = () => copyText(it.command || "", copy);
  head.appendChild(copy);
  const body = el("div", "bash-body", '<div class="bash-wait"><span class="spin"></span> выполняется…</div>');
  n.append(head, body);
  n._fill = (r) => {
    const out = unentity(r.stdout);
    const err = unentity(r.stderr);
    body.innerHTML = "";
    if (out.trim()) body.appendChild(el("pre", "bash-out", esc(out)));
    if (err.trim()) body.appendChild(el("pre", "bash-out err", esc(err)));
    if (!out.trim() && !err.trim()) body.appendChild(el("div", "bash-empty", "без вывода"));
    const lines = out.split("\n").length + err.split("\n").length;
    if (lines > 18) {
      n.classList.add("long");
      const more = el("button", "bash-more", `Показать всё · ${lines} строк`);
      more.type = "button";
      more.onclick = () => { n.classList.toggle("expanded"); more.textContent = n.classList.contains("expanded") ? "Свернуть" : `Показать всё · ${lines} строк`; };
      body.appendChild(more);
    }
    n.querySelector(".bash-head").classList.toggle("failed", Boolean(err.trim()) && !out.trim());
  };
  if (it.kind === "bash_result") { head.querySelector(".bash-cmd").textContent = "(команда выше)"; n._fill(it); n._filled = true; }
  return n;
}

function commandNode(it) {
  const n = el("div", "cmd-chip");
  n.innerHTML = `<code>${esc(it.kind === "command" ? it.text : "")}</code>`;
  n._fill = (r) => { if (r.text) n.appendChild(el("span", "cmd-out", esc(unentity(r.text).split("\n")[0].slice(0, 160)))); };
  if (it.kind === "command_result") { n.innerHTML = ""; n._fill(it); n._filled = true; }
  return n;
}

function summaryNode(it) {
  const n = el("details", "summary-card");
  n.innerHTML = '<summary>Контекст сжат — краткое содержание предыдущей части</summary>';
  const body = renderMarkdown(String(it.text || "").replace(/^This session is being continued[^\n]*\n+/, ""));
  body.className = "summary-body";
  n.appendChild(body);
  return n;
}

// "Baked for 19s" in Claude Code: how long a finished turn took, what it wrote.
const secsRu = (sec) => (sec < 60 ? `${sec} с` : sec < 3600 ? `${Math.floor(sec / 60)} мин${sec % 60 ? ` ${sec % 60} с` : ""}` : `${Math.floor(sec / 3600)} ч ${Math.floor((sec % 3600) / 60)} мин`);
const clock = (sec) => { const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), x = String(sec % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`; };
const tokRu = (n) => (n < 1000 ? String(Math.round(n)) : n < 1e4 ? `${(n / 1000).toFixed(1).replace(".", ",")}k` : n < 1e6 ? `${Math.round(n / 1000)}k` : `${(n / 1e6).toFixed(1).replace(".", ",")}M`);

function turnNode(it) {
  const sec = Math.max(1, Math.round((it.ms || 0) / 1000));
  const n = el("div", "turn-stat");
  const out = it.out ? ` · ${tokRu(it.out)} токенов` : "";
  n.innerHTML = `<span class="spark-end"></span><span>${esc(secsRu(sec) + out)}</span>`;
  n.dataset.tip = `Ход занял ${secsRu(sec)}` + (it.out ? `; модель написала ${it.out.toLocaleString("ru-RU")} токенов (с размышлениями)` : "");
  return n;
}

function itemKey(it) {
  if (it.kind === "tool") return "t:" + it.id;
  if (it.kind === "tool_result") return null;
  return it.uuid ? `${it.kind}:${it.uuid}` : null;
}

function buildBlocks(items, continueFrom, live = false) {
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
        if (live) node.classList.add("enter");
        steps.querySelector(".steps-inner").appendChild(node);
      }
      refreshStepsHead(steps);
      continue;
    }
    if (it.kind === "bash_result" || it.kind === "command_result") {
      // Output lands in the card of the command just above it.
      // Appending live: the command may already be in the thread.
      const pool = [...frag.children].reverse().concat(live ? [...$("thread").children].slice(-6).reverse() : []);
      const host = pool.find((n) => n.classList.contains(it.kind === "bash_result" ? "bash-card" : "cmd-chip"));
      if (host && !host._filled) { host._fill(it); host._filled = true; continue; }
    }
    steps = null;
    const node = { user: userNode, bash: bashNode, bash_result: bashNode, command: commandNode, command_result: commandNode, summary: summaryNode, turn: turnNode }[it.kind]?.(it) || assistantNode(it);
    // The transcript copy replaces the pending bubble in place: no re-entry.
    if ((it.kind === "user" || it.kind === "bash" || it.kind === "command") && clearPending()) node.classList.add("swap");
    frag.appendChild(node);
  }
  return frag;
}

function clearPending() {
  const p = $("thread").querySelector(".u-msg.pending:not(.failed)");
  p?.remove();
  if (state.current != null) state.pending.delete(state.current);
  return Boolean(p);
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
function toBottom(smooth = false) {
  const s = $("scroller");
  s.scrollTo({ top: s.scrollHeight, behavior: smooth && !reducedMotion() ? "smooth" : "auto" });
}

function appendItems(items) {
  const thread = $("thread");
  if (state.inflight?.tid === state.current && items.some((it) => it.kind === "turn")) {
    state.inflight = null;
    renderHeader();
  }
  const stick = nearBottom();
  const frag = buildBlocks(items, thread.lastElementChild, true);
  for (const n of frag.children) {
    if (n.classList.contains("swap")) continue;
    n.classList.add("enter");
    if (n.classList.contains("a-msg")) {
      [...n.firstElementChild.children].slice(0, 12).forEach((p, i) => { p.style.animationDelay = `${i * 70}ms`; p.classList.add("reveal"); });
    }
  }
  thread.appendChild(frag);
  markLast();
  refreshMsgStatus();
  autoOpenLive();
  if (stick) toBottom(true); else if (items.length) $("jump-btn").hidden = false;
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
  if (last && last.classList.contains("steps") && turnRunning(s)) last.classList.add("open");
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
  $("cmd-palette").hidden = true;
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
  loadDraft();
  syncSendBtn();
  $("title-btn").hidden = true;
  $("pane-btn").hidden = true;
  $("sound-btn").hidden = true;
  $("chat-meta").hidden = true;
  $("pane").hidden = true;
  $("chat-state").innerHTML = "";
  $("working").hidden = true;
  $("prompt-card").hidden = true;
  $("tg-toggle").hidden = false;
  $("model-btn").hidden = false;
  $("cwd-btn").hidden = false;
  $("home-actions").hidden = false;
  $("mode-btn").hidden = true;
  $("live").hidden = true;
  renderCwd();
  $("input").placeholder = "Как я могу помочь?";
  setTitle("Кара");
  updateLed();
  renderToggle();
  renderSessions();
  showHubCached();
  document.querySelector(".main").scrollTop = 0;
  loadHub();
  loadModels();
  if (!isMobile()) $("input").focus();
}

async function openSession(tid) {
  $("app").classList.remove("home-mode", "drawer");
  $("home").hidden = true;
  $("tg-toggle").hidden = true;
  $("model-btn").hidden = true;
  $("effort-btn").hidden = true;
  $("cwd-btn").hidden = true;
  $("home-actions").hidden = true;
  hideHub();
  $("live").hidden = true;
  $("mode-btn").hidden = true;
  $("mode-btn").dataset.mode = "";
  $("input").placeholder = "Ответить Каре…";
  state.current = tid;
  state.unread.delete(tid);
  loadDraft();
  if (location.hash !== `#${tid}`) history.replaceState(null, "", `#${tid}`);
  $("pane-btn").hidden = false;
  $("prompt-card").hidden = true;
  $("chat-meta").hidden = true;
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
    if (pending) { const p = pendingNode(pending.text, pending.at); p.querySelector(".tick").innerHTML = TICK.sent; p.dataset.sent = "1"; $("thread").appendChild(p); }
    replay($("thread"), "fade");
    refreshMsgStatus();
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
  probeTopic(tid);
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
  const hits = state.hits && state.hits.q === state.filter.trim() ? state.hits.results : [];
  if (hits.length) nav.appendChild(hitsBox(state.hits.q, hits));
  else if (!list.length) nav.appendChild(el("div", "group-label", f ? "Ничего не нашлось" : "Чатов пока нет"));
  renderHeader();
}

const TIP = {
  web: "Только в вебе: у этого чата нет топика в Telegram",
  gone: "Топик удалён в Telegram — чат продолжает жить здесь",
};

// One floating tooltip for every [data-tip]: instant, never clipped by the
// sidebar's overflow. On touch screens a tap shows the same text as a toast.
(() => {
  const tip = el("div", "tip");
  tip.hidden = true;
  document.body.appendChild(tip);
  document.addEventListener("pointerover", (ev) => {
    const t = ev.target.closest?.("[data-tip]");
    if (!t || ev.pointerType === "touch") { tip.hidden = true; return; }
    tip.textContent = t.dataset.tip;
    tip.hidden = false;
    const r = t.getBoundingClientRect();
    const w = tip.offsetWidth;
    tip.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
    tip.style.top = `${r.bottom + 6}px`;
  });
  document.addEventListener("scroll", () => { tip.hidden = true; }, true);
  document.addEventListener("click", (ev) => {
    // Only for passive markers (flags, ticks): buttons keep doing their job.
    const t = ev.target.closest?.(".s-flag[data-tip], .tick[data-tip], .u-hit[data-tip]");
    if (t && matchMedia("(hover: none)").matches) { ev.preventDefault(); ev.stopPropagation(); toast(t.dataset.tip); }
  }, true);
})();

function sessionItem(s) {
  const item = el("a", `s-item${s.thread_id === state.current ? " active" : ""}${state.unread.has(s.thread_id) ? " unread" : ""}`);
  item.href = `#${s.thread_id}`;
  // Flags sit outside the name so the name ellipsizes and they never clip.
  const flag = s.topic_deleted
    ? `<span class="s-flag gone" data-tip="${esc(TIP.gone)}">${SVG.tgGone}</span>`
    : s.web_only ? `<span class="s-flag" data-tip="${esc(TIP.web)}">${SVG.globe}</span>` : "";
  item.innerHTML = `<span class="dot ${esc(s.state)}" title="${esc(STATE_LABEL[s.state] || "")}"></span><span class="s-name">${esc(s.name)}</span>${flag}`;
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
  // "Мои дела" links items to chats by id: names and states come from here.
  if (state.current == null && state.hubData?.pending && $("hub-pending")) renderPending(state.hubData.pending);
}

function renderHeader() {
  const s = currentSession();
  if (!s) return;
  $("title-btn").hidden = false;
  if (!$("chat-name").querySelector("input")) $("chat-name").textContent = s.name;
  $("chat-badge").hidden = !s.web_only;
  const working = turnRunning(s);
  const shown = working && s.state === "idle" ? "active" : s.state;
  const label = STATE_LABEL[shown] || "";
  $("chat-state").innerHTML = label ? `<span class="dot ${esc(shown)}"></span><span>${esc(label)}</span>` : "";
  if ($("working").hidden === working) {
    // The working line takes room from the thread: keep the last bubble in view.
    const stick = nearBottom();
    $("working").hidden = !working;
    if (stick) toBottom();
  }
  syncSendBtn();
  renderSoundBtn(s);
  $("gone-card").hidden = !s.topic_deleted;
  $("disclaimer").textContent = s.web_only ? "Этот чат только в вебе — в Telegram не дублируется." : "Этот чат дублируется в Telegram-топик.";
  setTitle(`${working ? "⏳ " : ""}${s.name} — Кара`);
  refreshMsgStatus();
  updateLed();
}

function setTitle(base) {
  const n = state.unread.size;
  document.title = n ? `(${n}) ${base}` : base;
}

// ═══ favicon LED ════════════════════════════════════════════════════════════
// The tab icon follows the open chat: steady ring = idle, spinning amber arc =
// working, pulsing red = a question or permission prompt waits for an answer.
// Chrome doesn't animate SVG favicons, so the frames are swapped from here.
function ledFrame(color, angle, dim) {
  const arc = angle == null ? "" : ` stroke-linecap="round" stroke-dasharray="70 30.53" transform="rotate(${angle} 32 32)"`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#262624"/><g opacity="${dim ? 0.45 : 1}"><circle cx="32" cy="32" r="16" fill="none" stroke="${color}" stroke-opacity=".16" stroke-width="14"/><circle cx="32" cy="32" r="22.5" fill="none" stroke="${color}" stroke-opacity=".4" stroke-width="1.5"/><circle cx="32" cy="32" r="16" fill="none" stroke="${color}" stroke-width="5.5"${arc}/></g></svg>`;
  return "data:image/svg+xml," + encodeURIComponent(svg);
}
const LED_FRAMES = {
  working: { ms: 100, frames: Array.from({ length: 12 }, (_, i) => ledFrame("#f2b544", i * 30)) },
  attention: { ms: 600, frames: [ledFrame("#f0614c"), ledFrame("#f0614c", null, true)] },
};
let ledMode = "idle";
let ledTimer = null;

function setLed(mode) {
  if (mode === ledMode) return;
  ledMode = mode;
  clearInterval(ledTimer);
  const link = document.querySelector('link[rel="icon"]');
  const anim = LED_FRAMES[mode];
  if (!anim) { link.href = "/static/icon.svg"; return; }
  let i = 0;
  link.href = anim.frames[0];
  ledTimer = setInterval(() => { i = (i + 1) % anim.frames.length; link.href = anim.frames[i]; }, anim.ms);
}

function updateLed() {
  const s = currentSession();
  setLed(!s ? "idle" : !$("prompt-card").hidden ? "attention" : turnRunning(s) ? "working" : "idle");
}

// ═══ notifications per chat ═════════════════════════════════════════════════
// "auto": Telegram copies are silent while the chat is driven from here;
// "on"/"off": always ring / never ring. "off" also mutes browser notifications.
// Two independent channels, each with a global and a per-chat switch:
//   Telegram — global "Звук в Telegram" (profile) × per-topic auto / on / off
//   Web      — global "Веб-уведомления" (profile) × per-chat on / off
const webOn = (s) => !(state.webOff && state.webOff.has(s.thread_id));
function tgRings(s) {
  return !s.web_only && !state.tgMuted && (s.sound || "auto") !== "off";
}
function soundInfo(s) {
  const tg = tgRings(s);
  const web = webOn(s) && !state.webMuted;
  const mode = s.sound || "auto";
  const tgText = s.web_only ? "Telegram: нет топика" : state.tgMuted ? "Telegram: выключен везде" : mode === "off" ? "Telegram: без звука" : mode === "on" ? "Telegram: всегда со звуком" : s.quiet_now ? "Telegram: авто (сейчас тихо — пишешь из веба)" : "Telegram: авто";
  const webText = state.webMuted ? "веб: выключены везде" : webOn(s) ? "веб: включены" : "веб: выключены";
  const title = `${tgText} · ${webText}`;
  if (!tg && !web) return { icon: SVG.bellOff, title, off: true };
  if (tg && mode === "on") return { icon: SVG.bellRing, title };
  if (tg && mode === "auto") return { icon: SVG.bellAuto, title };
  return { icon: SVG.bell, title };
}

function renderSoundBtn(s) {
  const b = $("sound-btn");
  b.hidden = s.sound === undefined; // backend without sound modes
  const info = soundInfo(s);
  if (b.dataset.sig !== info.title) {
    b.dataset.sig = info.title;
    b.innerHTML = info.icon;
  }
  b.title = info.title;
  b.classList.toggle("muted-bell", Boolean(info.off));
}

async function setSound(s, body) {
  const prev = { sound: s.sound, web: webOn(s) };
  if (body.mode) s.sound = body.mode;
  if ("web" in body) { if (body.web) state.webOff.delete(s.thread_id); else state.webOff.add(s.thread_id); }
  renderHeader();
  replay($("sound-btn"), "ring");
  try {
    await api(`/api/sessions/${s.thread_id}/sound`, { body });
    toast(soundInfo(s).title);
  } catch (e) {
    s.sound = prev.sound;
    if (prev.web) state.webOff.delete(s.thread_id); else state.webOff.add(s.thread_id);
    renderHeader();
    toast(e.message);
  }
}

function soundMenu(s) {
  const mode = s.sound || "auto";
  const items = [];
  if (!s.web_only) {
    items.push({ heading: state.tgMuted ? "Telegram · сейчас выключен везде (профиль)" : "Telegram — этот чат" });
    items.push(
      { label: "Авто", sub: "Тихо, пока пишешь отсюда; иначе со звуком", icon: SVG.bellAuto, check: mode === "auto", action: () => setSound(s, { mode: "auto" }) },
      { label: "Всегда со звуком", icon: SVG.bellRing, check: mode === "on", action: () => setSound(s, { mode: "on" }) },
      { label: "Без звука", sub: "Этот топик молчит, даже когда Telegram включён", icon: SVG.bellOff, check: mode === "off", action: () => setSound(s, { mode: "off" }) },
      "sep",
    );
  }
  items.push({ heading: state.webMuted ? "Веб · сейчас выключены везде (профиль)" : "Веб — этот чат" });
  items.push(
    { label: "Уведомлять", sub: state.pushOn ? "Push на этом устройстве включён" : "Push на этом устройстве выключен — включи в профиле", icon: SVG.bell, check: webOn(s), action: () => setSound(s, { web: true }) },
    { label: "Не уведомлять", icon: SVG.bellOff, check: !webOn(s), action: () => setSound(s, { web: false }) },
  );
  return items;
}

function effortMenu(s) {
  return [
    { heading: "Усилие этой сессии" },
    ...EFFORTS.filter(([id]) => id).map(([id, label, sub]) => ({
      label, sub, action: async () => {
        try {
          await api(`/api/sessions/${s.thread_id}/send`, { body: { text: `/effort ${id}` } });
          toast(`Усилие: ${label.toLowerCase()}`);
        } catch (e) { toast(e.message); }
      },
    })),
  ];
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

// Numbered choices of a Claude Code prompt ("❯ 1. Yes") become buttons.
function promptOptions(text) {
  const opts = [];
  for (const line of String(text).split("\n")) {
    const m = line.match(/^\s*(?:[❯›>]\s*)?(\d)\.\s+(.+?)\s*$/);
    if (m) opts.push({ key: m[1], label: m[2].replace(/\s*\((?:esc|tab)\)\s*$/i, ""), current: /^\s*[❯›>]/.test(line) });
  }
  return opts;
}

function renderPrompt(inter) {
  $("prompt-title").textContent = inter.name === "AskUserQuestion" ? "Кара задаёт вопрос" : /Permission/i.test(inter.name) ? "Кара просит разрешения" : "Кара ждёт выбора";
  const opts = promptOptions(inter.content);
  // Without the option lines the question reads cleaner; the buttons carry them.
  $("prompt-text").textContent = opts.length
    ? inter.content.split("\n").filter((l) => !/^\s*(?:[❯›>]\s*)?\d\.\s/.test(l) && !/^\s*Esc to cancel/i.test(l)).join("\n").trim()
    : inter.content;
  const sig = opts.map((o) => o.key + o.label).join("|");
  const box = $("prompt-options");
  if (box.dataset.sig !== sig) {
    box.dataset.sig = sig;
    box.innerHTML = "";
    for (const o of opts) {
      const b = el("button", `opt${/^(no|нет)\b/i.test(o.label) ? " no" : o.key === "1" ? " yes" : ""}`, `<span class="k">${o.key}</span><span>${esc(o.label)}</span>`);
      b.type = "button";
      b.onclick = () => { box.querySelectorAll("button").forEach((x) => { x.disabled = true; }); sendKey(o.key); };
      box.appendChild(b);
    }
  }
  $("prompt-keys").open = !opts.length;
}

const MODEL_NAMES = [[/opus[-_ ]?(\d)[-.](\d)/i, "Opus $1.$2"], [/sonnet[-_ ]?(\d)[-.](\d)/i, "Sonnet $1.$2"], [/haiku[-_ ]?(\d)[-.](\d)/i, "Haiku $1.$2"], [/fable[-_ ]?(\d)[-.](\d)/i, "Fable $1.$2"]];
function prettyModel(id) {
  for (const [re, name] of MODEL_NAMES) {
    const m = id.match(re);
    if (m) return name.replace("$1", m[1]).replace("$2", m[2]);
  }
  return id.replace(/^claude-/, "");
}
const kTokens = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : `${Math.round(n / 1000)}k`);

function renderMeta(meta) {
  const box = $("chat-meta");
  if (!meta || !meta.context) { box.hidden = true; return; }
  const cap = meta.context > 200000 || /1m/i.test(meta.model) ? 1000000 : 200000;
  const pct = Math.min(100, Math.round((meta.context / cap) * 100));
  box.hidden = false;
  box.title = `${meta.model} · контекст ${meta.context.toLocaleString("ru-RU")} из ${kTokens(cap)} токенов`;
  box.innerHTML = `<span class="m-name">${esc(prettyModel(meta.model))}</span><span class="ctx${pct >= 80 ? " hot" : ""}"><span class="ctx-bar"><i style="width:${pct}%"></i></span>${kTokens(meta.context)}</span>`;
}

async function pollPane() {
  clearTimeout(state.paneTimer);
  const tid = state.current;
  const s = currentSession();
  if (tid == null || !s) return;
  if (!document.hidden && s.state !== "dead") {
    try {
      const wantTerm = !$("pane").hidden;
      const [data, live, term] = await Promise.all([
        api(`/api/sessions/${tid}/pane`),
        api(`/api/sessions/${tid}/live`).catch(() => null),
        wantTerm ? api(`/api/sessions/${tid}/terminal`).catch(() => null) : null,
      ]);
      if (tid === state.current) {
        if (wantTerm) {
          const pre = $("pane-text");
          const stick = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
          if (term && term.alive) pre.innerHTML = ansiToHtml(term.ansi.replace(/\s+$/, ""));
          else pre.textContent = data.alive ? data.text.replace(/\s+$/, "") : "(сессия спит)";
          if (stick) pre.scrollTop = pre.scrollHeight;
        }
        state.lastLive = { tid, live };
        renderLive(live, s);
        if (s.provider === "claude" && (!state.modeAt || Date.now() - state.modeAt > 15000)) refreshMode(tid);
        const inter = data.interactive;
        $("prompt-card").hidden = !inter;
        if (inter) renderPrompt(inter);
        renderMeta(data.meta);
        updateLed();
      }
    } catch (_) { /* transient */ }
  } else if (s.state !== "dead") {
    // A background tab still watches for prompts, so the favicon can call you back.
    try {
      const data = await api(`/api/sessions/${tid}/pane`);
      if (tid === state.current) {
        $("prompt-card").hidden = !data.interactive;
        if (data.interactive) renderPrompt(data.interactive);
        updateLed();
      }
    } catch (_) { /* transient */ }
  }
  const fast = !document.hidden && (!$("pane").hidden || turnRunning(s) || !$("prompt-card").hidden);
  state.paneTimer = setTimeout(pollPane, fast ? 2000 : 6000);
}

// ═══ composer ═══════════════════════════════════════════════════════════════
function autosize() {
  const t = $("input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.4) + "px";
  syncSendBtn();
}

const SEND_ICON = {
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M12 19V5M5 12l7-7 7 7"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>',
};
// While a turn runs and there is nothing to send, the send button stops it
// (Esc in Claude Code); typing turns it back into send — a message typed
// mid-turn is queued by Claude Code.
function syncSendBtn() {
  const btn = $("send-btn");
  const empty = !$("input").value.trim() && !state.attachments.length;
  const stop = empty && state.current != null && turnRunning(currentSession());
  if (btn.classList.contains("stop") !== stop) {
    btn.classList.toggle("stop", stop);
    btn.innerHTML = stop ? SEND_ICON.stop : SEND_ICON.send;
    btn.setAttribute("aria-label", stop ? "Остановить" : "Отправить");
    if (stop) btn.dataset.tip = "Остановить — как Esc в Claude Code"; else delete btn.dataset.tip;
  }
  btn.disabled = empty && !stop;
}
$("send-btn").addEventListener("click", (ev) => {
  if (!$("send-btn").classList.contains("stop")) return;
  ev.preventDefault();
  sendKey("Escape");
  toast("Останавливаю…");
});

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

const draftKey = () => `kara-draft-${state.current ?? "new"}`;
function loadDraft() {
  $("input").value = store.get(draftKey()) || "";
  autosize();
}

function clearComposer() {
  $("input").value = "";
  store.set(draftKey(), "");
  state.attachments = [];
  renderAttachments();
}

async function sendTo(tid, message, node) {
  const s = sessionById(tid);
  const p = node || pendingNode(message);
  if (!node) {
    $("thread").appendChild(p);
    refreshMsgStatus();
    toBottom();
    flyIn(p);
  }
  p.classList.remove("failed");
  p.querySelector(".retry")?.remove();
  p.querySelector(".tick").innerHTML = TICK.clock;
  state.pending.set(tid, { text: message, at: Date.now() });
  startInflight(tid); // the clock starts on the click, like Claude Code's own
  renderHeader();
  if (s && s.state === "dead") $("working-text").textContent = "Бужу сессию…";
  try {
    await api(`/api/sessions/${tid}/send`, { body: { text: message } });
    p.querySelector(".tick").innerHTML = TICK.sent;
    p.dataset.sent = "1";
    refreshMsgStatus();
    if (message.startsWith("/")) setTimeout(() => revealDialog(tid), 1500);
  } catch (e) {
    state.pending.delete(tid);
    if (state.inflight?.tid === tid) { state.inflight = null; state.turnClock = null; renderHeader(); }
    p.classList.add("failed");
    p.querySelector(".tick").textContent = "!";
    const retry = el("button", "retry", "Не отправлено · Повторить");
    retry.type = "button";
    retry.onclick = () => sendTo(tid, message, p);
    p.appendChild(retry);
    toast(`❌ ${e.message}`);
  } finally {
    $("working-text").textContent = workVerb();
  }
}

async function send() {
  if (state.attachments.some((a) => a.uploading)) { toast("Файлы ещё загружаются…"); return; }
  const message = composeMessage();
  if (!message) return;
  // Claude Code's /usage opens a dialog in the terminal that the web never
  // sees: answer it here, from the same source, without bothering the session.
  if (/^\/usage\s*$/i.test(message)) { clearComposer(); autosize(); showUsage(); return; }
  $("send-btn").disabled = true;
  try {
    if (state.current == null) {
      $("working").hidden = false;
      $("working-text").textContent = state.telegram ? "Создаю чат и топик…" : "Создаю чат…";
      const c = currentChoice();
      const effort = c?.provider.id === "claude" && state.effort ? state.effort : undefined;
      const res = await api("/api/sessions", { body: { text: message, web_only: !state.telegram, provider: c?.provider.id, model: c?.model?.id || undefined, effort, cwd: state.cwd || undefined, worktree: state.worktree || undefined } });
      if (state.worktree) { state.worktree = false; renderCwd(); }
      clearComposer();
      state.pending.set(res.thread_id, { text: message, at: Date.now() });
      startInflight(res.thread_id);
      await refreshSessions();
      location.hash = `#${res.thread_id}`;
      return;
    }
    clearComposer();
    sendTo(state.current, message); // optimistic: the composer frees up at once
    return;
  } catch (e) {
    toast(`❌ ${e.message}`);
  } finally {
    $("working-text").textContent = workVerb();
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
  renderEffort();
}

// Effort is its own chip next to the model (claude only; others ignore it).
function renderEffort() {
  const isClaude = currentChoice()?.provider.id === "claude";
  $("effort-btn").hidden = !isClaude || state.current != null;
  const e = EFFORTS.find(([id]) => id === state.effort) || EFFORTS[0];
  $("effort-name").innerHTML = e[0] ? `<span class="lbl-long">Усилие: </span>${esc(e[1].toLowerCase())}` : '<span class="lbl-long">Усилие</span>';
  $("effort-btn").classList.toggle("set", Boolean(e[0]));
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
  if (s && (!webOn(s) || state.webMuted)) return;
  if (document.hidden && state.pushOn) return; // the server's Web Push shows it
  if (!document.hidden && e.thread_id === state.current) return;
  if (e.role !== "assistant" || e.content_type !== "text" || !e.is_complete) return;
  const title = s ? s.name : "Кара";
  const opts = { body: String(e.text).slice(0, 180), tag: `t${e.thread_id}`, icon: "/static/icon.svg", data: { url: `/#${e.thread_id}` } };
  navigator.serviceWorker?.ready
    .then((reg) => reg.showNotification(title, opts))
    .catch(() => {
      const n = new Notification(title, opts);
      n.onclick = () => { window.focus(); location.hash = `#${e.thread_id}`; n.close(); };
    });
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
      else if (e.role === "assistant" && e.content_type === "text" && e.is_complete) { state.unread.add(e.thread_id); renderSessions(); if (state.current == null) setTitle("Кара"); }
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
  items.push({ heading: "Telegram" });
  items.push({
    label: "Новые чаты в Telegram",
    sub: state.telegram ? "Каждый новый чат получает свой топик" : "Режим «только веб»: топики не создаются",
    icon: SVG.tg,
    check: state.telegram,
    action: () => $("tg-toggle").click(),
  });
  items.push({ heading: "Уведомления" });
  items.push({
    label: "Когда уведомлять",
    sub: state.notifyMode === "done" ? "Только последнее сообщение — когда задача готова (и если нужно разрешение)" : "На каждое сообщение",
    icon: state.notifyMode === "done" ? SVG.check2 : SVG.bell,
    action: () => setNotifyMode(state.notifyMode === "done" ? "each" : "done"),
  });
  items.push({
    label: "Звук в Telegram",
    sub: state.tgMuted ? "Все топики без звука · настройки чатов сохранены" : "Звук по настройкам каждого чата",
    icon: state.tgMuted ? SVG.bellOff : SVG.tg,
    check: !state.tgMuted,
    action: () => setTgMuted(!state.tgMuted),
  });
  items.push({
    label: "Веб-уведомления",
    sub: state.webMuted ? "Выключены на всех устройствах · настройки чатов сохранены" : "Включены · по настройкам каждого чата",
    icon: state.webMuted ? SVG.bellOff : SVG.bell,
    check: !state.webMuted,
    action: () => setWebMuted(!state.webMuted),
  });
  if (pushSupported()) {
    items.push(state.pushOn
      ? { label: "Push на этом устройстве", sub: "Включён · нажми, чтобы выключить", icon: SVG.bell, check: true, action: disablePush }
      : { label: "Push на этом устройстве", sub: isStandalone() || !isIOS() ? "Выключен · включи, чтобы получать ответы при закрытом приложении" : "На iPhone — после «На экран Домой»", icon: SVG.bellOff, action: enablePush });
  }
  items.push("sep", { label: "Безопасность", sub: "Пароль, ключи доступа, последние входы", icon: SVG.shield, action: openSecurity });
  items.push({ label: "Выйти", icon: SVG.out, action: async () => { await api("/api/logout", { body: {} }).catch(() => {}); showLogin(); } });
  return items;
}

async function setWebMuted(on) {
  try {
    state.webMuted = (await api("/api/settings", { body: { web_muted: on } })).web_muted;
    toast(on ? "Веб-уведомления выключены везде — выключенные чаты так и останутся выключенными" : "Веб-уведомления снова приходят — по настройкам каждого чата");
    const s = currentSession();
    if (s) renderSoundBtn(s);
  } catch (e) { toast(e.message); }
}

async function setTgMuted(on) {
  try {
    state.tgMuted = (await api("/api/settings", { body: { tg_muted: on } })).tg_muted;
    toast(on ? "Telegram замолчал целиком — заглушённые чаты так и останутся заглушёнными" : "Telegram снова звенит — по настройкам каждого чата");
    const s = currentSession();
    if (s) renderSoundBtn(s);
  } catch (e) { toast(e.message); }
}

// ═══ web push ════════════════════════════════════════════════════════════════
const isIOS = () => /iP(hone|ad|od)/.test(navigator.userAgent);
const isStandalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const b64ToBytes = (b64) => {
  const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
};

async function refreshPushState() {
  if (!pushSupported()) return;
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    state.pushOn = Boolean(sub) && Notification.permission === "granted";
    // Re-register: keeps the server list right after a key/endpoint change.
    if (state.pushOn) await api("/api/push/subscribe", { body: { subscription: sub.toJSON() } });
  } catch (_) { state.pushOn = false; }
}

async function enablePush() {
  // Ask first, synchronously inside the tap: iOS refuses permission prompts
  // that are not a direct result of a user gesture.
  const perm = await Notification.requestPermission();
  if (perm !== "granted") { toast(isIOS() && !isStandalone() ? "На iPhone уведомления работают в приложении с экрана Домой" : "Уведомления запрещены в настройках браузера"); return; }
  try {
    const { key } = await api("/api/push/key");
    const reg = await navigator.serviceWorker.ready;
    const sub = (await reg.pushManager.getSubscription()) || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
    await api("/api/push/subscribe", { body: { subscription: sub.toJSON() } });
    state.pushOn = true;
    toast("Уведомления включены");
  } catch (e) { toast(`Не получилось: ${e.message}`); }
}

async function disablePush() {
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      await api("/api/push/unsubscribe", { body: { endpoint: sub.endpoint } }).catch(() => {});
      await sub.unsubscribe();
    }
  } catch (_) { /* already gone */ }
  state.pushOn = false;
  toast("Уведомления выключены на этом устройстве");
}

// While a Kara tab is on screen the server holds pushes back (the page shows
// its own notification); it resumes 45 s after the last "visible" ping.
const clientId = Math.random().toString(36).slice(2);
function reportPresence() {
  api("/api/presence", { body: { client: clientId, visible: !document.hidden } }).catch(() => {});
}
document.addEventListener("visibilitychange", reportPresence);
setInterval(() => { if (!document.hidden) reportPresence(); }, 25000);

// ═══ auth / boot ════════════════════════════════════════════════════════════
function showLogin() {
  state.es?.close();
  $("app").hidden = true;
  $("login").hidden = false;
  showLoginMethods();
}

// ═══ login: passkey (Face ID / Touch ID / Windows Hello), Telegram, password ═
const b64u = {
  enc: (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""),
  dec: (text) => Uint8Array.from(atob(text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4)), (ch) => ch.charCodeAt(0)),
};
const hasWebAuthn = () => Boolean(window.isSecureContext && window.PublicKeyCredential && navigator.credentials);
const keyWord = () => {
  const ua = navigator.userAgent;
  return /iPhone|iPad/.test(ua) ? "Face ID" : /Macintosh/.test(ua) ? "Touch ID" : /Windows/.test(ua) ? "Windows Hello" : /Android/.test(ua) ? "отпечатку" : "ключу доступа";
};
// The server speaks WebAuthn JSON (base64url); the browser wants ArrayBuffers.
function credJSON(c) {
  const r = c.response;
  const out = { id: c.id, rawId: b64u.enc(c.rawId), type: c.type, clientExtensionResults: c.getClientExtensionResults?.() || {}, response: { clientDataJSON: b64u.enc(r.clientDataJSON) } };
  if (c.authenticatorAttachment) out.authenticatorAttachment = c.authenticatorAttachment;
  if (r.attestationObject) Object.assign(out.response, { attestationObject: b64u.enc(r.attestationObject), transports: r.getTransports?.() || [] });
  if (r.authenticatorData) Object.assign(out.response, { authenticatorData: b64u.enc(r.authenticatorData), signature: b64u.enc(r.signature), ...(r.userHandle ? { userHandle: b64u.enc(r.userHandle) } : {}) });
  return out;
}
const loginError = (e) => (e.message === "too many attempts" ? "Слишком много попыток — подожди 15 минут"
  : e.message === "wrong password" ? "Неверный пароль"
  : /key|ключ/i.test(e.message) ? "Ключ не подошёл — попробуй ещё раз или войди через Telegram" : e.message);

async function showLoginMethods() {
  let m = { password: true, passkey: false, telegram: false };
  try { m = await api("/api/auth/methods"); } catch (_) { /* offline: show the password */ }
  const key = m.passkey && hasWebAuthn();
  $("passkey-login").hidden = !key;
  $("passkey-label").textContent = `Войти по ${keyWord()}`;
  $("tg-login").hidden = !m.telegram;
  $("login-pass").hidden = !m.password;
  $("password").required = m.password;
  $("login-or").hidden = !(m.password && (key || m.telegram));
  $("pass-submit").classList.toggle("primary", !key);
  if (!key && m.password) $("password").focus();
}

async function afterLogin(viaKey) {
  state.offerPasskey = !viaKey;
  boot(await api("/api/me").catch(() => null));
}

$("passkey-login").onclick = async () => {
  $("login-error").textContent = "";
  try {
    const begin = await api("/api/auth/passkey/login/options", { body: {} });
    const o = begin.options;
    const cred = await navigator.credentials.get({ publicKey: { ...o, challenge: b64u.dec(o.challenge), allowCredentials: (o.allowCredentials || []).map((c) => ({ ...c, id: b64u.dec(c.id) })) } });
    await api("/api/auth/passkey/login", { body: { token: begin.token, credential: credJSON(cred) } });
    store.set("kara-passkey", "1");
    afterLogin(true);
  } catch (e) {
    if (e.name !== "NotAllowedError" && e.name !== "AbortError") $("login-error").textContent = loginError(e);
  }
};

$("tg-login").onclick = async () => {
  $("login-error").textContent = "";
  let req;
  try { req = await api("/api/auth/telegram", { body: {} }); } catch (e) {
    $("login-error").textContent = e.message === "too many requests" ? "Запросы слишком часто — подожди минуту" : e.message;
    return;
  }
  const wait = $("tg-wait");
  wait.hidden = false;
  wait.innerHTML = `<div class="tg-code">${esc(req.code)}</div><div>В группе «Кара» в Telegram пришёл запрос. Проверь, что там тот же код, и нажми «Впустить».</div><button type="button" class="link-btn" id="tg-cancel">Отмена</button>`;
  state.tgLogin = req.id;
  $("tg-cancel").onclick = () => { state.tgLogin = null; wait.hidden = true; };
  while (state.tgLogin === req.id) {
    await new Promise((r) => setTimeout(r, 2000));
    if (state.tgLogin !== req.id) return;
    let st;
    try { st = (await api(`/api/auth/telegram/${req.id}`)).state; } catch (_) { continue; }
    if (st === "pending") continue;
    state.tgLogin = null;
    wait.hidden = true;
    if (st === "approved") { afterLogin(false); return; }
    $("login-error").textContent = st === "denied" ? "Вход отклонён в Telegram" : "Запрос устарел — попробуй ещё раз";
  }
};

async function addPasskey() {
  const begin = await api("/api/auth/passkeys/options", { body: {} });
  const o = begin.options;
  const cred = await navigator.credentials.create({ publicKey: { ...o, challenge: b64u.dec(o.challenge), user: { ...o.user, id: b64u.dec(o.user.id) }, excludeCredentials: (o.excludeCredentials || []).map((c) => ({ ...c, id: b64u.dec(c.id) })) } });
  const r = await api("/api/auth/passkeys", { body: { token: begin.token, credential: credJSON(cred) } });
  store.set("kara-passkey", "1");
  return r;
}

// After a password / Telegram login on a device that could use a key.
async function offerPasskey() {
  if (!state.offerPasskey || !hasWebAuthn() || store.get("kara-passkey") === "1") return;
  state.offerPasskey = false;
  if (!(await confirmDialog(`Входить по ${keyWord()}?`, "Добавлю ключ доступа для этого устройства — дальше вход в одно касание, без пароля.", "Добавить", false))) return;
  try { await addPasskey(); toast(`Готово — теперь вход по ${keyWord()}`); } catch (e) { if (e.name !== "NotAllowedError") toast(e.message); }
}

const LOGIN_HOW = { password: "пароль", passkey: "ключ доступа", telegram: "Telegram" };
function whenRu(ts) {
  const d = new Date(ts * 1000);
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 864e5);
  const day = days === 0 ? "сегодня" : days === 1 ? "вчера" : d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  return `${day}, ${hhmm(d.getTime())}`;
}

async function openSecurity() {
  const dlg = $("security");
  let sec;
  try { sec = await api("/api/security"); } catch (e) { toast(e.message); return; }
  state.passProof = null;
  const renderKeys = (keys) => {
    sec.keys = keys;
    $("keys-list").innerHTML = keys.length ? keys.map((k) => `<li><span class="key-ico">${SVG.key}</span><span class="key-main"><b>${esc(k.name)}</b><span class="muted">добавлен ${whenRu(k.created)}${k.last_used ? ` · вход ${whenRu(k.last_used)}` : ""}${k.synced ? " · синхронизируется между твоими устройствами" : ""}</span></span><button type="button" class="icon-btn small" data-del="${esc(k.id)}" data-tip="Удалить ключ">${SVG.trash}</button></li>`).join("") : '<li class="muted">Ключей пока нет</li>';
    $("keys-list").querySelectorAll("[data-del]").forEach((b) => {
      b.onclick = async () => {
        if (!(await confirmDialog("Удалить ключ?", "С этого ключа войти больше не получится.", "Удалить"))) return;
        try { renderKeys((await api(`/api/auth/passkeys/${b.dataset.del}/delete`, { body: {} })).keys); } catch (e) { toast(e.message); }
      };
    });
    const proof = $("sec-proof-key");
    proof.hidden = !keys.length || !hasWebAuthn();
    proof.textContent = `Не помню пароль — подтвердить по ${keyWord()}`;
  };
  const renderInfo = () => {
    $("sec-pass-state").textContent = sec.password_set_here
      ? `Твой пароль, сменён ${whenRu(sec.password_changed)}. Хранится только его хеш.`
      : "Сейчас действует пароль из настроек сервера. Задай свой — он заменит его.";
    $("sec-tg").textContent = sec.telegram
      ? "Включён: на новом устройстве нажми «Войти через Telegram» — в группе «Кара» придёт запрос с кодом, впускаешь кнопкой."
      : "Недоступен: у бота не настроена группа.";
    $("sec-logins").innerHTML = sec.logins.length
      ? sec.logins.map((e) => `<li><span>${esc(whenRu(e.ts))}</span><span>${esc(LOGIN_HOW[e.method] || e.method)}</span><span class="muted">${esc(e.device || "")} · ${esc(e.ip || "")}</span></li>`).join("")
      : '<li class="muted">Записей пока нет — журнал ведётся с сегодняшнего дня</li>';
    $("sec-failed").textContent = sec.failed_24h ? `Неудачных попыток входа за сутки: ${sec.failed_24h}` : "Неудачных попыток за сутки не было";
  };
  renderKeys(sec.keys);
  renderInfo();
  $("sec-pass-form").hidden = true;
  $("sec-pass-open").hidden = false;
  ["sec-current", "sec-new", "sec-new2"].forEach((id) => { $(id).value = ""; });
  $("sec-current").hidden = false;
  $("sec-pass-open").onclick = () => { $("sec-pass-form").hidden = false; $("sec-pass-open").hidden = true; $("sec-current").focus(); };
  $("sec-proof-key").onclick = async () => {
    try {
      const begin = await api("/api/auth/passkey/login/options", { body: {} });
      const o = begin.options;
      const cred = await navigator.credentials.get({ publicKey: { ...o, challenge: b64u.dec(o.challenge), allowCredentials: [] } });
      state.passProof = { token: begin.token, credential: credJSON(cred) };
      $("sec-current").hidden = true;
      $("sec-proof-key").textContent = `✓ Подтверждено по ${keyWord()}`;
      $("sec-new").focus();
    } catch (e) { if (e.name !== "NotAllowedError") toast(e.message); }
  };
  $("sec-pass-save").onclick = async () => {
    const next = $("sec-new").value;
    if (next.length < 8) { toast("Пароль — от 8 символов"); return; }
    if (next !== $("sec-new2").value) { toast("Пароли не совпадают"); return; }
    const body = state.passProof ? { passkey: state.passProof, new: next } : { current: $("sec-current").value, new: next };
    try {
      sec = { ...sec, ...(await api("/api/security/password", { body })) };
      state.passProof = null;
      renderInfo();
      $("sec-pass-form").hidden = true;
      $("sec-pass-open").hidden = false;
      toast("Пароль сменён — остальные устройства вышли");
    } catch (e) { toast(e.message === "wrong password" ? "Текущий пароль не тот" : loginError(e)); }
  };
  $("keys-add").hidden = !hasWebAuthn();
  $("keys-add").onclick = async () => {
    try { renderKeys((await addPasskey()).keys); toast("Ключ добавлен"); } catch (e) { if (e.name === "InvalidStateError") toast("На этом устройстве ключ уже есть"); else if (e.name !== "NotAllowedError") toast(e.message); }
  };
  $("sec-signout").onclick = async () => {
    if (!(await confirmDialog("Выйти на других устройствах?", "Все остальные браузеры и приложения выйдут. Это устройство останется в системе.", "Выйти везде"))) return;
    try { await api("/api/security/sign-out-others", { body: {} }); toast("Готово — остальные устройства вышли"); } catch (e) { toast(e.message); }
  };
  dlg.showModal();
}
// Enter in a field must not close the dialog (a form with method="dialog").
$("security").querySelector("form").addEventListener("submit", (ev) => {
  if (!ev.submitter || ev.submitter.value !== "close") ev.preventDefault();
  if (!ev.submitter && !$("sec-pass-form").hidden) $("sec-pass-save").click();
});

async function boot(me) {
  if (me && "hub" in me) store.set("kara-hub", me.hub ? "1" : "0");
  if (me && "tts" in me) state.tts = me.tts;
  $("login").hidden = true;
  $("app").hidden = false;
  if (store.get("kara-collapsed") === "1" && !isMobile()) $("app").classList.add("collapsed");
  await refreshSessions();
  connect();
  route();
  offerPasskey();
  reportPresence();
  refreshPushState();
  api("/api/settings").then((r) => {
    state.tgMuted = r.tg_muted;
    state.notifyMode = r.notify_mode || "done";
    state.webMuted = Boolean(r.web_muted);
    state.webOff = new Set(r.web_off || []);
    state.stt = Boolean(r.stt);
    $("mic-btn").hidden = !micSupported();
    if (typeof r.new_chat_telegram === "boolean") { state.telegram = r.new_chat_telegram; store.set("kara-telegram", state.telegram ? "1" : "0"); renderToggle(); }
    const s = currentSession();
    if (s) renderSoundBtn(s);
  }).catch(() => {});
}

$("login-form").onsubmit = async (ev) => {
  ev.preventDefault();
  $("login-error").textContent = "";
  try {
    await api("/api/login", { body: { password: $("password").value } });
    $("password").value = "";
    afterLogin(false);
  } catch (e) {
    $("login-error").textContent = loginError(e);
  }
};
window.addEventListener("hashchange", route);
$("composer").onsubmit = (ev) => { ev.preventDefault(); send(); };
$("input").addEventListener("input", () => { autosize(); store.set(draftKey(), $("input").value); });
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

$("filter").addEventListener("input", (ev) => {
  state.filter = ev.target.value;
  renderSessions();
  clearTimeout(state.searchTimer);
  state.searchTimer = setTimeout(searchContent, 350);
});

// Full-text hits below the name matches ("В переписке").
async function searchContent() {
  const q = state.filter.trim();
  if (q.length < 3) return;
  let data;
  try { data = await api(`/api/search?q=${encodeURIComponent(q)}`); } catch (_) { return; }
  if (state.filter.trim() !== q) return;
  state.hits = { q, results: data.results };
  renderSessions();
}

function hitsBox(q, results) {
  const box = el("div", "search-hits");
  box.appendChild(el("div", "group-label", "В переписке"));
  const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "ig");
  const seen = new Set();  // sessions sharing one transcript repeat the same hit
  for (const r of results) {
    for (const h of r.hits.slice(0, 2)) {
      if (seen.has(h.ts + h.snippet)) continue;
      seen.add(h.ts + h.snippet);
      const a = el("a", "hit");
      a.href = `#${r.thread_id}`;
      a.innerHTML = `<span class="hit-name">${esc(r.name)}</span><span class="hit-snip">${esc(h.snippet).replace(re, (m) => `<mark>${m}</mark>`)}</span>`;
      box.appendChild(a);
    }
  }
  return box;
}

function openSwitcher() {
  const dlg = $("switcher");
  const input = $("switcher-input");
  let sel = 0;
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    const list = state.sessions.filter((s) => !q || s.name.toLowerCase().includes(q)).slice(0, 12);
    sel = Math.min(sel, Math.max(0, list.length - 1));
    const box = $("switcher-list");
    box.innerHTML = "";
    list.forEach((s, i) => {
      const a = el("a", `sw-item${i === sel ? " sel" : ""}`, `<span class="dot ${esc(s.state)}"></span><span>${esc(s.name)}</span><span class="muted">${esc(groupOf(s.last_activity))}</span>`);
      a.href = `#${s.thread_id}`;
      a.onclick = () => dlg.close();
      box.appendChild(a);
    });
    if (!list.length) box.appendChild(el("div", "sw-empty muted", "Ничего не нашлось"));
    return list;
  };
  input.value = "";
  let list = draw();
  input.oninput = () => { sel = 0; list = draw(); };
  input.onkeydown = (ev) => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); sel = Math.min(sel + 1, list.length - 1); list = draw(); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); sel = Math.max(sel - 1, 0); list = draw(); }
    else if (ev.key === "Enter" && list[sel]) { ev.preventDefault(); location.hash = `#${list[sel].thread_id}`; dlg.close(); }
  };
  dlg.showModal();
  input.focus();
}
$("switcher").addEventListener("click", (ev) => { if (ev.target === $("switcher")) $("switcher").close(); });
$("collapse-btn").onclick = () => { $("app").classList.add("collapsed"); store.set("kara-collapsed", "1"); };
$("menu-btn").onclick = () => {
  if (isMobile()) $("app").classList.add("drawer");
  else { $("app").classList.remove("collapsed"); store.set("kara-collapsed", ""); }
};
$("scrim").onclick = () => $("app").classList.remove("drawer");

// Desktop: drag the sidebar edge to resize; the width is remembered.
(() => {
  const app = $("app");
  const apply = (w) => app.style.setProperty("--side-w", `${Math.max(200, Math.min(520, w))}px`);
  const saved = parseInt(store.get("kara-side-w") || "", 10);
  if (saved) apply(saved);
  const handle = $("side-resizer");
  handle.addEventListener("pointerdown", (ev) => {
    if (isMobile() || app.classList.contains("collapsed")) return;
    ev.preventDefault();
    handle.setPointerCapture(ev.pointerId);
    app.classList.add("resizing");
    const move = (e) => apply(e.clientX);
    const up = (e) => {
      handle.releasePointerCapture(e.pointerId);
      handle.removeEventListener("pointermove", move);
      app.classList.remove("resizing");
      store.set("kara-side-w", String(parseInt(getComputedStyle(app).getPropertyValue("--side-w"), 10) || 272));
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up, { once: true });
  });
  handle.addEventListener("dblclick", () => { apply(272); store.set("kara-side-w", "272"); });
})();
// Phone: swipe right from the left edge opens the chat list, swipe left closes it.
(() => {
  let x0 = null;
  let y0 = 0;
  document.addEventListener("touchstart", (ev) => {
    const t = ev.touches[0];
    const open = $("app").classList.contains("drawer");
    x0 = isMobile() && (open || t.clientX < 28) ? t.clientX : null;
    y0 = t.clientY;
  }, { passive: true });
  document.addEventListener("touchend", (ev) => {
    if (x0 == null) return;
    const t = ev.changedTouches[0];
    const dx = t.clientX - x0;
    if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(t.clientY - y0) * 1.5) $("app").classList.toggle("drawer", dx > 0);
    x0 = null;
  }, { passive: true });
})();
$("session-list").addEventListener("click", (ev) => { if (ev.target.closest(".s-item") && isMobile()) $("app").classList.remove("drawer"); });
$("new-btn").onclick = () => { if (isMobile()) $("app").classList.remove("drawer"); };
$("chat-meta").onclick = () => {
  const s = currentSession();
  if (s && s.provider === "claude") openMenu(effortMenu(s), menuBelow($("chat-meta")), $("chat-meta"));
};
$("sound-btn").onclick = () => {
  const s = currentSession();
  if (!s) return;
  const r = $("sound-btn").getBoundingClientRect();
  openMenu(soundMenu(s), { x: r.right - 260, y: r.bottom + 4, top: r.top }, $("sound-btn"));
};
$("pane-btn").onclick = () => { $("pane").hidden = !$("pane").hidden; pollPane(); };
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
$("tg-toggle").onclick = () => {
  state.telegram = !state.telegram;
  store.set("kara-telegram", state.telegram ? "1" : "0");
  renderToggle();
  // The server keeps the choice for every device and the home-screen app.
  api("/api/settings", { body: { new_chat_telegram: state.telegram } }).catch(() => toast("Не сохранилось на сервере"));
};
$("effort-btn").onclick = () => {
  openMenu([{ heading: "Усилие для нового чата" }, ...EFFORTS.map(([id, label, sub]) => ({
    label, sub, check: state.effort === id,
    action: () => { state.effort = id; store.set("kara-effort", id); renderEffort(); },
  }))], menuBelow($("effort-btn")));
};
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
  if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && ev.code === "KeyO") { ev.preventDefault(); location.hash = "#new"; }
  if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && !ev.altKey && ev.code === "KeyP") { ev.preventDefault(); openSwitcher(); }
});

// ═══ deleted-topic probe ═════════════════════════════════════════════════════
// Telegram never tells bots a topic was deleted; opening the chat checks it.
async function probeTopic(tid) {
  const s = sessionById(tid);
  if (!s || s.web_only || !s.chat_id) return;
  try {
    const r = await api(`/api/sessions/${tid}/probe`, { body: {} });
    if (r.exists === false) { toast("Топик этого чата удалён в Telegram"); await refreshSessions(); }
  } catch (_) { /* best effort */ }
}

// ═══ live status: what the agent is doing right now ═════════════════════════
// Is a turn running? The session list lags (Telegram topics repaint on a
// debounce, short turns never show "active"), so also trust the spinner on
// the pane and a message just sent from here until its turn is seen to end.
const INFLIGHT_MS = 120000;
function turnRunning(s) {
  if (!s) return false;
  if (s.state === "active") return true;
  const l = state.lastLive;
  if (l && l.tid === s.thread_id && l.live && l.live.status) return true;
  const f = state.inflight;
  return Boolean(f && f.tid === s.thread_id && Date.now() - f.at < INFLIGHT_MS);
}
// Like Claude Code's spinner word: one per turn, picked at random.
const WORK_VERBS = ["Думает", "Соображает", "Колдует", "Прикидывает", "Вникает", "Разбирается", "Мозгует", "Кумекает", "Шуршит", "Корпит", "Размышляет", "Смекает", "Распутывает", "Химичит", "Мудрит", "Ворожит", "Шаманит", "Докапывается", "Нащупывает", "Сопоставляет"];
function workVerb(turnStart) {
  const w = state.workVerb;
  const fresh = !w || w.tid !== state.current || (turnStart && w.start && w.start !== turnStart);
  if (fresh) state.workVerb = { tid: state.current, start: turnStart || "", text: `${WORK_VERBS[Math.floor(Math.random() * WORK_VERBS.length)]}…` };
  else if (turnStart && !w.start) w.start = turnStart;
  return state.workVerb.text;
}
function startInflight(tid) {
  state.workVerb = null;
  state.inflight = { tid, at: Date.now(), seen: false };
  state.turnClock = { tid, start: Date.now(), out: 0 };
}
const STATUS_RU = [[/^(reading|read)\b/i, "Читает"], [/^(writing|write)\b/i, "Пишет"], [/^(editing|edit)\b/i, "Правит"], [/^(searching|search|grep)/i, "Ищет"], [/^(running|bash|execut)/i, "Выполняет"], [/^(thinking|reason)/i, "Думает"], [/^(testing|test)/i, "Тестирует"], [/^fetch/i, "Загружает"]];
// "still thinking with xhigh effort" → "всё ещё думает · усилие xhigh"
function thinkRu(part) {
  const m = part.match(/^(still )?thinking( more)?(?: with (\w+) effort)?$/i);
  return m && `${m[1] ? "всё ещё думает" : m[2] ? "думает дальше" : "думает"}${m[3] ? ` · усилие ${m[3]}` : ""}`;
}
// "Pondering… (1m 3s · ↓ 2.1k tokens · thinking)" → label + Claude Code's own turn clock and token count.
function parseStatus(raw) {
  let text = String(raw || "").trim();
  let secs = null, tokens = null;
  const paren = text.match(/\s*\(([^)]*)\)\s*$/);
  if (paren) {
    const rest = [];
    for (const part of paren[1].split("·").map((p) => p.trim()).filter(Boolean)) {
      const t = part.match(/^(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?$/);
      const k = part.match(/^[↑↓]\s*([\d.,]+)\s*([kKmM]?)\s*tokens?$/);
      if (t && (t[1] || t[2] || t[3])) secs = (+t[1] || 0) * 3600 + (+t[2] || 0) * 60 + (+t[3] || 0);
      else if (k) tokens = parseFloat(k[1].replace(",", ".")) * ({ k: 1e3, m: 1e6 }[k[2].toLowerCase()] || 1);
      else if (!/esc to interrupt|ctrl\+/i.test(part)) rest.push(thinkRu(part) || part);
    }
    text = text.slice(0, paren.index) + (rest.length ? ` · ${rest.join(" · ")}` : "");
  }
  for (const [re, ru] of STATUS_RU) if (re.test(text)) { text = `${ru}: ${text}`; break; }
  return { text, secs, tokens };
}

// The working line's clock ticks locally between polls.
function turnClock(live, status) {
  const prev = state.turnClock && state.turnClock.tid === state.current ? state.turnClock : null;
  const turn = live.turn || {};
  const skew = turn.now ? Date.now() - turn.now * 1000 : 0;
  let start = turn.start ? Date.parse(turn.start) + skew : NaN;
  if (Number.isNaN(start)) start = status.secs != null ? Date.now() - status.secs * 1000 : prev ? prev.start : Date.now();
  if (prev && prev.start < start) start = prev.start; // never run backwards
  state.turnClock = { tid: state.current, start, out: Math.max(turn.out || 0, status.tokens || 0) };
  tickWork();
}
function tickWork() {
  const meta = $("working-meta");
  const c = state.turnClock;
  if (!c || c.tid !== state.current || $("working").hidden) { meta.hidden = true; return; }
  const sec = Math.max(0, Math.round((Date.now() - c.start) / 1000));
  meta.hidden = false;
  meta.textContent = clock(sec) + (c.out ? ` · ${tokRu(c.out)} ток.` : "");
  meta.dataset.tip = `Ход идёт ${secsRu(sec)}` + (c.out ? `; написано ${Math.round(c.out).toLocaleString("ru-RU")} токенов` : "");
}
setInterval(tickWork, 1000);

function renderLive(live, s) {
  const box = $("live");
  const f = state.inflight;
  if (f && s && f.tid === s.thread_id && live) {
    if (live.status) f.seen = true;
    else if (f.seen) state.inflight = null; // the spinner came and went: turn over
  }
  const working = turnRunning(s);
  if ($("working").hidden === working) renderHeader();
  if (!working) state.turnClock = null;
  const idleText = s && s.state === "dead" ? "Бужу сессию…" : workVerb(live?.turn?.start);
  if (!live || !working) { box.hidden = true; if (working) $("working-text").textContent = idleText; tickWork(); refreshMsgStatus(); return; }
  const status = parseStatus(live.status);
  // Claude Code's own whimsical word ("Combobulating…") reads as ours, in Russian.
  const text = status.text.replace(/^[A-Za-z'’-]+…/, idleText);
  $("working-text").textContent = text || live.wait || idleText;
  turnClock(live, status);
  const agents = live.subagents || [];
  $("live-agents").innerHTML = agents.length
    ? `<span class="la-ico">${ICONS.bot}</span><span>${agents.length === 1 ? "Субагент" : `Субагентов: ${agents.length}`} — ${agents.map((a) => `<b>${esc(a)}</b>`).join(", ")}</span>`
    : "";
  const items = (live.todos && live.todos.length)
    ? live.todos.map((t) => ({ text: t.text, mark: t.status === "completed" ? "done" : t.status === "in_progress" ? "doing" : "todo" }))
    : (live.progress || []).map((line) => {
      const m = line.match(/^(?:⎿\s*)?([✔◼◻◔])\s+(.*)$/);
      return m ? { text: m[2], mark: m[1] === "✔" ? "done" : m[1] === "◻" ? "todo" : "doing" } : { text: line, mark: "" };
    });
  const list = $("live-list");
  list.innerHTML = items.slice(0, 12).map((it) => `<li class="${it.mark}"><span class="lm"></span><span>${esc(it.text)}</span></li>`).join("");
  box.hidden = !agents.length && !items.length;
  refreshMsgStatus();
}

// ═══ Claude Code mode (Default / Accept edits / Plan / Bypass) ══════════════
// Labels from ccgram's scrape of the Claude Code status bar (none = default).
const MODE_RU = {
  Def: ["Обычный", "спрашивает разрешения на действия"],
  Edit: ["Правки", "меняет файлы без подтверждения"],
  Plan: ["План", "сначала составляет план, потом действует"],
  Auto: ["Авто", "сама решает, о чём спрашивать"],
  YOLO: ["Без вопросов", "ничего не спрашивает (yolo)"],
};
const MODE_ICON = {
  Def: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4" stroke-linecap="round"/></svg>',
  Edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M14 6l4 4"/></svg>',
  Plan: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 3.5h6v3H9zM9 11h6M9 15h4"/></svg>',
  Auto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M11 3l1.8 4.2L17 9l-4.2 1.8L11 15l-1.8-4.2L5 9l4.2-1.8z"/><path d="M18.5 14l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/></svg>',
  YOLO: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M13 2L4.5 13.5H11L10 22l8.5-11.5H12z"/></svg>',
};
const CHEV = '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';

function applyMode(tid, r) {
  if (tid !== state.current) return;
  if (r.available) state.modeAvail = { wid: currentSession()?.window_id, list: r.available };
  const b = $("mode-btn");
  const key = r.mode || "Def";
  const m = MODE_RU[key];
  b.hidden = !m;
  if (!m) return;
  b.innerHTML = `<span>${esc(m[0])}</span>${CHEV}`;
  b.dataset.mode = key;
  b.dataset.tip = `Режим Claude Code: ${m[1]}. Нажми, чтобы выбрать`;
}

async function refreshMode(tid) {
  state.modeAt = Date.now();
  try { applyMode(tid, await api(`/api/sessions/${tid}/mode`)); } catch (_) { /* window asleep */ }
}

function modeMenu() {
  const cur = $("mode-btn").dataset.mode || "Def";
  const s = currentSession();
  const avail = state.modeAvail && s && state.modeAvail.wid === s.window_id ? state.modeAvail.list : null;
  const items = [{ heading: "Режим Claude Code" }];
  for (const [key, [label, desc]] of Object.entries(MODE_RU)) {
    const off = Boolean(avail) && !avail.includes(key);
    items.push({ label, sub: off ? "в этой сессии нет — её запустили без этого режима" : desc, icon: MODE_ICON[key], cls: `mode-${key}`, check: key === cur, disabled: off, action: () => setMode(key) });
  }
  items.push("sep", { heading: "То же, что Shift+Tab в терминале" });
  return items;
}

async function setMode(key) {
  const tid = state.current;
  const b = $("mode-btn");
  if (tid == null || key === (b.dataset.mode || "Def")) return;
  b.classList.add("busy");
  state.modeAt = Date.now();
  try {
    const r = await api(`/api/sessions/${tid}/mode`, { body: { target: key } });
    applyMode(tid, r);
    const now = MODE_RU[r.mode || "Def"];
    toast(r.reached ? `Режим: ${now[0].toLowerCase()} — ${now[1]}` : `В этой сессии нет режима «${MODE_RU[key][0]}» — оставила «${now[0]}»`);
  } catch (e) { toast(e.message); } finally { b.classList.remove("busy"); }
}

$("mode-btn").onclick = () => {
  const b = $("mode-btn");
  if (state.current == null || b.classList.contains("busy")) return;
  if (b.classList.contains("menu-open")) { closeMenu(); return; }
  const r = b.getBoundingClientRect();
  openMenu(modeMenu(), { x: r.right - 300, y: r.bottom + 6, top: r.top }, b, "mode-menu");
};

// ═══ coloured terminal (ANSI SGR → HTML) ═════════════════════════════════════
const ANSI16 = ["#1f1e1d", "#e5534b", "#57ab5a", "#c69026", "#539bf5", "#b083f0", "#39c5cf", "#d8d5cc", "#6e7681", "#ff7b72", "#7ee787", "#e3b341", "#79c0ff", "#d2a8ff", "#56d4dd", "#ffffff"];
function ansi256(n) {
  if (n < 16) return ANSI16[n];
  if (n >= 232) { const v = 8 + (n - 232) * 10; return `rgb(${v},${v},${v})`; }
  const i = n - 16; const c = (x) => (x ? 55 + x * 40 : 0);
  return `rgb(${c(Math.floor(i / 36))},${c(Math.floor(i / 6) % 6)},${c(i % 6)})`;
}
function ansiToHtml(text) {
  let fg = null; let bg = null; let bold = false; let dim = false; let ital = false; let under = false; let inv = false;
  let out = ""; let open = false;
  const parts = String(text).split(/\x1b\[([0-9;]*)m/);
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      if (!parts[i]) continue;
      const f = inv ? (bg || "#1f1e1d") : fg; const b = inv ? (fg || "#e8e6df") : bg;
      const css = [f && `color:${f}`, b && `background:${b}`, bold && "font-weight:700", dim && "opacity:.6", ital && "font-style:italic", under && "text-decoration:underline"].filter(Boolean).join(";");
      out += css ? `<span style="${css}">${esc(parts[i].replace(/\x1b\[[0-9;?]*[A-Za-z]/g, ""))}</span>` : esc(parts[i].replace(/\x1b\[[0-9;?]*[A-Za-z]/g, ""));
      continue;
    }
    const codes = parts[i] === "" ? [0] : parts[i].split(";").map(Number);
    for (let k = 0; k < codes.length; k++) {
      const c = codes[k];
      if (c === 0) { fg = bg = null; bold = dim = ital = under = inv = false; }
      else if (c === 1) bold = true; else if (c === 2) dim = true; else if (c === 3) ital = true; else if (c === 4) under = true; else if (c === 7) inv = true;
      else if (c === 22) bold = dim = false; else if (c === 23) ital = false; else if (c === 24) under = false; else if (c === 27) inv = false;
      else if (c >= 30 && c <= 37) fg = ANSI16[c - 30]; else if (c >= 90 && c <= 97) fg = ANSI16[c - 82];
      else if (c >= 40 && c <= 47) bg = ANSI16[c - 40]; else if (c >= 100 && c <= 107) bg = ANSI16[c - 92];
      else if (c === 39) fg = null; else if (c === 49) bg = null;
      else if ((c === 38 || c === 48) && codes[k + 1] === 5) { const v = ansi256(codes[k + 2] || 0); if (c === 38) fg = v; else bg = v; k += 2; }
      else if ((c === 38 || c === 48) && codes[k + 1] === 2) { const v = `rgb(${codes[k + 2] || 0},${codes[k + 3] || 0},${codes[k + 4] || 0})`; if (c === 38) fg = v; else bg = v; k += 4; }
    }
  }
  void open;
  return out;
}

// ═══ folder for a new chat (+ git worktree) ═════════════════════════════════
state.webOff = new Set();
state.webMuted = false;
state.cwd = store.get("kara-cwd") || "";
state.worktree = false;
function renderCwd() {
  const name = state.cwd ? state.cwd.replace(/\/+$/, "").split("/").pop() : "workspace";
  $("cwd-name").textContent = state.worktree ? `${name} · worktree` : name;
  $("cwd-btn").classList.toggle("on", Boolean(state.cwd) || state.worktree);
  $("cwd-btn").dataset.tip = state.cwd ? `Новый чат запустится в ${state.cwd}${state.worktree ? " — в отдельном git worktree" : ""}` : "Новый чат запустится в рабочей папке Кары. Нажми, чтобы выбрать другую";
}
async function browseTo(path) {
  try {
    const r = await api(`/api/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`);
    const dlg = $("folder-dlg");
    dlg.dataset.path = r.path;
    $("folder-path").innerHTML = `${SVG.folder}<span>${esc(r.path.replace(/^\/home\/[^/]+/, "~"))}</span>${r.git ? '<span class="git-tag">git</span>' : ""}`;
    const list = $("folder-list");
    list.innerHTML = "";
    if (r.parent) {
      const up = el("button", "pk-item up", "<span>↑</span><span>..</span>");
      up.type = "button"; up.onclick = () => browseTo(r.parent); list.appendChild(up);
    }
    for (const d of r.dirs) {
      const b = el("button", "pk-item", `<span>${d.git ? SVG.branch : SVG.folder}</span><span>${esc(d.name)}</span>`);
      b.type = "button"; b.onclick = () => browseTo(`${r.path}/${d.name}`); list.appendChild(b);
    }
    if (!r.dirs.length) list.appendChild(el("div", "pk-empty muted", "Вложенных папок нет"));
    $("folder-wt").hidden = !r.git;
    $("folder-wt-box").checked = false;
  } catch (e) { toast(e.message); }
}
$("cwd-btn").onclick = () => { $("folder-dlg").showModal(); browseTo(state.cwd || ""); };
$("folder-pick").onclick = () => {
  state.cwd = $("folder-dlg").dataset.path || "";
  state.worktree = !$("folder-wt").hidden && $("folder-wt-box").checked;
  store.set("kara-cwd", state.cwd);
  renderCwd();
  $("folder-dlg").close();
};
$("folder-default").onclick = () => { state.cwd = ""; state.worktree = false; store.set("kara-cwd", ""); renderCwd(); $("folder-dlg").close(); };
document.querySelectorAll("[data-close]").forEach((b) => { b.onclick = () => b.closest("dialog").close(); });
document.querySelectorAll("dialog.picker").forEach((d) => d.addEventListener("click", (ev) => { if (ev.target === d) d.close(); }));

// ═══ continue an earlier Claude session ═════════════════════════════════════
const ago = (ts) => {
  const d = (Date.now() / 1000 - ts) / 60;
  return d < 60 ? `${Math.max(1, Math.round(d))} мин назад` : d < 1440 ? `${Math.round(d / 60)} ч назад` : new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
};
$("resume-btn").onclick = async () => {
  const dlg = $("resume-dlg");
  $("resume-list").innerHTML = '<div class="pk-empty muted">Ищу сессии…</div>';
  dlg.showModal();
  try {
    const r = await api(`/api/resumable${state.cwd ? `?cwd=${encodeURIComponent(state.cwd)}` : ""}`);
    $("resume-where").innerHTML = `${SVG.folder}<span>${esc(r.cwd.replace(/^\/home\/[^/]+/, "~"))}</span>`;
    const list = $("resume-list");
    list.innerHTML = "";
    for (const x of r.sessions) {
      const summary = String(x.summary || "").replace(/<[^>]+>/g, " ").replace(/`{3,}/g, "").replace(/\s+/g, " ").trim();
      const b = el("button", "pk-item two", `<span>${esc(summary || x.id.slice(0, 8))}</span><span class="muted">${esc(ago(x.mtime))}</span>`);
      b.type = "button";
      b.onclick = async () => {
        dlg.close();
        $("working").hidden = false;
        $("working-text").textContent = "Поднимаю сессию…";
        try {
          const res = await api("/api/sessions", { body: { text: "", web_only: !state.telegram, provider: "claude", resume_id: x.id, cwd: r.cwd, name: (x.summary || "").slice(0, 60) || undefined } });
          await refreshSessions();
          location.hash = `#${res.thread_id}`;
        } catch (e) { toast(`❌ ${e.message}`); } finally { if (state.current == null) $("working").hidden = true; }
      };
      list.appendChild(b);
    }
    if (!r.sessions.length) list.appendChild(el("div", "pk-empty muted", "В этой папке прошлых сессий нет"));
  } catch (e) { $("resume-list").innerHTML = `<div class="pk-empty">${esc(e.message)}</div>`; }
};

// ═══ "/" command palette ═════════════════════════════════════════════════════
const palette = { list: [], sel: 0, key: "" };
async function loadCommands() {
  const key = state.current != null ? `t${state.current}` : `p${currentChoice()?.provider.id || "claude"}`;
  if (palette.key === key) return palette.cache;
  palette.key = key;
  const q = state.current != null ? `tid=${state.current}` : `provider=${encodeURIComponent(currentChoice()?.provider.id || "claude")}`;
  try { palette.cache = (await api(`/api/commands?${q}`)).commands; } catch (_) { palette.cache = []; }
  return palette.cache;
}
async function updatePalette() {
  const v = $("input").value;
  const m = v.match(/^\/([^\s]*)$/);
  const box = $("cmd-palette");
  if (!m) { box.hidden = true; return; }
  const all = await loadCommands();
  const q = m[1].toLowerCase();
  palette.list = all.filter((c) => c.name.slice(1).toLowerCase().includes(q))
    .sort((a, b) => (a.name.slice(1).toLowerCase().startsWith(q) ? 0 : 1) - (b.name.slice(1).toLowerCase().startsWith(q) ? 0 : 1)).slice(0, 40);
  palette.sel = Math.min(palette.sel, Math.max(0, palette.list.length - 1));
  box.innerHTML = "";
  palette.list.forEach((c, i) => {
    const row = el("button", `cp-item${i === palette.sel ? " sel" : ""}`, `<span class="cp-name">${esc(c.name)}</span><span class="cp-desc">${esc(c.description)}</span>${c.source !== "builtin" ? `<span class="cp-src">${c.source === "skill" ? "скил" : "команда"}</span>` : ""}`);
    row.type = "button";
    row.onmousedown = (ev) => { ev.preventDefault(); pickCommand(i); };
    box.appendChild(row);
  });
  box.hidden = !palette.list.length;
  box.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
}
function pickCommand(i) {
  const c = palette.list[i];
  if (!c) return;
  $("input").value = `${c.name} `;
  $("cmd-palette").hidden = true;
  autosize();
  $("input").focus();
}
$("input").addEventListener("input", () => { palette.sel = 0; updatePalette(); });
$("input").addEventListener("keydown", (ev) => {
  if ($("cmd-palette").hidden) return;
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    ev.preventDefault(); ev.stopImmediatePropagation();
    palette.sel = (palette.sel + (ev.key === "ArrowDown" ? 1 : -1) + palette.list.length) % palette.list.length;
    updatePalette();
  } else if (ev.key === "Tab" || (ev.key === "Enter" && !ev.shiftKey)) {
    ev.preventDefault(); ev.stopImmediatePropagation(); pickCommand(palette.sel);
  } else if (ev.key === "Escape") { $("cmd-palette").hidden = true; }
}, true);
$("input").addEventListener("blur", () => setTimeout(() => { $("cmd-palette").hidden = true; }, 150));

// ═══ voice input (server Whisper; works in every browser incl. Firefox/iOS) ══
const micSupported = () => state.stt && navigator.mediaDevices && "MediaRecorder" in window;
const mic = { rec: null, chunks: [], t0: 0, timer: null };
async function toggleMic() {
  if (mic.rec) { mic.rec.stop(); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (_) { toast("Нет доступа к микрофону"); return; }
  const type = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg"].find((t) => MediaRecorder.isTypeSupported?.(t)) || "";
  const rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  mic.rec = rec; mic.chunks = []; mic.t0 = Date.now();
  rec.ondataavailable = (e) => { if (e.data.size) mic.chunks.push(e.data); };
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    clearInterval(mic.timer);
    mic.rec = null;
    $("mic-btn").classList.remove("rec");
    $("mic-btn").classList.add("busy");
    $("mic-time").textContent = "…";
    const blob = new Blob(mic.chunks, { type: rec.mimeType || "audio/webm" });
    const ext = (rec.mimeType || "").includes("mp4") ? "m4a" : (rec.mimeType || "").includes("ogg") ? "ogg" : "webm";
    const form = new FormData();
    form.append("audio", blob, `voice.${ext}`);
    try {
      const { text } = await api("/api/stt", { form });
      if (text) {
        const t = $("input");
        t.value = (t.value.trim() ? `${t.value.trimEnd()} ` : "") + text;
        t.dispatchEvent(new Event("input"));
        t.focus();
      } else toast("Ничего не расслышала");
    } catch (e) { toast(`Распознавание не удалось: ${e.message}`); }
    $("mic-btn").classList.remove("busy");
    $("mic-time").textContent = "";
  };
  rec.start();
  $("mic-btn").classList.add("rec");
  mic.timer = setInterval(() => {
    const sec = Math.floor((Date.now() - mic.t0) / 1000);
    $("mic-time").textContent = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
    if (sec >= 300) rec.stop();
  }, 250);
}
$("mic-btn").onclick = toggleMic;

// ═══ notification mode ═══════════════════════════════════════════════════════
async function setNotifyMode(mode) {
  try {
    state.notifyMode = (await api("/api/settings", { body: { notify_mode: mode } })).notify_mode;
    toast(state.notifyMode === "done" ? "Звенит только последнее сообщение — когда задача готова. Промежуточные приходят без звука" : "Уведомления на каждое сообщение");
  } catch (e) { toast(e.message); }
}

// ═══ message status: sending → delivered → read → being worked on ═══════════
const MSG_STATUS = {
  sending: "Отправляется…",
  sent: "Доставлено в сессию — модель ещё не прочитала",
  queued: "Claude Code отдаст его модели на следующем шаге, работу не прерывает",
  read: "Прочитано моделью",
  working: "Прочитано — Кара работает над этим",
};
function refreshMsgStatus() {
  const thread = $("thread");
  thread.querySelectorAll(".u-msg.latest").forEach((n) => n.classList.remove("latest"));
  const msgs = thread.querySelectorAll(".u-msg");
  const last = msgs[msgs.length - 1];
  if (!last) return;
  let st = "read";
  if (last.classList.contains("failed")) return;
  if (last.classList.contains("pending")) st = !last.dataset.sent ? "sending" : turnRunning(currentSession()) ? "queued" : "sent";
  else {
    const s = currentSession();
    let answered = false;
    for (let n = last.nextElementSibling; n; n = n.nextElementSibling) if (n.classList.contains("a-msg")) { answered = true; break; }
    if (turnRunning(s) && !answered) st = "working";
  }
  last.classList.add("latest");
  last.dataset.status = st;
  const label = last.querySelector(".u-status") || last.querySelector(".u-meta").insertBefore(el("span", "u-status"), last.querySelector(".u-meta").firstChild);
  label.textContent = { sending: "отправляется", sent: "доставлено", queued: "в очереди", read: "прочитано", working: "прочитано · в работе" }[st];
  last.querySelector(".tick").dataset.tip = MSG_STATUS[st];
}

// ═══ home: Kara's own page — now / reach / pending / memory ═════════════════
// /api/hub answers only when <CCGRAM_DIR>/hub.toml exists; otherwise the home
// screen stays a plain greeting.
const HUB_ICON = {
  now: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>',
  reach: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><path d="M12 7.5v4m0 0l-5.3 5.3M12 11.5l5.3 5.3"/></svg>',
  pending: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 6h10M10 12h10M10 18h10M3.5 6l1.5 1.5L8 4.5M3.5 12l1.5 1.5L8 10.5"/><circle cx="5.5" cy="18" r="1.2"/></svg>',
  memory: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5z"/><path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19v-3M9 7h6"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.4-5.7L20 8.5M20 3.5v5h-5"/></svg>',
  map: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/></svg>',
  done: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 12.5l5 5L19.5 7"/></svg>',
  happened: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/></svg>',
  usage: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
  diary: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h11a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6z"/><path d="M6 3v18M10 8h5M10 12h5"/></svg>',
  start: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 5.5v13l10.5-6.5z"/></svg>',
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11.5a7.5 7.5 0 0 1-11 6.6L4 19.5l1.4-4.6A7.5 7.5 0 1 1 20 11.5z"/></svg>',
  later: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="13" r="7.5"/><path d="M12 9.5V13l2.5 1.5M5 4L2.5 6.5M19 4l2.5 2.5"/></svg>',
};

const pluralRu = (n, one, few, many) => {
  const a = Math.abs(n) % 10, b = Math.abs(n) % 100;
  return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 10 || b >= 20) ? few : many;
};
const agoRu = (ts) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 90 ? "только что" : s < 3600 ? `${Math.round(s / 60)} мин назад` : s < 86400 ? `${Math.round(s / 3600)} ч назад` : `${Math.round(s / 86400)} дн. назад`;
};
const dueRu = (n) => (n < 0 ? `просрочено ${-n} ${pluralRu(-n, "день", "дня", "дней")}` : n === 0 ? "сегодня" : n === 1 ? "завтра" : `через ${n} ${pluralRu(n, "день", "дня", "дней")}`);
// Pending items are one-line markdown; [[wiki-links]] point into memory.
const inlineMd = (text) => DOMPurify.sanitize(marked.parseInline(String(text).replace(/\[\[([^\]]+)\]\]/g, "`$1`")));

// The layout must not jump when the data lands: show the last data at once
// (or placeholders on a cold start, the "enabled" flag is remembered), then
// refresh in place.
function showHubCached() {
  if (state.hubData) { applyHub(state.hubData); return; }
  if (store.get("kara-hub") !== "1") return;
  hubSkeleton();
  $("app").classList.add("has-hub");
  $("hub").classList.add("loading");
  $("hub").hidden = false;
}

async function loadHub() {
  clearTimeout(state.hubTimer);
  if (state.current != null) return;
  state.hubTimer = setTimeout(loadHub, 60000);
  if (document.hidden && state.hubData) return;
  let data;
  try { data = await api("/api/hub"); } catch (_) { return; }
  state.hubData = data;
  store.set("kara-hub", data.enabled ? "1" : "0");
  if (state.current == null) applyHub(data);
}

function applyHub(data) {
  const on = Boolean(data.enabled);
  $("app").classList.toggle("has-hub", on);
  $("hub").hidden = !on;
  $("hub").classList.remove("loading");
  if (!on) return;
  hubSkeleton();
  renderNow(data.now);
  renderReach(data.reach);
  renderPending(data.pending);
  renderHappened(data.happened, data.pending);
  renderUsage(data.usage);
  $("hub-diary").hidden = !data.diary;
  if (data.diary) initDiary();
  if (data.memory) renderMemory(data.memory, data.map);
  $("hub-memory").hidden = !data.memory;
  $("hub-pending").hidden = !data.pending;
}

function hideHub() {
  clearTimeout(state.hubTimer);
  $("hub").hidden = true;
  $("app").classList.remove("has-hub");
}

function hubSkeleton() {
  const hub = $("hub");
  if (hub.dataset.built) return;
  hub.dataset.built = "1";
  hub.innerHTML = `
    <div class="hub-grid"><article id="hub-now" class="hub-card"></article><article id="hub-reach" class="hub-card"></article></div>
    <article id="hub-events" class="hub-card" hidden></article>
    <article id="hub-usage" class="hub-card" hidden></article>
    <article id="hub-diary" class="hub-card" hidden></article>
    <article id="hub-pending" class="hub-card"></article>
    <article id="hub-memory" class="hub-card">
      <header class="hub-head">${HUB_ICON.memory}<h2>Что я помню</h2><span id="hub-mem-count" class="hub-sub"></span><span class="spacer"></span>
        <button id="hub-map-btn" type="button" class="btn small" hidden>${HUB_ICON.map}Карта</button></header>
      <label class="hub-search">${ICONS.search}<input id="hub-mem-q" type="search" placeholder="Найти в памяти…" autocomplete="off" enterkeyhint="search"></label>
      <div id="hub-mem-list"></div>
    </article>`;
  $("hub-mem-q").oninput = () => { clearTimeout(state.memTimer); state.memTimer = setTimeout(memSearch, 250); };
  $("hub-map-btn").onclick = () => openMap();
}

function renderNow(now) {
  const t = now.today;
  const w = now.working;
  $("hub-now").innerHTML = `
    <header class="hub-head">${HUB_ICON.now}<h2>Сейчас</h2></header>
    <div class="hub-lead">${w.length ? `${w.length} ${pluralRu(w.length, "чат работает", "чата работают", "чатов работают")}` : "Все чаты ждут тебя"}</div>
    ${w.length ? `<div class="hub-chips">${w.map((c) => `<a class="hub-chip" href="#${c.thread_id}"><span class="dot active"></span>${esc(c.name)}</a>`).join("")}</div>` : ""}
    ${now.recent.length ? `<div class="hub-label">Сегодня были в работе</div><div class="hub-chips">${now.recent.map((c) => `<a class="hub-chip" href="#${c.thread_id}">${esc(c.name)}<span class="hub-chip-t">${hhmm(c.at * 1000)}</span></a>`).join("")}</div>` : ""}
    <div class="hub-stats">
      <div><b>${tokRu(t.out)}</b><span>токенов сегодня</span></div>
      <div><b>${t.turns}</b><span>${pluralRu(t.turns, "ход", "хода", "ходов")}</span></div>
      <div><b>${t.ms ? secsRu(Math.round(t.ms / 1000)) : "—"}</b><span>в работе</span></div>
    </div>
    <div class="hub-foot">Сегодня ${now.chats_today} ${pluralRu(now.chats_today, "чат", "чата", "чатов")} · не спят: ${now.alive}</div>`;
  $("hub-now").querySelector(".hub-stats").dataset.tip = "Токены — то, что модель написала (с размышлениями), как ↓ в Claude Code. Время — сумма ходов.";
}

function reachStatus(r) {
  if (!r.ok) return `${r.why}${r.last_ok ? ` · был ${agoRu(r.last_ok)}` : ""}`;
  const d = r.local && r.detail;
  return d ? `${(d.mem_avail_mb / 1024).toFixed(1).replace(".", ",")} ГБ свободно` : "на связи";
}

function renderReach(reach) {
  const rows = reach.targets.map((r) => `
    <li><span class="rdot ${r.ok ? "ok" : "off"}"></span><span class="rmain" data-tip="${esc(r.access)}"><b>${esc(r.name)}</b><span>${esc(r.access)}</span></span><span class="rstat"${r.local && r.detail ? ` data-tip="Нагрузка ${esc(String(r.detail.load))} · работает ${esc(secsRu(r.detail.uptime_s || 0))}"` : ""}>${esc(reachStatus(r))}</span></li>`);
  for (const g of reach.groups) {
    const bad = g.items.filter((i) => !i.ok);
    const open = state.reachOpen?.has(g.name) ? " open" : "";
    rows.push(`<li class="grp"><details data-group="${esc(g.name)}"${open}><summary><span class="rdot ${bad.length ? "warn" : "ok"}"></span><span class="rmain"><b>${esc(g.name)}</b><span>${g.ok} из ${g.items.length} на связи</span></span><span class="rstat">${bad.length ? `нет: ${esc(bad.map((i) => i.name).join(", "))}` : "все на связи"}</span></summary>
      <ul class="reach-sub">${g.items.map((i) => `<li><span class="rdot ${i.ok ? "ok" : "off"}"></span>${esc(i.name)}${i.ok ? "" : `<span class="muted"> — ${esc(reachStatus(i))}</span>`}</li>`).join("")}</ul></details></li>`);
  }
  const checked = Math.max(0, ...reach.targets.map((r) => r.checked));
  $("hub-reach").innerHTML = `
    <header class="hub-head">${HUB_ICON.reach}<h2>Куда дотягиваюсь</h2><span class="spacer"></span>
      <button id="hub-reach-btn" type="button" class="icon-btn small" data-tip="Проверить сейчас">${HUB_ICON.refresh}</button></header>
    <ul class="reach-list">${rows.join("")}</ul>
    <div class="hub-foot">Проверено ${checked ? agoRu(checked) : "—"} · сама проверяю раз в 5 минут</div>`;
  $("hub-reach").querySelectorAll("details").forEach((d) => {
    d.ontoggle = () => { state.reachOpen = state.reachOpen || new Set(); state.reachOpen[d.open ? "add" : "delete"](d.dataset.group); };
  });
  $("hub-reach-btn").onclick = async (ev) => {
    const btn = ev.currentTarget;
    btn.classList.add("spinning");
    try {
      const reach = await api("/api/hub/reach", { body: {} });
      if (state.hubData) state.hubData.reach = reach;
      renderReach(reach);
    } catch (e) { toast(e.message); btn.classList.remove("spinning"); }
  };
}

// ═══ «Расход»: output tokens per day for a week + the chats that used most ═
const WEEKDAY = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const niceMax = (v) => {
  if (v <= 0) return 1000;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
};
// A column with a 4px rounded data-end, square at the baseline.
const colPath = (x, base, w, h) => {
  if (h <= 0) return "";
  const r = Math.min(4, w / 2, h);
  return `M${x},${base}V${base - h + r}a${r},${r} 0 0 1 ${r},${-r}H${x + w - r}a${r},${r} 0 0 1 ${r},${r}V${base}Z`;
};
function usageChart(days) {
  const W = 420, H = 160, left = 40, right = 6, top = 20, bottom = 24;
  const plotW = W - left - right, plotH = H - top - bottom, base = top + plotH;
  const max = niceMax(Math.max(...days.map((d) => d.out)));
  const band = plotW / days.length;
  const bw = Math.min(24, band * 0.6);
  const peak = days.reduce((best, d, i) => (d.out > days[best].out ? i : best), 0);
  const last = days.length - 1;
  let svg = "";
  for (const v of [0, max / 2, max]) {
    const yy = base - (v / max) * plotH;
    svg += `<line class="u-grid" x1="${left}" x2="${W - right}" y1="${yy}" y2="${yy}"/><text class="u-tick" x="${left - 6}" y="${yy + 4}" text-anchor="end">${v ? tokRu(v) : "0"}</text>`;
  }
  days.forEach((d, i) => {
    const x = left + band * i + (band - bw) / 2;
    const h = d.out ? Math.max(2, (d.out / max) * plotH) : 0;
    const dt = new Date(`${d.date}T12:00:00`);
    const name = i === last ? "сегодня" : WEEKDAY[dt.getDay()];
    const tip = `${i === last ? "Сегодня" : `${WEEKDAY[dt.getDay()]} ${dt.toLocaleDateString("ru-RU", { day: "numeric", month: "numeric" })}`} · ${tokRu(d.out)} токенов · ${d.turns} ${pluralRu(d.turns, "ход", "хода", "ходов")}`;
    svg += `<path class="u-col" d="${colPath(x, base, bw, h)}"/>`;
    if (d.out && (i === last || i === peak)) svg += `<text class="u-val" x="${x + bw / 2}" y="${base - h - 6}" text-anchor="middle">${tokRu(d.out)}</text>`;
    svg += `<text class="u-tick${i === last ? " now" : ""}" x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${name}</text>`;
    svg += `<rect class="u-hit" x="${left + band * i}" y="${top - 14}" width="${band}" height="${plotH + 14}" data-tip="${esc(tip)}"/>`;
  });
  return `<svg class="u-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Токены ответа по дням за неделю">${svg}</svg>`;
}

// ═══ plan limits — what /usage shows in Claude Code ═════════════════════════
function resetRu(ts) {
  if (!ts) return "";
  const left = Math.max(0, Math.round(ts - Date.now() / 1000));
  const d = new Date(ts * 1000);
  const at = left < 86400 ? hhmm(ts * 1000) : `${d.toLocaleDateString("ru-RU", { weekday: "short" })} ${hhmm(ts * 1000)}`;
  const inRu = left < 3600 ? `${Math.max(1, Math.round(left / 60))} мин` : left < 86400 ? `${Math.floor(left / 3600)} ч ${Math.floor((left % 3600) / 60)} мин` : `${Math.floor(left / 86400)} д ${Math.floor((left % 86400) / 3600)} ч`;
  return `сброс через ${inRu} · ${at}`;
}
function limitsHtml(u) {
  const rows = u.limits.map((l) => {
    const pct = Math.round(l.percent);
    const level = l.severity !== "normal" || pct >= 95 ? "crit" : pct >= 80 ? "warn" : "";
    return `<div class="lim ${level}"><div class="lim-top"><span>${esc(l.label)}</span><b>${pct} %</b></div>
      <div class="lim-bar"><i style="width:${Math.min(100, Math.max(pct ? 1.5 : 0, pct))}%"></i></div>
      <div class="lim-reset">${esc(resetRu(l.resets_at))}</div></div>`;
  }).join("");
  const parts = u.surfaces.filter((x) => x.percent > 0).map((x) => `${esc(x.name)} ${Math.round(x.percent)} %`);
  const when = u.live ? `обновлено ${agoRu(u.as_of) || "только что"}` : `данные Claude Code от ${hhmm(u.as_of * 1000)}`;
  return `${rows}<div class="lim-foot">${parts.length ? `Неделя по продуктам: ${parts.join(" · ")} · ` : ""}${when}</div>`;
}
async function fetchLimits(refresh) {
  try { state.limits = (await api(`/api/usage${refresh ? "?refresh=1" : ""}`)).usage; } catch (_) { /* keep the last */ }
  return state.limits;
}
// "/usage" in a chat: a card in the thread, like Claude Code's own panel.
async function showUsage() {
  if (state.current == null) { // home: the "Расход" card has the same block
    $("hub-usage").scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
    renderLimits(await fetchLimits(true));
    return;
  }
  const card = el("div", "usage-card", '<div class="uc-head"><b>Лимиты Claude</b><span class="uc-plan"></span></div><div class="uc-body muted">Узнаю у Anthropic…</div>');
  $("thread").appendChild(card);
  toBottom(true);
  const u = await fetchLimits(true);
  card.querySelector(".uc-body").classList.remove("muted");
  card.querySelector(".uc-plan").textContent = u?.plan || "";
  card.querySelector(".uc-body").innerHTML = u ? limitsHtml(u) : "Лимиты сейчас недоступны — Claude Code ещё ни разу их не запрашивал.";
  toBottom(true);
}
function renderLimits(u) {
  const box = $("hub-usage")?.querySelector(".u-limits");
  if (!box) return;
  box.hidden = !u || !u.limits.length;
  if (!box.hidden) box.innerHTML = `<div class="hub-label">Лимиты Claude${u.plan ? ` · ${esc(u.plan)}` : ""}</div><div class="lim-grid">${limitsHtml(u)}</div>`;
}

// After a slash command: Claude Code may have opened a dialog in the terminal
// (/status, /config, /model…) that never reaches the transcript — show it.
async function revealDialog(tid) {
  if (state.current !== tid || !$("pane").hidden) return;
  let data;
  try { data = await api(`/api/sessions/${tid}/pane`); } catch (_) { return; }
  if (!data.alive || state.current !== tid) return;
  const tail = data.text.replace(/\s+$/, "").split("\n").slice(-14);
  if (tail.some((line) => /^\s*❯/.test(line))) return; // the prompt is there: no dialog
  $("pane").hidden = false;
  pollPane();
  toast("Claude Code открыл окно — оно в терминале. Закрыть — Esc");
}

function renderUsage(u) {
  const card = $("hub-usage");
  card.hidden = !u;
  if (!u) return;
  const top = u.chats[0]?.out || 1;
  card.innerHTML = `
    <header class="hub-head">${HUB_ICON.usage}<h2>Расход</h2><span class="hub-sub">за 7 дней ${tokRu(u.total)} токенов ответа</span></header>
    <div class="u-limits" hidden></div>
    <div class="u-cols">
      <div>${usageChart(u.days)}
        <details class="u-table"><summary>Таблицей</summary><table>${u.days.map((d) => `<tr><td>${esc(d.date.split("-").reverse().slice(0, 2).join("."))}</td><td>${tokRu(d.out)}</td><td>${d.turns} ${pluralRu(d.turns, "ход", "хода", "ходов")}</td></tr>`).join("")}</table></details>
      </div>
      <div><div class="hub-label">Больше всего</div>
        ${u.chats.length ? `<ul class="u-chats">${u.chats.map((c) => `<li><a href="#${c.thread_id}">${esc(c.name)}</a><span class="u-num">${tokRu(c.out)}</span><span class="u-share"><i style="width:${Math.max(3, (c.out / top) * 100)}%"></i></span></li>`).join("")}</ul>` : '<div class="muted ev-none">За неделю чаты не работали</div>'}
      </div>
    </div>`;
  if (state.limits) renderLimits(state.limits);
  if (!state.limitsAt || Date.now() - state.limitsAt > 60000) {
    state.limitsAt = Date.now();
    fetchLimits(false).then(renderLimits);
  }
}

// ═══ «Дневник»: a short digest of a day, written on request ═════════════════
const isoDay = (back) => {
  const d = new Date();
  d.setDate(d.getDate() - back);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
function dayLabel(back, iso) {
  if (back === 0) return "сегодня";
  if (back === 1) return "вчера";
  return new Date(`${iso}T12:00:00`).toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "long" });
}
function initDiary() {
  const card = $("hub-diary");
  if (card.dataset.built) return;
  card.dataset.built = "1";
  state.diaryBack = 0;
  card.innerHTML = `<header class="hub-head">${HUB_ICON.diary}<h2>Дневник</h2><span class="spacer"></span>
    <button type="button" id="dy-prev" class="icon-btn small" data-tip="День назад">‹</button><span id="dy-day" class="dy-day"></span><button type="button" id="dy-next" class="icon-btn small" data-tip="День вперёд">›</button></header>
    <div id="dy-body"></div>`;
  $("dy-prev").onclick = () => { state.diaryBack += 1; loadDiary(); };
  $("dy-next").onclick = () => { if (state.diaryBack > 0) { state.diaryBack -= 1; loadDiary(); } };
  loadDiary();
}
async function loadDiary() {
  const back = state.diaryBack;
  const day = isoDay(back);
  $("dy-day").textContent = dayLabel(back, day);
  $("dy-next").disabled = back === 0;
  try {
    const d = await api(`/api/hub/diary?date=${day}`);
    if (state.diaryBack === back) renderDiary(d, back);
  } catch (e) { $("dy-body").textContent = e.message; }
}
function renderDiary(d, back) {
  const body = $("dy-body");
  body.innerHTML = "";
  if (d.text) {
    const md = renderMarkdown(d.text);
    md.className = "dy-text";
    body.appendChild(md);
  } else {
    body.appendChild(el("div", "hub-empty", back === 0 ? "Дневник за сегодня ещё не составлен." : "За этот день дневника нет."));
  }
  const row = el("div", "dy-row");
  if (!d.text || back === 0) {
    const btn = el("button", "btn small", d.text ? "Обновить" : "Составить");
    btn.type = "button";
    btn.onclick = async () => {
      btn.disabled = true;
      btn.textContent = "Составляю… обычно полминуты";
      try { const r = await api("/api/hub/diary", { body: { date: d.date } }); if (state.diaryBack === back) renderDiary(r, back); } catch (e) { toast(e.message); btn.disabled = false; btn.textContent = "Составить"; }
    };
    row.appendChild(btn);
  }
  if (d.generated) row.appendChild(el("span", "muted", `составлен ${whenRu(d.generated)}`));
  body.appendChild(row);
}

const ALERT_DOT = { escalated: "err", "serial-flapper-suppressed": "warn", failed: "warn", flap: "ok", resolved: "ok" };
function renderHappened(h, pending) {
  const card = $("hub-events");
  card.hidden = !h;
  if (!h) return;
  const a = h.alerts;
  const now = new Date();
  const nowHm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  const alertRows = a ? (a.items.length ? a.items.map((x) => `
    <li><span class="rdot ${ALERT_DOT[x.kind] || ""}"></span><span class="ev-main"><b>${esc(x.name)}${x.instance && x.instance !== "unknown" ? ` · ${esc(x.instance)}` : ""}</b><span class="muted">${esc(x.label)} · ${hhmm(x.ts * 1000)}${x.count > 1 ? ` · ×${x.count}` : ""}</span>
      ${x.verdict ? `<details class="ev-verdict"><summary>Мой разбор</summary><div>${esc(x.verdict)}</div></details>` : ""}</span></li>`).join("") : '<li class="muted">Тихо — алертов за сутки не было</li>') : "";
  const cal = h.calendar;
  const calRow = (e) => `<li class="${e.day === "today" && e.time && e.time < nowHm ? "past" : ""}"><span class="ev-time">${esc(e.time || "весь день")}</span><span>${esc(e.title)}</span></li>`;
  const todayCal = (cal || []).filter((e) => e.day === "today");
  const tomorrowCal = (cal || []).filter((e) => e.day === "tomorrow");
  const calRows = cal === null ? '<li class="muted">Календарь загружается…</li>' : cal ? `${todayCal.length ? todayCal.map(calRow).join("") : '<li class="muted">В календаре на сегодня пусто</li>'}${tomorrowCal.length ? `<li class="ev-day">Завтра</li>${tomorrowCal.map(calRow).join("")}` : ""}` : "";
  const due = ((pending && pending.items) || []).filter((it) => it.days <= 0);
  const late = due.filter((it) => it.days < 0).length;
  card.innerHTML = `
    <header class="hub-head">${HUB_ICON.happened}<h2>Что случилось</h2><span class="hub-sub">${a ? `за сутки: ${a.important} ${pluralRu(a.important, "важный", "важных", "важных")}, ${a.noise} шума` : ""}</span></header>
    <div class="ev-cols">
      ${a ? `<section><div class="hub-label">Алерты</div><ul class="ev-list">${alertRows}</ul></section>` : ""}
      <section><div class="hub-label">Сегодня</div>${"calendar" in h && cal !== undefined ? `<ul class="ev-cal">${calRows}</ul>` : ""}
        ${due.length ? `<button type="button" class="link-btn ev-due">${due.length} ${pluralRu(due.length, "дело", "дела", "дел")} на сегодня${late ? ` · просрочено ${late}` : ""}</button>` : '<div class="muted ev-none">Дел на сегодня нет</div>'}</section>
    </div>`;
  const jump = card.querySelector(".ev-due");
  if (jump) jump.onclick = () => $("hub-pending").scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
}

function renderPending(p) {
  const items = (p && p.items) || [];
  const card = $("hub-pending");
  card.innerHTML = `
    <header class="hub-head">${HUB_ICON.pending}<h2>Мои дела</h2><span class="hub-sub">${items.length || ""}</span></header>
    ${items.length ? `<ul class="pend-list">${items.map((it) => {
      const [, m, d] = it.date.split("-");
      const cls = it.days < 0 ? "late" : it.days === 0 ? "today" : "";
      const chat = it.chat != null && sessionById(it.chat);
      const go = chat
        ? `<button type="button" class="icon-btn small" data-act="open" data-tip="Открыть чат по этому делу">${HUB_ICON.chat}</button>`
        : `<button type="button" class="icon-btn small" data-act="start" data-tip="Взяться — новый чат с контекстом дела">${HUB_ICON.start}</button>`;
      return `<li class="pend ${cls}" data-id="${it.id}"><span class="pdate"><b>${d}.${m}</b><span>${dueRu(it.days)}</span></span>
        <span class="pbody"><span class="ptext">${inlineMd(it.text)}</span>${chat ? `<a class="hub-chip pchat" href="#${chat.thread_id}"><span class="dot ${esc(chat.state)}"></span><span class="pchat-name">${esc(chat.name)}</span>${STATE_LABEL[chat.state] ? `<span class="muted">· ${esc(STATE_LABEL[chat.state])}</span>` : ""}</a>` : ""}</span>
        <span class="pacts">${go}<button type="button" class="icon-btn small" data-act="done" data-tip="Сделано — убрать в архив">${HUB_ICON.done}</button><button type="button" class="icon-btn small" data-act="later" data-tip="Отложить">${HUB_ICON.later}</button></span></li>`;
    }).join("")}</ul>` : '<div class="hub-empty">Обещанных дел нет — всё закрыто.</div>'}`;
  card.querySelectorAll(".ptext").forEach((t) => { t.onclick = (ev) => { if (!ev.target.closest("a")) t.classList.toggle("open"); }; });
  card.querySelectorAll(".pend").forEach((row) => {
    const it = items.find((x) => x.id === row.dataset.id);
    const start = row.querySelector('[data-act="start"]');
    if (start) start.onclick = () => startPending(it, start);
    const open = row.querySelector('[data-act="open"]');
    if (open) open.onclick = () => { location.hash = `#${it.chat}`; };
    row.querySelector('[data-act="done"]').onclick = () => closePending(it);
    row.querySelector('[data-act="later"]').onclick = (ev) => {
      const btn = ev.currentTarget;
      openMenu([{ heading: "Напомнить" }, ...[[1, "завтра"], [3, "через 3 дня"], [7, "через неделю"], [30, "через месяц"]].map(([days, label]) => ({ label, action: () => snoozePending(it, days) }))], menuBelow(btn), btn);
    };
  });
}

function keepPending(p) {
  if (state.hubData) { state.hubData.pending = p; renderHappened(state.hubData.happened, p); }
  return p;
}

async function closePending(it) {
  const short = it.text.replace(/\s+/g, " ").slice(0, 180);
  if (!(await confirmDialog("Дело сделано?", `${short}${it.text.length > 180 ? "…" : ""}\n\nУберу его в архив закрытых дел.`, "Закрыть", false))) return;
  try { renderPending(keepPending(await api(`/api/hub/pending/${it.id}/close`, { body: {} }))); toast("Закрыто — дело в архиве"); } catch (e) { toast(e.message); loadHub(); }
}

// A chat for the item, set up like the composer's (Telegram or not, model,
// effort); the server writes its first message from the item and its memories.
async function startPending(it, btn) {
  btn.disabled = true;
  btn.classList.add("busy");
  toast(state.telegram ? "Создаю чат и топик…" : "Создаю чат…");
  try {
    const c = currentChoice();
    const effort = c?.provider.id === "claude" && state.effort ? state.effort : undefined;
    const res = await api(`/api/hub/pending/${it.id}/start`, { body: { web_only: !state.telegram, provider: c?.provider.id, model: c?.model?.id || undefined, effort } });
    state.pending.set(res.thread_id, { text: res.text, at: Date.now() });
    startInflight(res.thread_id);
    await refreshSessions();
    location.hash = `#${res.thread_id}`;
  } catch (e) {
    toast(`❌ ${e.message}`);
    btn.disabled = false;
    btn.classList.remove("busy");
    loadHub();
  }
}

async function snoozePending(it, days) {
  try {
    const p = keepPending(await api(`/api/hub/pending/${it.id}/snooze`, { body: { days } }));
    renderPending(p);
    const moved = p.items.find((x) => x.text === it.text);
    toast(moved ? `Напомню ${moved.date.split("-").reverse().slice(0, 2).join(".")}` : "Отложено");
  } catch (e) { toast(e.message); loadHub(); }
}

function renderMemory(mem, hasMap) {
  state.memToday = mem.today;
  state.hubMap = hasMap;
  $("hub-map-btn").hidden = !hasMap;
  $("hub-mem-count").innerHTML = `${mem.total} ${pluralRu(mem.total, "запись", "записи", "записей")}<span class="wide"> · ${mem.domains} ${pluralRu(mem.domains, "раздел", "раздела", "разделов")}</span>`;
  if (!$("hub-mem-q").value.trim()) showToday();
}

function showToday() {
  const items = state.memToday || [];
  const fresh = items.filter((d) => d.change === "new").length;
  const label = items.length
    ? `Сегодня: ${fresh ? `${fresh} ${pluralRu(fresh, "новая", "новые", "новых")}` : ""}${fresh && items.length > fresh ? ", " : ""}${items.length > fresh ? `${items.length - fresh} ${pluralRu(items.length - fresh, "обновлена", "обновлены", "обновлено")}` : ""}`
    : "Сегодня в памяти ничего не менялось";
  renderMemList(items, label);
}

function renderMemList(items, label, all = false) {
  const list = $("hub-mem-list");
  const shown = all ? items : items.slice(0, 6);
  list.innerHTML = `<div class="mem-label">${esc(label)}</div>${shown.map((d) => `
    <button type="button" class="mem-item" data-path="${esc(d.path)}"><span class="mem-dom">${esc(d.domain)}</span>
      <span class="mem-title">${esc(d.title)}${d.change === "new" ? '<span class="mem-new">новое</span>' : ""}</span>
      <span class="mem-desc">${esc(d.description)}</span></button>`).join("")}
    ${items.length > shown.length ? `<button type="button" class="link-btn mem-more">Показать ещё ${items.length - shown.length}</button>` : ""}`;
  list.querySelectorAll(".mem-item").forEach((b) => { b.onclick = () => openMemory(b.dataset.path); });
  const more = list.querySelector(".mem-more");
  if (more) more.onclick = () => renderMemList(items, label, true);
}

async function memSearch() {
  const q = $("hub-mem-q").value.trim();
  if (!q) { showToday(); return; }
  try {
    const r = await api(`/api/hub/memory/search?q=${encodeURIComponent(q)}`);
    if ($("hub-mem-q").value.trim() !== q) return;
    renderMemList(r.results, r.results.length ? `Нашлось ${r.results.length}${r.results.length === 20 ? "+" : ""} — самые подходящие сверху` : "Ничего не нашлось");
  } catch (e) { toast(e.message); }
}

// ═══ read a reply aloud (TTS) ════════════════════════════════════════════════
// The reply goes out in sentence groups; the next one is fetched while the
// current one plays, so speech starts in about a second.
const TTS_CHUNK = 350;
// 50 ms of silence: played inside the tap, it unlocks the element on iOS.
const SILENCE = "data:audio/mpeg;base64,SUQzBAAAAAAAIlRTU0UAAAAOAAADTGF2ZjYxLjcuMTAzAAAAAAAAAAAAAAD/83DAAAAAAAAAAAAASW5mbwAAAA8AAAAEAAACWAB6enp6enp6enp6enp6enp6enp6enp6enqmpqampqampqampqampqampqampqampqam09PT09PT09PT09PT09PT09PT09PT09PT0/////////////////////////////////8AAAAATGF2YzYxLjE5AAAAAAAAAAAAAAAAJAJxAAAAAAAAAlj7+meXAAAAAAAAAAAAAAAAAP/zQMQAAAADSAAAAABMQU1FMy4xMDBVVVVVVVVVVVVVTEFNRTMuMTAwVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV//NCxFsAAANIAAAAAFVVVVVVVVVVVVVVVVVVVVVVVVVVTEFNRTMuMTAwVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV//NAxKQAAANIAAAAAFVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVX/80LEowAAA0gAAAAAVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVU=";
function speechText(node) {
  const body = node.firstElementChild.cloneNode(true);
  body.querySelectorAll("pre, .codebox, table, img").forEach((x) => x.remove());
  return body.innerText.trim();
}
function speechChunks(text) {
  const sentences = text.replace(/\s*\n+\s*/g, ". ").replace(/\.\s*\./g, ".").match(/[^.!?…]+[.!?…]*\s*/g) || [];
  const chunks = [];
  let cur = "";
  for (const raw of sentences) {
    const part = raw.length > TTS_CHUNK * 2 ? `${raw.slice(0, TTS_CHUNK * 2)} ` : raw;
    if (cur && (cur + part).length > TTS_CHUNK) { chunks.push(cur.trim()); cur = ""; }
    cur += part;
  }
  if (cur.trim()) chunks.push(cur.trim());
  return chunks.filter((c) => /[\p{L}\p{N}]/u.test(c));
}
async function ttsClip(text) {
  const res = await fetch("/api/tts", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CCGram": "1" }, body: JSON.stringify({ text }) });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
  return URL.createObjectURL(await res.blob());
}
function stopSpeaking() {
  const run = state.speech;
  if (!run) return;
  state.speech = null;
  run.stopped = true;
  run.audio.pause();
  run.resolve?.();
  run.btn.textContent = "Прослушать";
  run.btn.classList.remove("on");
}
async function speak(node, btn) {
  if (state.speech?.btn === btn) { stopSpeaking(); return; }
  stopSpeaking();
  const chunks = speechChunks(speechText(node)).slice(0, 60);
  if (!chunks.length) { toast("Здесь нечего читать вслух"); return; }
  const audio = new Audio(SILENCE);
  audio.play().catch(() => {});
  const run = { audio, btn, stopped: false };
  state.speech = run;
  btn.textContent = "Стоп";
  btn.classList.add("on");
  let next = ttsClip(chunks[0]);
  for (let i = 0; i < chunks.length && !run.stopped; i++) {
    let url;
    try { url = await next; } catch (e) { toast(`Озвучка: ${e.message}`); break; }
    if (i + 1 < chunks.length) next = ttsClip(chunks[i + 1]);
    if (run.stopped) { URL.revokeObjectURL(url); break; }
    audio.src = url;
    await new Promise((resolve) => { run.resolve = resolve; audio.onended = resolve; audio.onerror = resolve; audio.play().catch(resolve); });
    URL.revokeObjectURL(url);
  }
  if (state.speech === run) stopSpeaking();
}

// ═══ sheet: the memory map (or one memory) over the page ═══════════════════
function openSheet(title) {
  $("sheet-title").textContent = title;
  syncSheetActions();
  $("sheet").hidden = false;
  document.body.classList.add("sheet-open");
}
function closeSheet() {
  $("sheet").hidden = true;
  state.sheetPath = null;
  document.body.classList.remove("sheet-open");
}
$("sheet-close").onclick = closeSheet;
$("sheet").addEventListener("mousedown", (ev) => { if (ev.target === $("sheet")) closeSheet(); });
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !$("sheet").hidden && $("menu").hidden) closeSheet(); });

// The map is a 4 MB single page: load it once, then jump by hash.
function openMap(path) {
  const frame = $("sheet-frame");
  const hash = path ? `#file:${encodeURIComponent(path)}` : "";
  state.sheetPath = path || null;
  $("sheet-body").hidden = true;
  frame.hidden = false;
  openSheet("Карта памяти");
  const win = frame.src ? frame.contentWindow : null;
  // After an edit / forget the map is rebuilt in the background: reload it.
  if (!win || typeof win.fromHash !== "function" || state.mapStale) { state.mapStale = false; frame.src = `/hub/map/index.html?v=${Date.now()}${hash}`; return; }
  if (!hash) return;
  win.history.replaceState(null, "", hash);
  win.fromHash();
}

async function openMemory(path) {
  if (state.hubMap) { openMap(path); return; }
  try { showMemoryText(await api(`/api/hub/memory/file?path=${encodeURIComponent(path)}`)); } catch (e) { toast(e.message); }
}

function showMemoryText(d) {
  state.sheetPath = d.path;
  $("sheet-frame").hidden = true;
  const body = $("sheet-body");
  body.hidden = false;
  body.replaceChildren(renderMarkdown(d.body));
  openSheet(d.title);
}

// The map tells which memory it shows; the sheet's buttons act on that one.
window.addEventListener("message", (ev) => {
  if (ev.origin !== location.origin || ev.data?.type !== "kara-memory") return;
  state.sheetPath = ev.data.file || null;
  syncSheetActions();
});
function syncSheetActions() {
  const editing = Boolean($("sheet-body").querySelector(".mem-editor"));
  $("sheet-edit").hidden = $("sheet-forget").hidden = !state.sheetPath || editing;
}

async function editMemory() {
  const path = state.sheetPath;
  if (!path) return;
  let d;
  try { d = await api(`/api/hub/memory/file?path=${encodeURIComponent(path)}`); } catch (e) { toast(e.message); return; }
  const back = () => (state.hubMap ? openMap(path) : showMemoryText(d));
  $("sheet-frame").hidden = true;
  const body = $("sheet-body");
  body.hidden = false;
  body.innerHTML = `<div class="mem-editor">
    <label>Описание<input id="me-desc" type="text"></label>
    <label class="me-grow">Текст<textarea id="me-body" spellcheck="true"></textarea></label>
    <div class="me-actions"><button type="button" id="me-save" class="btn primary small">Сохранить</button><button type="button" id="me-cancel" class="btn small">Отмена</button><span class="muted">Перед записью сохраню копию в ~/memory-backups</span></div></div>`;
  $("me-desc").value = d.description || "";
  $("me-body").value = d.body;
  $("sheet-title").textContent = `Правка: ${d.title}`;
  syncSheetActions();
  $("me-body").focus();
  $("me-cancel").onclick = back;
  $("me-save").onclick = async () => {
    try {
      const saved = await api("/api/hub/memory/save", { body: { path, body: $("me-body").value, description: $("me-desc").value, mtime: d.mtime } });
      state.mapStale = true;
      toast("Сохранено — карта обновится через минуту");
      showMemoryText(saved);
      loadHub();
    } catch (e) { toast(e.message); }
  };
}

async function forgetMemory() {
  const path = state.sheetPath;
  if (!path) return;
  const name = path.split("/").pop().replace(/\.md$/, "");
  if (!(await confirmDialog(`Забыть «${name}»?`, "Запись уйдёт в архив памяти, указатели на неё уберу из оглавлений. Ничего не стирается — вернуть можно из архива.", "Забыть"))) return;
  try {
    const r = await api("/api/hub/memory/forget", { body: { path } });
    state.mapStale = true;
    closeSheet();
    toast(`Забыла — запись в ${r.archived.split("/").slice(0, 2).join("/")}`);
    loadHub();
  } catch (e) { toast(e.message); }
}
$("sheet-edit").onclick = editMemory;
$("sheet-forget").onclick = forgetMemory;

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
api("/api/me").then(boot).catch(() => showLogin());
