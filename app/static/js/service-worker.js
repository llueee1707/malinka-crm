const CACHE_NAME = "salon-crm-v39";
const OFFLINE_ASSETS = [
    "/",
    "/login",
    "/manifest.webmanifest?v=20260507-1",
    "/static/css/app.css?v=20260507-1",
    "/static/css/mobile.css?v=20260507-1",
    "/static/js/app.js?v=20260507-1",
    "/static/images/salon-hero.jpg",
    "/static/icons/apple-touch-icon.png",
    "/static/icons/icon-192.png",
    "/static/icons/icon-512.png",
    "/static/icons/icon.svg",
];

function isCacheableStaticAsset(requestUrl) {
    return requestUrl.origin === self.location.origin && requestUrl.pathname.startsWith("/static/");
}

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(OFFLINE_ASSETS)).then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) =>
            Promise.all(
                cacheNames
                    .filter((cacheName) => cacheName !== CACHE_NAME)
                    .map((cacheName) => caches.delete(cacheName))
            )
        ).then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    if (event.request.method !== "GET") {
        return;
    }

    const requestUrl = new URL(event.request.url);

    if (event.request.mode === "navigate") {
        event.respondWith(
            fetch(event.request).catch(async () => {
                const cachedResponse = await caches.match(event.request);
                return cachedResponse || caches.match("/login");
            })
        );
        return;
    }

    if (!isCacheableStaticAsset(requestUrl)) {
        return;
    }

    event.respondWith(
        fetch(event.request).then(async (networkResponse) => {
            if (!networkResponse || networkResponse.status !== 200 || networkResponse.type !== "basic") {
                return networkResponse;
            }

            const responseClone = networkResponse.clone();
            const cache = await caches.open(CACHE_NAME);
            await cache.put(event.request, responseClone);
            return networkResponse;
        }).catch(() => caches.match(event.request))
    );
});
