// Comportamientos compartidos. No hay JavaScript en línea en las plantillas porque la política CSP lo bloquea.
(() => {
  "use strict";

  // <form data-confirmar="¿Seguro?"> pide confirmación antes de enviar (rotar clave, revocar).
  document.addEventListener("submit", (evento) => {
    const mensaje = evento.target.dataset?.confirmar;
    if (mensaje && !window.confirm(mensaje)) evento.preventDefault();
  });

  // <a data-regresar href="..."> vuelve a la pantalla anterior si vino de otra página de este sitio;
  // si no (entrada directa, o la anterior es esta misma tras enviar un formulario), sigue su href.
  document.addEventListener("click", (evento) => {
    const enlace = evento.target.closest("[data-regresar]");
    if (!enlace || evento.ctrlKey || evento.metaKey || evento.shiftKey) return;
    let anterior = null;
    try { anterior = document.referrer ? new URL(document.referrer) : null; } catch { anterior = null; }
    const mismoSitio = anterior && anterior.origin === window.location.origin;
    const mismaPantalla = anterior && anterior.pathname === window.location.pathname;
    if (mismoSitio && !mismaPantalla && window.history.length > 1) {
      evento.preventDefault();
      window.history.back();
    }
  });

  // <select data-autoenviar> envía su formulario al cambiar de opción.
  document.addEventListener("change", (evento) => {
    if (evento.target.matches("[data-autoenviar]")) evento.target.form.submit();
  });

  // <input data-autoenviar-texto> envía su formulario cuando se deja de escribir (sin recargar con cada letra).
  // Al recargar, el cursor vuelve al mismo campo para seguir escribiendo.
  const ESPERA_MS = 500;
  let temporizador;
  const enviarTexto = (campo) => {
    clearTimeout(temporizador);
    if (campo.value === campo.defaultValue) return;  // nada cambió (p. ej. se escribió y se borró)
    sessionStorage.setItem("enfocar", campo.id);
    campo.form.submit();
  };
  document.addEventListener("input", (evento) => {
    const campo = evento.target;
    if (!campo.matches("[data-autoenviar-texto]")) return;
    clearTimeout(temporizador);
    temporizador = setTimeout(() => enviarTexto(campo), ESPERA_MS);
  });
  document.addEventListener("submit", (evento) => {
    // Enter dentro del campo: enviar ya, sin esperar el temporizador.
    clearTimeout(temporizador);
    const campo = evento.target.querySelector("[data-autoenviar-texto]");
    if (campo && document.activeElement === campo) sessionStorage.setItem("enfocar", campo.id);
  });
  const enfocar = sessionStorage.getItem("enfocar");
  if (enfocar) {
    sessionStorage.removeItem("enfocar");
    const campo = document.getElementById(enfocar);
    if (campo) {
      campo.focus();
      campo.setSelectionRange(campo.value.length, campo.value.length);
    }
  }

  // <button data-copiar="#id"> copia el texto del elemento indicado.
  document.addEventListener("click", async (evento) => {
    const boton = evento.target.closest("[data-copiar]");
    if (!boton) return;
    const origen = document.querySelector(boton.dataset.copiar);
    if (!origen) return;
    try {
      await navigator.clipboard.writeText(origen.textContent.trim());
      const texto = boton.textContent;
      boton.textContent = "¡Copiado!";
      setTimeout(() => { boton.textContent = texto; }, 1500);
    } catch {
      window.getSelection().selectAllChildren(origen);
    }
  });
})();
