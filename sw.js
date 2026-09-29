const CACHE_NAME = 'trysearch-shell-v18';

// Only precache URLs that are guaranteed to exist. cache.addAll() rejects as a
// unit, so a single 404 aborts install and leaves the previous service worker
// in control permanently — which is exactly how the v17 shell got stuck.
const APP_SHELL = [
  '/',
  '/manifest.webmanifest',
  '/trysearch-logo.png'
];

// Application code must never be served cache-first: a stale bundle renders an
// old UI against a new API. These prefixes are always revalidated.
const ALWAYS_REVALIDATE = ['/static/js/', '/static/css/', '/sw.js', '/pwa.js'];

function isAppCode(pathname) {
  return ALWAYS_REVALIDATE.some((prefix) => pathname.startsWith(prefix));
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      // Per-entry so one missing asset cannot abort the whole installation.
      .then((cache) => Promise.all(APP_SHELL.map((url) => cache.add(url).catch(() => null))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((key) => key.startsWith('trysearch-') && key !== CACHE_NAME).map((key) => caches.delete(key))
  )).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).then((response) => {
      const copy = response.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
      return response;
    }).catch(() => caches.match(request).then((cached) => cached || caches.match('/'))));
    return;
  }

  if (isAppCode(url.pathname)) {
    // Network-first, and `cache: 'no-cache'` forces an HTTP revalidation so an
    // edge-injected max-age cannot pin an old bundle in the browser HTTP cache.
    // The origin sends strong ETags, so the common case is a cheap 304.
    event.respondWith(
      fetch(request, { cache: 'no-cache' }).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        }
        return response;
      }).catch(() => caches.match(request))
    );
    return;
  }

  event.respondWith(caches.match(request).then((cached) => cached || fetch(request).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
    }
    return response;
  })));
});
