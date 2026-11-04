// The page itself works without the laptop (it says the laptop is unreachable); every
// question goes to the laptop or nowhere -- nothing is queued and sent later on its own.
'use strict';
const SHELL = 'jarvis-pocket-v2';
const FILES = ['/', '/app.js', '/manifest.webmanifest', '/icon.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(FILES)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((key) => key !== SHELL).map((key) => caches.delete(key)))));
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/')) return;           // always the network
  event.respondWith(fetch(event.request)
    .then((response) => {
      const copy = response.clone();
      caches.open(SHELL).then((cache) => cache.put(event.request, copy));
      return response;
    })
    .catch(() => caches.match(event.request)));
});
