// Detalle de una señal: gráfica con Chart.js (servido localmente desde /static/vendor), estadísticas y umbrales.
(() => {
  "use strict";

  const cfg = JSON.parse(document.getElementById("config-senal").textContent);
  const aviso = document.getElementById("aviso-grafica");
  const botones = document.querySelectorAll("[data-minutos]");
  const enlaceExportar = document.getElementById("enlace-exportar");
  const esBooleana = cfg.tipo === "BOOL";
  let minutos = 60;
  let temporizador = null;

  const mostrarAviso = (texto) => { aviso.textContent = texto; aviso.classList.remove("hidden"); };
  const ocultarAviso = () => aviso.classList.add("hidden");
  const poner = (id, texto) => { const el = document.getElementById(id); if (el) el.textContent = texto; };

  function formatoValor(valor) {
    if (valor === null || valor === undefined) return "—";
    const etiqueta = cfg.etiquetas[String(Math.round(valor))];
    if (etiqueta) return etiqueta;
    if (esBooleana) return valor >= 0.5 ? "Sí" : "No";
    return `${Number(valor).toFixed(cfg.decimales)} ${cfg.unidad}`.trim();
  }

  function formatoHora(ms) {
    const fecha = new Date(ms);
    const hora = fecha.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", second: minutos <= 60 ? "2-digit" : undefined });
    return minutos > 1440 ? `${fecha.toLocaleDateString("es-MX", { day: "2-digit", month: "short" })} ${hora}` : hora;
  }

  if (typeof Chart === "undefined") {
    mostrarAviso("Falta Chart.js: ejecuta «npm install» y «npm run build» dentro de la carpeta backend.");
    return;
  }
  if (cfg.nodo === null) {
    mostrarAviso("No hay nodos registrados todavía.");
    return;
  }

  const lineaUmbral = (etiqueta) => ({
    label: etiqueta, data: [], borderColor: "#f59e0b", borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, fill: false,
  });

  const grafica = new Chart(document.getElementById("grafica"), {
    type: "line",
    data: {
      datasets: [
        {
          label: cfg.nombre, data: [], borderColor: "#1d4ed8", backgroundColor: "rgba(29, 78, 216, 0.08)", fill: true,
          borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: esBooleana ? 0 : 0.25, stepped: esBooleana ? "before" : false,
        },
        lineaUmbral("Umbral alto"),
        lineaUmbral("Umbral bajo"),
      ],
    },
    options: {
      animation: false,
      parsing: false,
      normalized: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: { type: "linear", ticks: { maxTicksLimit: 8, callback: (valor) => formatoHora(valor) }, grid: { color: "#f1f5f9" } },
        y: esBooleana
          ? { min: -0.1, max: 1.1, ticks: { stepSize: 1, callback: (v) => (v === 0 || v === 1 ? formatoValor(v) : "") } }
          : { grace: "5%", ticks: { callback: (v) => `${v} ${cfg.unidad}`.trim() }, grid: { color: "#f1f5f9" } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: (item) => item.datasetIndex === 0,
          callbacks: {
            title: (items) => (items.length ? new Date(items[0].parsed.x).toLocaleString("es-MX") : ""),
            label: (item) => formatoValor(item.parsed.y),
          },
        },
      },
    },
  });

  const lineaHorizontal = (valor, desde, hasta) => (valor === null ? [] : [{ x: desde, y: valor }, { x: hasta, y: valor }]);

  // Si dejaron de llegar datos un rato (nodo apagado, sin red), corta la línea en vez de unir los puntos.
  function conHuecos(puntos, desde, hasta) {
    const huecoMaximo = Math.max(60_000, (hasta - desde) * 0.05);
    const resultado = [];
    puntos.forEach(([x, y], i) => {
      if (i > 0 && x - puntos[i - 1][0] > huecoMaximo) resultado.push({ x: (x + puntos[i - 1][0]) / 2, y: null });
      resultado.push({ x, y });
    });
    return resultado;
  }

  async function cargar() {
    try {
      const url = `${cfg.urlSerie}?nodo=${encodeURIComponent(cfg.nodo)}&minutos=${minutos}`;
      const respuesta = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (respuesta.status === 401 || respuesta.status === 403) { window.location.reload(); return; }
      if (!respuesta.ok) throw new Error(`HTTP ${respuesta.status}`);
      const datos = await respuesta.json();

      const puntos = datos.puntos.map(([x, y]) => ({ x, y }));
      grafica.data.datasets[0].data = conHuecos(datos.puntos, datos.desde, datos.hasta);
      grafica.data.datasets[1].data = lineaHorizontal(datos.umbral_alto, datos.desde, datos.hasta);
      grafica.data.datasets[2].data = lineaHorizontal(datos.umbral_bajo, datos.desde, datos.hasta);
      grafica.options.scales.x.min = datos.desde;
      grafica.options.scales.x.max = datos.hasta;
      grafica.update();

      const e = datos.estadisticas;
      poner("est-ultimo", puntos.length ? formatoValor(puntos[puntos.length - 1].y) : "—");
      poner("est-min", formatoValor(e.min));
      poner("est-max", formatoValor(e.max));
      poner("est-prom-etiqueta", esBooleana ? "Tiempo activo" : "Promedio");
      poner("est-prom", e.promedio === null ? "—" : esBooleana ? `${Math.round(e.promedio * 100)} %` : formatoValor(e.promedio));
      poner("est-n", e.n.toLocaleString("es-MX"));
      if (puntos.length) ocultarAviso(); else mostrarAviso("Sin lecturas en este intervalo.");
    } catch (error) {
      mostrarAviso("No se pudieron cargar los datos; reintentando…");
    }
  }

  function seleccionar(nuevo) {
    minutos = nuevo;
    botones.forEach((boton) => {
      const activo = Number(boton.dataset.minutos) === minutos;
      boton.classList.toggle("btn-primario", activo);
      boton.classList.toggle("btn-secundario", !activo);
    });
    if (enlaceExportar) {
      enlaceExportar.href = `${cfg.urlExportar}?senal=${encodeURIComponent(cfg.codigo)}&nodo=${encodeURIComponent(cfg.nodo)}&minutos=${minutos}`;
    }
    clearInterval(temporizador);
    cargar();
    if (minutos <= 360) temporizador = setInterval(cargar, 5000);
  }

  botones.forEach((boton) => boton.addEventListener("click", () => seleccionar(Number(boton.dataset.minutos))));
  seleccionar(60);
})();
