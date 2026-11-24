// Detalle de una orden: lecturas de la señal antes (gris) y después (negro) de la reparación, con sus umbrales.
(() => {
  "use strict";

  const datos = JSON.parse(document.getElementById("datos-orden").textContent);
  const lienzo = document.getElementById("grafica-orden");
  if (!datos || !lienzo || typeof Chart === "undefined") return;

  const COLOR = { tinta: "#141414", antes: "#9a9a96", umbral: "#b3261e", rejilla: "#efefee", texto: "#6a6a67" };
  Chart.defaults.font.family = '"Inter", "Segoe UI", Arial, sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = COLOR.texto;

  const esBooleana = datos.tipo === "BOOL";
  const formato = (valor) => {
    if (valor === null || valor === undefined) return "—";
    const etiqueta = (datos.etiquetas || {})[String(Math.round(valor))];
    if (etiqueta) return etiqueta;
    return `${Number(valor).toFixed(datos.decimales)} ${datos.unidad}`.trim();
  };
  const hora = (ms) => new Date(ms).toLocaleString("es-MX", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

  const corte = datos.reparacion ?? Infinity;
  const antes = datos.puntos.filter(([x]) => x < corte).map(([x, y]) => ({ x, y }));
  const despues = datos.puntos.filter(([x]) => x >= corte).map(([x, y]) => ({ x, y }));
  const horizontal = (valor) => (valor === null ? [] : [{ x: datos.desde, y: valor }, { x: datos.hasta, y: valor }]);
  const serie = (puntos, color) => ({
    data: puntos, borderColor: color, borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 3, fill: false,
    tension: esBooleana ? 0 : 0.25, stepped: esBooleana ? "before" : false,
  });
  // Si ningún valor ni umbral es negativo, el eje empieza en 0 (no hay rpm ni porcentajes negativos).
  const sinNegativos = [...datos.puntos.map(([, y]) => y), datos.umbral_alto, datos.umbral_bajo]
    .filter((v) => v !== null && v !== undefined).every((v) => v >= 0);
  const umbral = (valor) => ({ data: horizontal(valor), borderColor: COLOR.umbral, borderDash: [4, 4], borderWidth: 1, pointRadius: 0 });

  // Línea vertical en el momento en que se registró la reparación.
  const marcaReparacion = {
    id: "marcaReparacion",
    afterDatasetsDraw(grafica) {
      if (!datos.reparacion) return;
      const { ctx, chartArea, scales } = grafica;
      const x = scales.x.getPixelForValue(datos.reparacion);
      if (x < chartArea.left || x > chartArea.right) return;
      ctx.save();
      ctx.strokeStyle = COLOR.tinta;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, chartArea.top);
      ctx.lineTo(x, chartArea.bottom);
      ctx.stroke();
      ctx.fillStyle = COLOR.tinta;
      ctx.fillText("Reparación", x + 6, chartArea.top + 12);
      ctx.restore();
    },
  };

  new Chart(lienzo, {
    type: "line",
    data: { datasets: [serie(antes, COLOR.antes), serie(despues, COLOR.tinta), umbral(datos.umbral_alto), umbral(datos.umbral_bajo)] },
    options: {
      animation: false, parsing: false, normalized: true, maintainAspectRatio: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: {
          type: "linear", min: datos.desde, max: datos.hasta, border: { display: false }, grid: { color: COLOR.rejilla },
          ticks: { maxTicksLimit: 6, callback: (v) => hora(v) },
        },
        y: esBooleana
          ? { min: -0.1, max: 1.1, border: { display: false }, grid: { color: COLOR.rejilla }, ticks: { stepSize: 1, callback: (v) => (v === 0 || v === 1 ? formato(v) : "") } }
          : { grace: "10%", min: sinNegativos ? 0 : undefined, border: { display: false }, grid: { color: COLOR.rejilla }, ticks: { callback: (v) => `${v} ${datos.unidad}`.trim() } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: COLOR.tinta, cornerRadius: 4, padding: 10, displayColors: false,
          filter: (item) => item.datasetIndex < 2,
          callbacks: { title: (items) => (items.length ? hora(items[0].parsed.x) : ""), label: (item) => formato(item.parsed.y) },
        },
      },
    },
    plugins: [marcaReparacion],
  });
})();