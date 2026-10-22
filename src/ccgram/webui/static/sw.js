// Minimal service worker: makes the page installable as a PWA. No caching —
// the app is live-only, a stale offline shell would only mislead.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});

// Web Push from the server: a finished reply while no Kara tab is on screen.
self.addEventListener("push", (e) => {
  let p = {};
  try { p = e.data ? e.data.json() : {}; } catch (_) { p = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(p.title || "Кара", {
    body: p.body || "",
    tag: p.tag,
    renotify: Boolean(p.tag),
    icon: "/static/icon-192.png",
    badge: "/static/badge-96.png",
    data: { url: p.url || "/" },
  }));
});
// Notifications are shown through the worker (the only way on Android);
// a tap focuses an open tab on that chat, or opens one.
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil((async () => {
    const tabs = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const tab of tabs) {
      if ("focus" in tab) { await tab.navigate(url).catch(() => {}); return tab.focus(); }
    }
    return self.clients.openWindow(url);
  })());
});
