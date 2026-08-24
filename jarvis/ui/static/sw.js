// Nur die Huelle wird zwischengespeichert, damit sich die Seite als App
// installieren laesst. Job-Daten kommen immer frisch vom Server - ein
// zwischengespeicherter Job-Status waere schlimmer als gar keiner.

var CACHE = "jarvis-v3";
var HUELLE = [
  "/static/style.css",
  "/static/app.js",
  "/static/aufnahme.js",
  "/static/icon.svg",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/apple-touch-icon.png",
  "/static/manifest.webmanifest"
];

self.addEventListener("install", function (ereignis) {
  ereignis.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(HUELLE); }));
  self.skipWaiting();
});

self.addEventListener("activate", function (ereignis) {
  ereignis.waitUntil(
    caches.keys().then(function (namen) {
      return Promise.all(namen.filter(function (n) { return n !== CACHE; }).map(function (n) {
        return caches.delete(n);
      }));
    })
  );
  self.clients.claim();
});

self.addEventListener("fetch", function (ereignis) {
  var url = new URL(ereignis.request.url);
  if (ereignis.request.method !== "GET" || !url.pathname.startsWith("/static/")) return;
  ereignis.respondWith(
    caches.match(ereignis.request).then(function (treffer) {
      return treffer || fetch(ereignis.request);
    })
  );
});
