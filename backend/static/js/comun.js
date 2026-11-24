// Comportamientos compartidos. No hay JavaScript en línea en las plantillas porque la política CSP lo bloquea.
(() => {
  "use strict";

  // <form data-confirmar="¿Seguro?"> pide confirmación antes de enviar (rotar clave, revocar).
  document.addEventListener("submit", (evento) => {
    const mensaje = evento.target.dataset?.confirmar;
    if (mensaje && !window.confirm(mensaje)) evento.preventDefault();
  });

  // <select data-autoenviar> envía su formulario al cambiar de opción.
  document.addEventListener("change", (evento) => {
    if (evento.target.matches("[data-autoenviar]")) evento.target.form.submit();
  });

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
