// Only public branding and the offline explanation are cached. Documents,
// attachments, authenticated HTML, API responses and writes always use network.
const CACHE = "notedown-offline-v1";
const OFFLINE = "/offline.html";
const PUBLIC_FILES = [OFFLINE, "/icons/notedown.svg", "/icons/favicon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      await cache.addAll(
        PUBLIC_FILES.map((url) => new Request(url, { cache: "reload" })),
      );
      // No page reload or app-shell cache: an update never discards editor drafts.
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const name of await caches.keys()) {
        if (name.startsWith("notedown-offline-") && name !== CACHE)
          await caches.delete(name);
      }
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  const appPage = ["/", "/login", "/setup", "/admin", "/editor"].includes(
    url.pathname,
  );
  if (request.mode === "navigate" && appPage) {
    event.respondWith(
      fetch(request).catch(async () => {
        const cached = await (await caches.open(CACHE)).match(OFFLINE);
        return cached || Response.error();
      }),
    );
  } else if (PUBLIC_FILES.includes(url.pathname) && !url.search) {
    event.respondWith(
      fetch(request).catch(async () => {
        const cached = await (await caches.open(CACHE)).match(url.pathname);
        return cached || Response.error();
      }),
    );
  }
});
