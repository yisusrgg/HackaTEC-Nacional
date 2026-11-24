// Tablero en tiempo real: consulta /api/v1/tablero/estado/ cada 2 s y actualiza las tarjetas.
(() => {
  "use strict";

  const config = JSON.parse(document.getElementById("config-resumen").textContent);

  // Las clases completas van escritas aquí para que Tailwind las incluya al compilar.
  const CLASE_NIVEL = {
    ok: "border-l-emerald-500",
    alerta: "border-l-amber-500",
    falla: "border-l-rose-500",
    desactualizado: "border-l-slate-400",
    sin_datos: "border-l-slate-300",
  };
  const CLASES_NIVEL = Object.values(CLASE_NIVEL);
  const PASTILLA_EN_LINEA = ["bg-emerald-100", "text-emerald-700"];
  const PASTILLA_FUERA = ["bg-rose-100", "text-rose-700"];
  const PASTILLA_NEUTRA = ["bg-slate-100", "text-slate-600"];

  const porId = (id) => document.getElementById(id);
  const poner = (id, texto) => {
    const elemento = porId(id);
    if (elemento) elemento.textContent = texto ?? "—";
  };

  function haceCuanto(iso) {
    if (!iso) return "Sin datos todavía";
    const segundos = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
    if (segundos < 60) return `hace ${segundos} s`;
    const minutos = Math.round(segundos / 60);
    if (minutos < 60) return `hace ${minutos} min`;
    return `hace ${Math.round(minutos / 60)} h`;
  }

  function pastilla(id, enLinea, textoSi, textoNo) {
    const elemento = porId(id);
    if (!elemento) return;
    elemento.classList.remove(...PASTILLA_EN_LINEA, ...PASTILLA_FUERA, ...PASTILLA_NEUTRA);
    elemento.classList.add(...(enLinea ? PASTILLA_EN_LINEA : PASTILLA_FUERA));
    elemento.textContent = enLinea ? textoSi : textoNo;
  }

  function pintarSenales(senales) {
    for (const senal of senales) {
      const tarjeta = document.querySelector(`[data-senal="${CSS.escape(senal.codigo)}"]`);
      if (!tarjeta) continue;
      tarjeta.querySelector('[data-campo="texto"]').textContent = senal.texto;
      tarjeta.querySelector('[data-campo="estado"]').textContent =
        senal.nivel === "falla" ? `Sensor con falla · ${haceCuanto(senal.ts)}` : haceCuanto(senal.ts);
      tarjeta.classList.remove(...CLASES_NIVEL);
      tarjeta.classList.add(CLASE_NIVEL[senal.nivel] || CLASE_NIVEL.sin_datos);
    }
  }

  function pintarSistema(datos) {
    const gateway = datos.gateway;
    if (gateway) {
      const sinContacto = gateway.ultimo_contacto ? `Sin contacto · ${haceCuanto(gateway.ultimo_contacto)}` : "Sin contacto";
      pastilla("gw-pastilla", gateway.en_linea, "En línea", gateway.activo ? sinContacto : "Revocado");
      const estado = gateway.estado || {};
      poner("gw-rssi", estado.rssi !== undefined ? `${estado.rssi} dBm` : "—");
      poner("gw-cola", estado.cola !== undefined ? `${estado.cola} lotes` : "—");
      poner("can-estado", estado.can_estado || "—");
      poner("can-errores", estado.can_err_rx !== undefined ? `${estado.can_err_rx} / ${estado.can_err_tx}` : "—");
      poner("can-bus", estado.can_bus_err !== undefined ? `${estado.can_bus_err} errores de bus` : "—");
    }
    const nodo = datos.nodo;
    if (nodo) {
      poner("nodo-perdida", `${nodo.porcentaje_perdida.toFixed(2)} % pérdida`);
      poner("nodo-recibidas", nodo.recibidas.toLocaleString("es-MX"));
      poner("nodo-rechazadas", nodo.rechazadas.toLocaleString("es-MX"));
      poner("nodo-reinicios", String(nodo.reinicios));
    }
    poner("alertas-activas", String(datos.alertas_activas));
    poner("eventos-criticos", String(datos.eventos_criticos_24h));
  }

  async function refrescar() {
    try {
      const respuesta = await fetch(config.urlEstado, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (respuesta.status === 401 || respuesta.status === 403) {
        window.location.reload(); // la sesión expiró: el servidor redirige al login
        return;
      }
      if (!respuesta.ok) throw new Error(`HTTP ${respuesta.status}`);
      const datos = await respuesta.json();
      pintarSenales(datos.senales);
      pintarSistema(datos);
      poner("ultima-actualizacion", new Date().toLocaleTimeString("es-MX"));
      porId("aviso-conexion")?.classList.add("hidden");
    } catch (error) {
      porId("aviso-conexion")?.classList.remove("hidden");
    }
  }

  refrescar();
  setInterval(refrescar, config.intervaloMs);
})();
