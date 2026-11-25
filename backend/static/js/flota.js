// Resumen de la flota: recarga la página cada 30 s mientras la pestaña está visible.
(() => {
  "use strict";
  const INTERVALO_MS = 30000;
  setInterval(() => {
    if (document.visibilityState === "visible") window.location.reload();
  }, INTERVALO_MS);
})();
