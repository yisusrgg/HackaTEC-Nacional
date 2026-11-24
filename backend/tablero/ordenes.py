"""Pantallas de órdenes de servicio: listado, alta, detalle con validación y acciones."""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from auditoria.servicios import registrar
from auditoria.utils import ip_de
from servicio import validacion
from servicio.forms import FormularioOrden
from servicio.models import AvanceOrden, OrdenServicio
from telemetria.models import Alerta, Medicion

from .api import _reducir
from .views import Sev, Tipo, VistaProtegida

Etapa = OrdenServicio.Etapa
TipoAvance = AvanceOrden.Tipo


class OrdenesView(VistaProtegida, ListView):
    permission_required = "telemetria.ver_tablero"
    template_name = "tablero/ordenes.html"
    context_object_name = "ordenes"
    paginate_by = 25

    FILTROS = {"abiertas": OrdenServicio.ABIERTAS, "cerradas": (Etapa.CERRADA, Etapa.CANCELADA), "todas": None}

    def get_queryset(self):
        validacion.actualizar_en_validacion()
        self.filtro = self.request.GET.get("estado", "abiertas")
        if self.filtro not in self.FILTROS:
            self.filtro = "abiertas"
        consulta = OrdenServicio.objects.select_related("vehiculo", "responsable", "senal")
        etapas = self.FILTROS[self.filtro]
        return consulta.filter(etapa__in=etapas) if etapas else consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            filtro=self.filtro, parametros=f"estado={self.filtro}",
            filtros=[("abiertas", "Abiertas"), ("cerradas", "Cerradas"), ("todas", "Todas")],
            pasos=range(len(OrdenServicio.PASOS)),
            conteo={etapa: OrdenServicio.objects.filter(etapa=etapa).count() for etapa in OrdenServicio.ABIERTAS},
        )
        return contexto


class NuevaOrdenView(VistaProtegida, View):
    permission_required = "servicio.gestionar_ordenes"
    template_name = "tablero/orden_nueva.html"

    def _alerta(self, request):
        pk = request.GET.get("alerta") or request.POST.get("alerta")
        if pk and str(pk).isdigit():
            return Alerta.objects.select_related("nodo__gateway__vehiculo", "gateway__vehiculo", "senal").filter(pk=pk).first()
        return None

    def get(self, request):
        alerta = self._alerta(request)
        inicial = {"responsable": request.user if request.user.groups.exists() else None}
        if alerta:
            nodo = alerta.nodo
            gateway = nodo.gateway if nodo else alerta.gateway
            inicial.update(
                titulo=alerta.mensaje[:120],
                diagnostico=f"Orden creada desde la alerta «{alerta.mensaje}».",
                vehiculo=gateway.vehiculo if gateway else None,
                nodo=nodo,
                senal=alerta.senal if alerta.senal and alerta.senal.subsistema != "NODO" else None,
            )
        return render(request, self.template_name, {"form": FormularioOrden(initial=inicial), "alerta": alerta})

    def post(self, request):
        alerta = self._alerta(request)
        form = FormularioOrden(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "alerta": alerta}, status=400)
        with transaction.atomic():
            orden = form.save(commit=False)
            orden.creada_por = request.user
            orden.alerta = alerta
            orden.save()
            orden.anotar("Orden creada" + (f" desde la alerta «{alerta.mensaje}»." if alerta else "."),
                         autor=request.user, tipo=TipoAvance.ETAPA)
            if alerta and alerta.estado == Alerta.Estado.ABIERTA:
                alerta.estado = Alerta.Estado.ATENDIDA
                alerta.atendida_por = request.user
                alerta.atendida_en = timezone.now()
                alerta.nota = f"Se abrió la orden {orden.folio}"
                alerta.save(update_fields=["estado", "atendida_por", "atendida_en", "nota"])
        messages.success(request, f"Orden {orden.folio} creada.")
        return redirect("tablero:orden", pk=orden.pk)


class OrdenView(VistaProtegida, DetailView):
    permission_required = "telemetria.ver_tablero"
    template_name = "tablero/orden.html"
    context_object_name = "orden"
    queryset = OrdenServicio.objects.select_related("vehiculo", "nodo", "senal", "alerta", "responsable", "creada_por")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        orden = self.object
        resultado = validacion.actualizar(orden)
        usuario = self.request.user
        contexto.update(
            validacion=resultado,
            avances=orden.avances.select_related("autor"),
            puede_gestionar=usuario.has_perm("servicio.gestionar_ordenes"),
            puede_validar=usuario.has_perm("servicio.validar_ordenes"),
            grafica=_datos_grafica(orden),
            minimo_lecturas=validacion.MIN_LECTURAS,
        )
        return contexto


