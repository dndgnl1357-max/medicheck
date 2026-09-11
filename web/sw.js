// 서비스 워커 — 앱 껍데기만 캐시한다.
//
// **분석 결과는 절대 캐시하지 않는다.** 복약 데이터는 갱신되고, 오래된 위험도를
// 최신인 것처럼 보여주면 서비스의 존재 이유가 무너진다. /api/* 는 항상 네트워크로 가고,
// 오프라인이면 화면이 "지금은 확인할 수 없다" 고 말한다. 조용히 옛 답을 주지 않는다.
//
// (README 의 오프라인 우선 설계는 앱에 SQLite 를 내장하는 방식이다. 지금 PWA 는
//  껍데기만 오프라인이고 분석에는 서버가 필요하다.)

const SHELL = "medicheck-shell-v1";
const SHELL_FILES = ["/", "/index.html", "/manifest.json", "/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return;   // 분석은 언제나 네트워크
  if (e.request.method !== "GET") return;

  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(e.request, copy));
        return res;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("/index.html")))
  );
});
