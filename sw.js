const CACHE_NAME = 'friday-app-v1';
const ASSETS_TO_CACHE = [
  '/',
  '/manifest.json',
  'https://cdn.jsdelivr.net/npm/marked/marked.min.js',
  'https://www.youtube.com/iframe_api'
];

// Instala o Service Worker e faz o cache dos arquivos base
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
  self.skipWaiting();
});

// Limpa caches antigos quando há atualização
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) return caches.delete(key);
        })
      );
    })
  );
  self.clients.claim();
});

// Intercepta as requisições
self.addEventListener('fetch', (event) => {
  // Não fazemos cache de APIs dinâmicas (chat, status, tts)
  if (event.request.url.includes('/chat') || event.request.url.includes('/status') || event.request.url.includes('/tts')) {
    return; 
  }
  
  // Para o resto (HTML, JS), tenta o cache primeiro, depois a rede
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    })
  );
});
