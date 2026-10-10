// Service worker: lets the site be installed as an app (iPhone "На экран Домой", Android Chrome) and
// shows a friendly page instead of the browser error when there is no internet.
// Network first for everything, so users always get the current version; the cache is only a fallback.
// API calls are never cached.
const CACHE = "master-ryadom-v1";
const CORE = ["offline.html", "css/style.css", "js/icons.js", "js/i18n.js", "js/api.js", "js/nav.js", "icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/uploads/")) return;

  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok && response.type === "basic") {
          const copy = response.clone();
          caches.open(CACHE).then(cache => cache.put(request, copy));
        }
        return response;
      })
      .catch(async () => {
        const cached = await caches.match(request, { ignoreSearch: request.mode === "navigate" });
        if (cached) return cached;
        if (request.mode === "navigate") return caches.match("offline.html");
        return Response.error();
      })
  );
});
