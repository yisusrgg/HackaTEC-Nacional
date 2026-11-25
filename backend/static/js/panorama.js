// Panorama: gráfica de fallas por semana (apilada por subsistema) y recarga cada 60 s mientras se ve la pestaña.
(() => {
  "use strict";

  setInterval(() => {
    if (document.visibilityState === "visible") window.location.reload();
  }, 60000);

  const nodo = document.getElementById("datos-panorama");
  const lienzo = document.getElementById("grafica-fallas");
  if (!nodo || !lienzo || typeof Chart === "undefined") return;
  const datos = JSON.parse(nodo.textContent);

  const COLOR = { tinta: "#141414", rejilla: "#efefee", texto: "#6a6a67" };
  // Un tono por subsistema, del más oscuro al más claro (se distinguen también en escala de grises).
  const TONOS = { AC: "#1c5fa8", SAN: "#141414", LUZ: "#9a5b00", ELEC: "#9a9a96" };
  Chart.defaults.font.family = '"Inter", "Segoe UI", Arial, sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = COLOR.texto;

  const semana = (iso) => {
    const [a, m, d] = iso.split("-").map(Number);
    return new Date(a, m - 1, d).toLocaleDateString("es-MX", { day: "2-digit", month: "short" });
  };

  new Chart(lienzo, {
    type: "bar",
    data: {
      labels: datos.semanas.map(semana),
      datasets: datos.series.map((s) => ({
        label: s.nombre, data: s.datos, backgroundColor: TONOS[s.codigo] || COLOR.tinta, borderRadius: 2, maxBarThickness: 28,
      })),
    },
    options: {
      animation: false, maintainAspectRatio: false,
      scales: {
        x: { stacked: true, border: { display: false }, grid: { display: false }, ticks: { maxRotation: 0 } },
        y: { stacked: true, beginAtZero: true, border: { display: false }, grid: { color: COLOR.rejilla }, ticks: { precision: 0 } },
      },
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10 } },
        tooltip: {
          backgroundColor: COLOR.tinta, cornerRadius: 4, padding: 10,
          callbacks: { title: (items) => `Semana del ${items[0].label}` },
        },
      },
    },
  });
})();
