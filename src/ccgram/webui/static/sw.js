// Minimal service worker: makes the page installable as a PWA. No caching —
// the app is live-only, a stale offline shell would only mislead.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
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
