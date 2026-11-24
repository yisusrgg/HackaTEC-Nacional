// Tablero en tiempo real: consulta /api/v1/tablero/estado/ cada 2 s y actualiza las lecturas.
(() => {
  "use strict";

  const config = JSON.parse(document.getElementById("config-resumen").textContent);

  // Clase CSS de cada lectura según su nivel (definidas en estilos.css, sección 16).
  const CLASE_NIVEL = {
    ok: "nivel-ok",
    alerta: "nivel-alerta",
    falla: "nivel-falla",
    desactualizado: "nivel-desactualizado",
    sin_datos: "nivel-sin-datos",
  };
  const CLASES_NIVEL = Object.values(CLASE_NIVEL);

  // Estado de cada subsistema: se muestra el nivel más grave de sus lecturas.
  const GRAVEDAD = ["sin_datos", "ok", "desactualizado", "alerta", "falla"];
  const ESTADO_SUBSISTEMA = {
    sin_datos: ["etiqueta-apagado", "Sin datos"],
    ok: ["etiqueta-ok", "Normal"],
    desactualizado: ["etiqueta-apagado", "Desactualizado"],
    alerta: ["etiqueta-aviso", "Con alerta"],
    falla: ["etiqueta-critico", "Falla de sensor"],
  };
  const CLASES_ETIQUETA = ["etiqueta-ok", "etiqueta-aviso", "etiqueta-critico", "etiqueta-apagado"];

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

  function etiqueta(elemento, clase, texto) {
    if (!elemento) return;
    elemento.classList.remove(...CLASES_ETIQUETA);
    elemento.classList.add(clase);
    elemento.textContent = texto;
  }

  function pintarSenales(senales) {
    for (const senal of senales) {
      const lectura = document.querySelector(`[data-senal="${CSS.escape(senal.codigo)}"]`);
      if (!lectura) continue;
      lectura.querySelector('[data-campo="texto"]').textContent = senal.texto;
      lectura.querySelector('[data-campo="estado"]').textContent =
        senal.nivel === "falla" ? `Sensor con falla · ${haceCuanto(senal.ts)}` : haceCuanto(senal.ts);
      lectura.classList.remove(...CLASES_NIVEL);
      lectura.classList.add(CLASE_NIVEL[senal.nivel] || CLASE_NIVEL.sin_datos);
      lectura.dataset.nivel = senal.nivel in CLASE_NIVEL ? senal.nivel : "sin_datos";
    }

    for (const seccion of document.querySelectorAll("[data-subsistema]")) {
      let peor = "sin_datos";
      for (const lectura of seccion.querySelectorAll("[data-senal]")) {
        const nivel = lectura.dataset.nivel || "sin_datos";
        if (GRAVEDAD.indexOf(nivel) > GRAVEDAD.indexOf(peor)) peor = nivel;
      }
      const [clase, texto] = ESTADO_SUBSISTEMA[peor];
      etiqueta(seccion.querySelector("[data-estado-subsistema]"), clase, texto);
    }
  }

  function pintarSistema(datos) {
    const gateway = datos.gateway;
    if (gateway) {
      const pastilla = porId("gw-pastilla");
      if (gateway.en_linea) {
        etiqueta(pastilla, "etiqueta-ok", "En línea");
      } else if (!gateway.activo) {
        etiqueta(pastilla, "etiqueta-critico", "Revocado");
      } else {
        const texto = gateway.ultimo_contacto ? `Sin contacto · ${haceCuanto(gateway.ultimo_contacto)}` : "Sin contacto";
        etiqueta(pastilla, "etiqueta-critico", texto);
      }
      const estado = gateway.estado || {};
      poner("gw-rssi", estado.rssi !== undefined ? `${estado.rssi} dBm` : "—");
      poner("gw-cola", estado.cola !== undefined ? `${estado.cola} lotes` : "—");
      poner("can-estado", estado.can_estado || "—");
      poner("can-errores", estado.can_err_rx !== undefined ? `${estado.can_err_rx} / ${estado.can_err_tx}` : "—");
      poner("can-bus", estado.can_bus_err !== undefined ? `${estado.can_bus_err} errores de bus` : "—");
    }
    const nodo = datos.nodo;
    if (nodo) {
      poner("nodo-perdida", nodo.porcentaje_perdida.toFixed(2));
      poner("nodo-recibidas", nodo.recibidas.toLocaleString("es-MX"));
      poner("nodo-rechazadas", nodo.rechazadas.toLocaleString("es-MX"));
      poner("nodo-reinicios", String(nodo.reinicios));
    }
    poner("alertas-activas", String(datos.alertas_activas));
    porId("alertas-activas")?.classList.toggle("cifra-critico", datos.alertas_activas > 0);
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
      const aviso = porId("aviso-conexion");
      if (aviso) aviso.hidden = true;
    } catch (error) {
      const aviso = porId("aviso-conexion");
      if (aviso) aviso.hidden = false;
    }
  }

  refrescar();
  setInterval(refrescar, config.intervaloMs);
})();