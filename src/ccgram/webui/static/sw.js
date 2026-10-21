// Minimal service worker: makes the page installable as a PWA. No caching —
// the app is live-only, a stale offline shell would only mislead.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
