// 최소 서비스워커: PWA 설치(및 공유 대상 등록) 자격을 위한 것.
// 네트워크는 그대로 통과시킨다.
self.addEventListener('install', function(e){ self.skipWaiting(); });
self.addEventListener('activate', function(e){ e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function(e){ /* passthrough */ });
