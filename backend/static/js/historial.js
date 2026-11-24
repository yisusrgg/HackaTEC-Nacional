// Historial de datos: gráfica de una señal de un nodo en el periodo consultado.
(() => {
  "use strict";

  const datos = JSON.parse(document.getElementById("datos-historial").textContent);
  const lienzo = document.getElementById("grafica-historial");
  if (!datos || !lienzo || typeof Chart === "undefined") return;

  const COLOR = { tinta: "#141414", relleno: "rgba(20, 20, 20, 0.06)", umbral: "#b3261e", rejilla: "#efefee", texto: "#6a6a67" };
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
  const horizontal = (valor) => (valor === null ? [] : [{ x: datos.desde, y: valor }, { x: datos.hasta, y: valor }]);
  const umbral = (valor) => ({ data: horizontal(valor), borderColor: COLOR.umbral, borderDash: [4, 4], borderWidth: 1, pointRadius: 0 });
  const sinNegativos = [...datos.puntos.map(([, y]) => y), datos.umbral_alto, datos.umbral_bajo]
    .filter((v) => v !== null && v !== undefined).every((v) => v >= 0);

  // Si dejaron de llegar datos un rato, corta la línea en vez de unir los puntos.
  const huecoMaximo = Math.max(10 * 60_000, (datos.hasta - datos.desde) * 0.02);
  const puntos = [];
  datos.puntos.forEach(([x, y], i) => {
    if (i > 0 && x - datos.puntos[i - 1][0] > huecoMaximo) puntos.push({ x: x - 1, y: null });
    puntos.push({ x, y });
  });

  new Chart(lienzo, {
    type: "line",
    data: {
      datasets: [
        { data: puntos, borderColor: COLOR.tinta, backgroundColor: COLOR.relleno, fill: true, borderWidth: 1.5, pointRadius: 0,
          pointHoverRadius: 3, tension: esBooleana ? 0 : 0.2, stepped: esBooleana ? "before" : false },
        umbral(datos.umbral_alto), umbral(datos.umbral_bajo),
      ],
    },
    options: {
      animation: false, parsing: false, normalized: true, maintainAspectRatio: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: { type: "linear", min: datos.desde, max: datos.hasta, border: { display: false }, grid: { color: COLOR.rejilla },
             ticks: { maxTicksLimit: 7, callback: (v) => hora(v) } },
        y: esBooleana
          ? { min: -0.1, max: 1.1, border: { display: false }, grid: { color: COLOR.rejilla }, ticks: { stepSize: 1, callback: (v) => (v === 0 || v === 1 ? formato(v) : "") } }
          : { grace: "10%", min: sinNegativos ? 0 : undefined, border: { display: false }, grid: { color: COLOR.rejilla }, ticks: { callback: (v) => `${v} ${datos.unidad}`.trim() } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: COLOR.tinta, cornerRadius: 4, padding: 10, displayColors: false,
          filter: (item) => item.datasetIndex === 0,
          callbacks: { title: (items) => (items.length ? hora(items[0].parsed.x) : ""), label: (item) => formato(item.parsed.y) },
        },
      },
    },
  });
})();