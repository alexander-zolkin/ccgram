// Minimal service worker: makes the page installable as a PWA. No caching —
// the app is live-only, a stale offline shell would only mislead.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

// Web Push from the server: a finished reply while no Kara tab is on screen.
self.addEventListener("push", (e) => {
  let p = {};
  try { p = e.data ? e.data.json() : {}; } catch (_) { p = { body: e.data && e.data.text() }; }
  const prompt = p.prompt && p.tid != null;
  e.waitUntil(self.registration.showNotification(p.title || "Кара", {
    body: p.body || "",
    tag: p.tag,
    renotify: Boolean(p.tag),
    icon: "/static/icon-192.png",
    badge: "/static/badge-96.png",
    data: { url: p.url || "/", tid: p.tid, prompt: p.prompt, title: p.title },
    // Android / desktop show these; iOS ignores them and a tap opens the chat.
    actions: prompt ? [{ action: "allow", title: "Разрешить" }, { action: "deny", title: "Отклонить" }] : [],
    requireInteraction: Boolean(prompt),
  }));
});

// «Разрешить» / «Отклонить»: the server presses the key only if the very
// prompt this notification was about is still on screen; otherwise (or on any
// error) the chat opens so the answer is given looking at the real prompt.
async function answer(d, choice) {
  try {
    const res = await fetch(`/api/sessions/${d.tid}/answer`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CCGram": "1" },
      body: JSON.stringify({ prompt: d.prompt, choice }),
    });
    if (res.ok) {
      await self.registration.showNotification(d.title || "Кара", {
        body: choice === "allow" ? "✅ Разрешено" : "🚫 Отклонено",
        tag: `t${d.tid}`,
        silent: true,
        icon: "/static/icon-192.png",
        badge: "/static/badge-96.png",
        data: { url: d.url },
      });
      return true;
    }
  } catch (_) { /* offline: fall through to opening the chat */ }
  return false;
}
// Notifications are shown through the worker (the only way on Android);
// a tap focuses an open tab on that chat, or opens one.
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const data = e.notification.data || {};
  const url = data.url || "/";
  e.waitUntil((async () => {
    if ((e.action === "allow" || e.action === "deny") && data.prompt && (await answer(data, e.action))) return;
    const tabs = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const tab of tabs) {
      if ("focus" in tab) { await tab.navigate(url).catch(() => {}); return tab.focus(); }
    }
    return self.clients.openWindow(url);
  })());
});