def _datos_grafica(orden):
    """Lecturas de la señal antes y después de la reparación, para comparar en la gráfica."""
    if not orden.tiene_criterio:
        return None
    ahora = timezone.now()
    duracion = timedelta(minutes=orden.minutos_validacion)
    if orden.validacion_inicio:
        # Misma duración antes y después de la reparación, para comparar.
        desde, hasta = orden.validacion_inicio - duracion, orden.validacion_fin
    else:
        desde, hasta = ahora - duracion, ahora
    consulta = Medicion.objects.filter(senal=orden.senal, nodo=orden.nodo, ts__gte=desde, ts__lte=min(hasta, ahora),
                                       valor__isnull=False)
    filas = consulta.order_by("ts").values_list("ts", "valor")
    senal = orden.senal
    return {
        "puntos": _reducir(filas, desde, hasta, settings.TELEMETRIA["PUNTOS_MAX_SERIE"], consulta.count()),
        "desde": int(desde.timestamp() * 1000),
        "hasta": int(hasta.timestamp() * 1000),
        "reparacion": int(orden.validacion_inicio.timestamp() * 1000) if orden.validacion_inicio else None,
        "umbral_alto": senal.umbral_alto, "umbral_bajo": senal.umbral_bajo,
        "nombre": senal.nombre, "unidad": senal.unidad, "tipo": senal.tipo, "decimales": senal.decimales,
        "etiquetas": senal.etiquetas,
    }


class OrdenAccionView(VistaProtegida, View):
    """Acciones sobre una orden. Avanzar y anotar: Técnico e Ingeniería. Validar y cancelar: solo Ingeniería."""

    permission_required = "servicio.gestionar_ordenes"
    SOLO_VALIDADOR = {"validar", "cancelar"}

    def post(self, request, pk):
        orden = get_object_or_404(OrdenServicio.objects.select_related("senal", "alerta"), pk=pk)
        accion = request.POST.get("accion", "")
        texto = request.POST.get("texto", "").strip()[:2000]
        if accion in self.SOLO_VALIDADOR and not request.user.has_perm("servicio.validar_ordenes"):
            registrar(Tipo.ACCESO_DENEGADO, severidad=Sev.ADVERTENCIA, actor=request.user.get_username(), ip=ip_de(request),
                      detalle={"ruta": request.path, "accion": accion, "permiso": ["servicio.validar_ordenes"]})
            raise PermissionDenied
        metodo = getattr(self, f"_{accion}", None)
        if metodo is None or (accion != "nota" and not orden.abierta):
            messages.error(request, "La orden ya no admite esa acción.")
            return redirect("tablero:orden", pk=orden.pk)
        with transaction.atomic():
            mensaje = metodo(request, orden, texto)
        if mensaje:
            messages.success(request, mensaje)
        return redirect("tablero:orden", pk=orden.pk)

    def _nota(self, request, orden, texto):
        if not texto:
            messages.error(request, "Escribe el avance antes de guardarlo.")
            return None
        orden.anotar(texto, autor=request.user)
        return "Avance guardado."

    def _a_reparacion(self, request, orden, texto):
        if orden.etapa != Etapa.DIAGNOSTICO:
            messages.error(request, "La orden no está en diagnóstico.")
            return None
        if texto:
            orden.diagnostico = (orden.diagnostico + "\n\n" + texto).strip()
        orden.etapa = Etapa.REPARACION
        orden.save(update_fields=["etapa", "diagnostico", "actualizada"])
        orden.anotar("Diagnóstico terminado; empieza la reparación." + (f" {texto}" if texto else ""),
                     autor=request.user, tipo=TipoAvance.ETAPA)
        return "La orden pasó a reparación."

    def _a_validacion(self, request, orden, texto):
        if orden.etapa != Etapa.REPARACION:
            messages.error(request, "La orden no está en reparación.")
            return None
        if not texto:
            messages.error(request, "Describe la solución aplicada (qué se hizo y qué refacciones se usaron).")
            return None
        orden.solucion = texto
        orden.etapa = Etapa.VALIDACION
        orden.validacion_inicio = timezone.now()
        orden.save(update_fields=["solucion", "etapa", "validacion_inicio", "actualizada"])
        detalle = (f"Empieza la validación de {orden.get_minutos_validacion_display()}." if orden.tiene_criterio
                   else "Sin señal para validar con datos: queda en espera de que Ingeniería la valide.")
        orden.anotar(f"Reparación terminada: {texto} {detalle}", autor=request.user, tipo=TipoAvance.ETAPA)
        return "Reparación registrada. " + detalle

    def _validar(self, request, orden, texto):
        if orden.etapa != Etapa.VALIDACION:
            messages.error(request, "Solo se puede validar una orden que está en validación.")
            return None
        if not texto:
            messages.error(request, "Explica por qué se da por buena la reparación.")
            return None
        orden.etapa = Etapa.CERRADA
        orden.resultado = OrdenServicio.Resultado.VALIDADA_MANUAL
        orden.cerrada_en = timezone.now()
        orden.save(update_fields=["etapa", "resultado", "cerrada_en", "actualizada"])
        orden.anotar(f"Validada y cerrada manualmente: {texto}", autor=request.user, tipo=TipoAvance.ETAPA)
        orden.resolver_alerta(request.user)
        return f"Orden {orden.folio} cerrada."

    def _cancelar(self, request, orden, texto):
        if not texto:
            messages.error(request, "Explica el motivo de la cancelación.")
            return None
        orden.etapa = Etapa.CANCELADA
        orden.resultado = OrdenServicio.Resultado.CANCELADA
        orden.cerrada_en = timezone.now()
        orden.save(update_fields=["etapa", "resultado", "cerrada_en", "actualizada"])
        orden.anotar(f"Orden cancelada: {texto}", autor=request.user, tipo=TipoAvance.ETAPA)
        return f"Orden {orden.folio} cancelada."