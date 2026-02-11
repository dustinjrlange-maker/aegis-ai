/**
 * Aegis AI — Service Worker
 * Provides offline caching for the PWA shell and static assets.
 * API calls are network-first (not cached).
 */

const CACHE_NAME = 'aegis-v2';
const SHELL_ASSETS = [
    '/',
    '/static/icon-192.png',
    '/static/icon-512.png',
    '/manifest.json',
];

// CDN assets to cache
const CDN_ASSETS = [
    'https://cdn.jsdelivr.net/npm/marked/marked.min.js',
    'https://fonts.googleapis.com/css2?family=Antonio:wght@400;600;700&family=Chakra+Petch:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap',
];

// Install: cache shell assets
self.addEventListener('install', function(event) {
    event.waitUntil(
        caches.open(CACHE_NAME).then(function(cache) {
            // Cache local assets (fail gracefully for CDN)
            return cache.addAll(SHELL_ASSETS).then(function() {
                // Try to cache CDN assets but don't block install on failure
                return Promise.allSettled(
                    CDN_ASSETS.map(function(url) {
                        return cache.add(url).catch(function() {});
                    })
                );
            });
        })
    );
    self.skipWaiting();
});

// Activate: clean old caches
self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys().then(function(keys) {
            return Promise.all(
                keys.filter(function(k) { return k !== CACHE_NAME; })
                    .map(function(k) { return caches.delete(k); })
            );
        })
    );
    self.clients.claim();
});

// Fetch: network-first for API, cache-first for assets
self.addEventListener('fetch', function(event) {
    const url = new URL(event.request.url);

    // Skip non-GET requests
    if (event.request.method !== 'GET') return;

    // API requests: network-first, no caching
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(
            fetch(event.request).catch(function() {
                return new Response(
                    JSON.stringify({ error: 'Offline' }),
                    { status: 503, headers: { 'Content-Type': 'application/json' } }
                );
            })
        );
        return;
    }

    // Navigation requests (HTML pages): network-first so code updates are always fresh
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request).then(function(response) {
                if (response.ok) {
                    var responseClone = response.clone();
                    caches.open(CACHE_NAME).then(function(cache) {
                        cache.put(event.request, responseClone);
                    });
                }
                return response;
            }).catch(function() {
                return caches.match('/') || new Response('Offline', { status: 503 });
            })
        );
        return;
    }

    // Static assets: cache-first with network fallback
    event.respondWith(
        caches.match(event.request).then(function(cached) {
            if (cached) return cached;

            return fetch(event.request).then(function(response) {
                if (response.ok && (
                    url.pathname.startsWith('/static/') ||
                    url.pathname === '/manifest.json' ||
                    url.hostname !== self.location.hostname
                )) {
                    var responseClone = response.clone();
                    caches.open(CACHE_NAME).then(function(cache) {
                        cache.put(event.request, responseClone);
                    });
                }
                return response;
            }).catch(function() {
                return new Response('Offline', { status: 503 });
            });
        })
    );
});
