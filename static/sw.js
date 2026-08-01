// 미너비니 스크리너 서비스워커 — 설치형 PWA용.
// 전략: 페이지(HTML)는 네트워크 우선(항상 최신 시세), 정적 자원은 캐시 우선,
//       오프라인이면 폴백 페이지. 아이콘 등 자원 버전이 바뀌면 CACHE 값을 올린다.
const CACHE = 'minervini-v1';
const CORE = [
  '/static/offline.html',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(CORE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;                 // POST(알림 등)는 그대로 통과
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;       // 외부(차트 CDN 등)는 건드리지 않음

  // 페이지 이동: 네트워크 우선 → 실패하면 오프라인 폴백
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match('/static/offline.html')));
    return;
  }

  // 정적 자원(/static/*): 캐시 우선 + 백그라운드 갱신
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(req).then((cached) => {
        const net = fetch(req).then((res) => {
          if (res && res.status === 200) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        }).catch(() => cached);
        return cached || net;
      })
    );
  }
});
