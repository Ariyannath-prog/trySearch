(function registerPwa() {
  if (!('serviceWorker' in navigator)) return;
  window.addEventListener('load', () => {
    // updateViaCache:'none' keeps the HTTP cache (and any edge-injected max-age
    // on /sw.js) out of the update check, so a new worker is picked up promptly.
    navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' }).then((reg) => {
      reg.update();
    }).catch(() => {
      // The website remains fully usable if a browser blocks service workers.
    });
  });
})();
