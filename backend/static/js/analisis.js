// Análisis de calidad: gráfica de fallas por día.
(() => {
  "use strict";

  const datos = JSON.parse(document.getElementById("datos-analisis").textContent);
  const lienzo = document.getElementById("grafica-tendencia");
  if (!lienzo || typeof Chart === "undefined") return;

  const COLOR = { tinta: "#141414", rejilla: "#efefee", texto: "#6a6a67" };
  Chart.defaults.font.family = '"Inter", "Segoe UI", Arial, sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = COLOR.texto;

  // "2026-11-24" → 24 nov (sin desfase por zona horaria)
  const fecha = (iso) => {
    const [a, m, d] = iso.split("-").map(Number);
    return new Date(a, m - 1, d).toLocaleDateString("es-MX", { day: "2-digit", month: "short" });
  };

  new Chart(lienzo, {
    type: "bar",
    data: {
      labels: datos.tendencia.map((d) => fecha(d.dia)),
      datasets: [{ data: datos.tendencia.map((d) => d.n), backgroundColor: COLOR.tinta, borderRadius: 2, maxBarThickness: 18 }],
    },
    options: {
      animation: false, maintainAspectRatio: false,
      scales: {
        x: { border: { display: false }, grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0 } },
        y: { beginAtZero: true, border: { display: false }, grid: { color: COLOR.rejilla }, ticks: { precision: 0 } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: COLOR.tinta, cornerRadius: 4, padding: 10, displayColors: false,
          callbacks: { label: (item) => `${item.parsed.y} ${item.parsed.y === 1 ? "falla" : "fallas"}` },
        },
      },
    },
  });
})();