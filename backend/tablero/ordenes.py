"""Pantallas de órdenes de servicio: listado, alta, detalle con validación y acciones.

Además de reparar, la orden registra por qué falló la pieza (causa del catálogo), qué componente se cambió y con
cuántos ciclos u horas falló: de ahí salen las fallas que usa Confiabilidad. Lo que registra un técnico de
convenio queda «Por validar» hasta que el Administrador lo aprueba o lo devuelve.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from auditoria.servicios import registrar
from auditoria.utils import ip_de
from confiabilidad import diagnostico as diagnosticos
from confiabilidad import servicios as confiabilidad
from confiabilidad.models import Causa, ComponenteInstalado
from servicio import validacion
from servicio.forms import FormularioOrden
from servicio.models import AvanceOrden, OrdenServicio
from telemetria.models import Alerta, Medicion, Nodo, Senal, Vehiculo

from .acceso import AccesoPorUnidadMixin, negar, solo_asignadas
from .api import _reducir
from .views import Sev, Tipo, VistaProtegida

Etapa = OrdenServicio.Etapa
Aprobacion = OrdenServicio.Aprobacion
TipoAvance = AvanceOrden.Tipo


def ordenes_visibles(usuario):
    consulta = OrdenServicio.objects.all()
    return consulta.filter(responsable=usuario) if solo_asignadas(usuario) else consulta


def exigir_orden(request, orden):
    if solo_asignadas(request.user) and orden.responsable_id != request.user.pk:
        negar(request, "orden no asignada", orden=orden.pk)


class OrdenesView(AccesoPorUnidadMixin, VistaProtegida, ListView):
    template_name = "tablero/ordenes.html"
    context_object_name = "ordenes"
    paginate_by = 25

    FILTROS = {"mis": OrdenServicio.ABIERTAS, "abiertas": OrdenServicio.ABIERTAS, "por_validar": None,
               "cerradas": (Etapa.CERRADA, Etapa.CANCELADA), "todas": None}

    def get_queryset(self):
        validacion.actualizar_en_validacion()
        restringido = solo_asignadas(self.request.user)
        self.filtro = self.request.GET.get("estado", "mis" if restringido else "abiertas")
        if self.filtro not in self.FILTROS:
            self.filtro = "abiertas"
        consulta = ordenes_visibles(self.request.user).select_related(
            "vehiculo__cliente", "responsable", "senal", "componente__tipo")
        if self.filtro == "mis":
            consulta = consulta.filter(responsable=self.request.user)
        if self.filtro == "por_validar":
            consulta = consulta.filter(aprobacion=Aprobacion.PENDIENTE)
        etapas = self.FILTROS[self.filtro]
        return consulta.filter(etapa__in=etapas) if etapas else consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        visibles = ordenes_visibles(self.request.user)
        filtros = [("mis", "Mis pendientes"), ("abiertas", "Abiertas"), ("por_validar", "Por validar"),
                   ("cerradas", "Cerradas"), ("todas", "Todas")]
        if solo_asignadas(self.request.user):
            filtros = [("mis", "Mis pendientes"), ("cerradas", "Cerradas"), ("todas", "Todas")]
        contexto.update(
            filtro=self.filtro, parametros=f"estado={self.filtro}", filtros=filtros,
            pasos=range(len(OrdenServicio.PASOS)),
            conteo={etapa: visibles.filter(etapa=etapa).count() for etapa in OrdenServicio.ABIERTAS},
            por_validar=visibles.filter(aprobacion=Aprobacion.PENDIENTE).count(),
            mis=visibles.filter(responsable=self.request.user, etapa__in=OrdenServicio.ABIERTAS).count(),
        )
        return contexto


class NuevaOrdenView(VistaProtegida, View):
    """P15: abrir una orden, normalmente desde una alerta (se llena sola, incluido el diagnóstico probable)."""

    permission_required = ("servicio.gestionar_ordenes", "telemetria.ver_tablero")
    template_name = "tablero/orden_nueva.html"

    def _alerta(self, request):
        pk = request.GET.get("alerta") or request.POST.get("alerta")
        if pk and str(pk).isdigit():
            return Alerta.objects.select_related("nodo__gateway__vehiculo", "gateway__vehiculo", "senal").filter(pk=pk).first()
        return None

    @staticmethod
    def _ya_tiene_orden(request, alerta):
        """Una alerta lleva a una sola orden: si ya tiene, no se duplica el trabajo."""
        orden = alerta.ordenes.first() if alerta else None
        if orden:
            messages.error(request, f"Esa alerta ya tiene la orden {orden.folio}.")
            return True
        return False

    def get(self, request):
        alerta = self._alerta(request)
        if self._ya_tiene_orden(request, alerta):
            return redirect("tablero:alertas")
        p = request.GET
        inicial = {"responsable": request.user if request.user.groups.exists() else None}
        vehiculo = Vehiculo.objects.filter(pk=p.get("vehiculo")).first() if p.get("vehiculo", "").isdigit() else None
        if vehiculo:
            inicial["vehiculo"] = vehiculo
            nodo = Nodo.objects.filter(pk=p.get("nodo"), gateway__vehiculo=vehiculo).first() if p.get("nodo", "").isdigit() else None
            senal = Senal.objects.filter(pk=p.get("revisar")).first() if p.get("revisar", "").isdigit() else None
            if senal:  # desde Calidad de los datos: revisar un sensor poco confiable
                inicial.update(nodo=nodo, titulo=f"Revisar sensor: {senal.nombre.lower()}"[:120],
                               diagnostico=f"El sensor de {senal.nombre.lower()} entrega pocas lecturas válidas. "
                                           "Revisar conexión, alimentación y estado del sensor.")
        if alerta:
            nodo = alerta.nodo
            gateway = nodo.gateway if nodo else alerta.gateway
            vehiculo = gateway.vehiculo if gateway else None
            texto = f"Orden creada desde la alerta «{alerta.mensaje}»."
            if alerta.diagnostico:
                texto += f"\nDiagnóstico del sistema ({alerta.get_certeza_display().lower()}): {alerta.diagnostico}"
            codigo = diagnosticos.componente_sugerido(alerta)
            inicial.update(
                titulo=alerta.mensaje[:120], diagnostico=texto, vehiculo=vehiculo, nodo=nodo,
                senal=alerta.senal if alerta.senal and alerta.senal.subsistema != "NODO" else None,
                componente=ComponenteInstalado.objects.activos().filter(vehiculo=vehiculo, tipo__codigo=codigo).first()
                if codigo and vehiculo else None,
            )
        return render(request, self.template_name, {"form": FormularioOrden(initial=inicial), "alerta": alerta})

    def post(self, request):
        alerta = self._alerta(request)
        if self._ya_tiene_orden(request, alerta):
            return redirect("tablero:alertas")
        form = FormularioOrden(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "alerta": alerta}, status=400)
        with transaction.atomic():
            orden = form.save(commit=False)
            orden.creada_por = request.user
            orden.alerta = alerta
            orden.save()
            asignado = f" Asignada a {orden.responsable.get_username()}." if orden.responsable else ""
            orden.anotar("Orden creada" + (f" desde la alerta «{alerta.mensaje}»." if alerta else ".") + asignado,
                         autor=request.user, tipo=TipoAvance.ETAPA)
            if alerta and alerta.activa:
                # «Atendida» = ya tiene orden. Se resuelve sola al cerrar la orden.
                alerta.estado = Alerta.Estado.ATENDIDA
                alerta.atendida_por = request.user
                alerta.atendida_en = timezone.now()
                alerta.nota = f"Se abrió la orden {orden.folio}"
                alerta.save(update_fields=["estado", "atendida_por", "atendida_en", "nota"])
        messages.success(request, f"Orden {orden.folio} creada.")
        return redirect("tablero:orden", pk=orden.pk)


class OrdenView(AccesoPorUnidadMixin, VistaProtegida, DetailView):
    template_name = "tablero/orden.html"
    context_object_name = "orden"
    queryset = OrdenServicio.objects.select_related("vehiculo__cliente", "nodo", "senal", "alerta", "responsable",
                                                    "creada_por", "componente__tipo", "causa")

    def get_object(self, queryset=None):
        orden = super().get_object(queryset)
        exigir_orden(self.request, orden)
        return orden

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        orden = self.object
        resultado = validacion.actualizar(orden)
        usuario = self.request.user
        subsistema = orden.componente.tipo.subsistema if orden.componente else (orden.senal.subsistema if orden.senal else "")
        causas = Causa.objects.filter(activa=True)
        contexto.update(
            validacion=resultado,
            avances=orden.avances.select_related("autor"),
            puede_gestionar=usuario.has_perm("servicio.gestionar_ordenes"),
            puede_validar=usuario.has_perm("servicio.validar_ordenes"),
            grafica=_datos_grafica(orden),
            minimo_lecturas=validacion.MIN_LECTURAS,
            falla=confiabilidad.falla_de(orden),
            causas=causas.filter(Q(subsistema=subsistema) | Q(subsistema="")) if subsistema else causas,
            componentes=ComponenteInstalado.objects.activos().filter(vehiculo=orden.vehiculo).select_related("tipo"),
            diagnostico_sistema=orden.alerta.diagnostico if orden.alerta_id and orden.alerta.diagnostico else "",
            certeza_sistema=orden.alerta.certeza if orden.alerta_id else "",
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
    """Acciones sobre una orden. Avanzar y anotar: técnicos y Administrador (el técnico de convenio, solo en sus
    órdenes). Validar, cancelar, aprobar y devolver: solo el Administrador. Ingeniería solo consulta."""

    permission_required = "servicio.gestionar_ordenes"
    SOLO_VALIDADOR = {"validar", "cancelar", "aprobar", "devolver"}
    SIEMPRE = {"nota", "aprobar", "devolver"}  # se permiten aunque la orden ya esté cerrada

    def post(self, request, pk):
        orden = get_object_or_404(OrdenServicio.objects.select_related("senal", "alerta", "componente__tipo",
                                                                       "responsable"), pk=pk)
        exigir_orden(request, orden)
        accion = request.POST.get("accion", "")
        texto = request.POST.get("texto", "").strip()[:2000]
        if accion in self.SOLO_VALIDADOR and not request.user.has_perm("servicio.validar_ordenes"):
            negar(request, "acción solo para el Administrador", accion=accion, permiso=["servicio.validar_ordenes"])
        metodo = getattr(self, f"_{accion}", None)
        if metodo is None or (accion not in self.SIEMPRE and not orden.abierta):
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

    def _diagnostico(self, request, orden, texto):
        """El técnico confirma o rechaza el diagnóstico que propuso el sistema (alimenta el % de acierto)."""
        respuesta = request.POST.get("respuesta")
        if respuesta not in ("confirmar", "rechazar"):
            messages.error(request, "Elige si confirmas o rechazas el diagnóstico.")
            return None
        causa = _causa(request)
        if respuesta == "rechazar" and causa is None:
            messages.error(request, "Si rechazas el diagnóstico, elige la causa real.")
            return None
        orden.diagnostico_confirmado = respuesta == "confirmar"
        campos = ["diagnostico_confirmado", "actualizada"]
        if causa:
            orden.causa = causa
            campos.append("causa")
        orden.save(update_fields=campos)
        if orden.alerta_id and orden.diagnostico_confirmado:
            orden.alerta.certeza = Alerta.Certeza.CONFIRMADO
            orden.alerta.save(update_fields=["certeza"])
        detalle = "confirmó" if orden.diagnostico_confirmado else f"rechazó (causa real: {causa})"
        orden.anotar(f"El técnico {detalle} el diagnóstico del sistema." + (f" {texto}" if texto else ""),
                     autor=request.user, tipo=TipoAvance.ETAPA)
        return "Diagnóstico registrado."

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
        causa = _causa(request)
        if causa is None:
            messages.error(request, "Elige la causa de la falla del catálogo.")
            return None
        componente_id = request.POST.get("componente", "")
        if componente_id.isdigit() and not orden.componente_id:
            orden.componente = ComponenteInstalado.objects.activos().filter(pk=componente_id, vehiculo=orden.vehiculo).first()
        reemplazar = request.POST.get("reemplazo") == "1"
        if reemplazar and orden.componente is None and not confiabilidad.falla_de(orden):
            messages.error(request, "Elige el componente que se cambió.")
            return None
        falla = confiabilidad.registrar_reparacion(orden, usuario=request.user, causa=causa, reemplazar=reemplazar,
                                                   serie_nueva=request.POST.get("serie_nueva", "").strip()[:60])
        orden.solucion = texto
        orden.etapa = Etapa.VALIDACION
        orden.validacion_inicio = timezone.now()
        if solo_asignadas(request.user):
            orden.aprobacion = Aprobacion.PENDIENTE
        orden.save(update_fields=["solucion", "causa", "componente", "etapa", "validacion_inicio", "aprobacion",
                                  "actualizada"])
        detalle = (f"Empieza la validación de {orden.get_minutos_validacion_display()}." if orden.tiene_criterio
                   else "Sin señal para validar con datos: queda en espera de que el Administrador la valide.")
        cambio = ""
        if falla:
            cambio = (f" Se cambió {falla.componente.tipo.nombre.lower()} con {falla.uso_al_fallar:,.0f} "
                      f"{falla.componente.tipo.unidad_texto} ({falla.porcentaje_garantia:.0f} % de la garantía"
                      f"{', falla prematura' if falla.prematura else ''}).")
        aprobacion = " Queda por validar por el Administrador." if orden.aprobacion == Aprobacion.PENDIENTE else ""
        orden.anotar(f"Reparación terminada: {texto} Causa: {causa}.{cambio} {detalle}{aprobacion}",
                     autor=request.user, tipo=TipoAvance.ETAPA)
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
        confiabilidad.confirmar_falla(orden)
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

    def _aprobar(self, request, orden, texto):
        if orden.aprobacion != Aprobacion.PENDIENTE:
            messages.error(request, "La orden no está por validar.")
            return None
        orden.aprobacion = Aprobacion.APROBADA
        orden.save(update_fields=["aprobacion", "actualizada"])
        orden.anotar("El Administrador aprobó lo registrado por el técnico de convenio." + (f" {texto}" if texto else ""),
                     autor=request.user, tipo=TipoAvance.ETAPA)
        confirmadas = confiabilidad.confirmar_falla(orden)
        registrar(Tipo.ORDEN_APROBACION, actor=request.user.get_username(), ip=ip_de(request),
                  detalle={"orden": orden.folio, "accion": "aprobar", "tecnico": orden.responsable.get_username()
                           if orden.responsable else ""})
        return "Trabajo aprobado." + (" La falla ya cuenta en los análisis." if confirmadas else
                                      " La falla contará cuando la orden se cierre." if orden.abierta else "")

    def _devolver(self, request, orden, texto):
        if orden.aprobacion != Aprobacion.PENDIENTE:
            messages.error(request, "La orden no está por validar.")
            return None
        if not texto:
            messages.error(request, "Explica al técnico qué debe corregir.")
            return None
        orden.aprobacion = Aprobacion.DEVUELTA
        orden.etapa = Etapa.REPARACION
        orden.validacion_inicio = None
        orden.resultado = OrdenServicio.Resultado.PENDIENTE
        orden.cerrada_en = None
        orden.save(update_fields=["aprobacion", "etapa", "validacion_inicio", "resultado", "cerrada_en", "actualizada"])
        orden.anotar(f"El Administrador devolvió la orden al técnico: {texto}", autor=request.user, tipo=TipoAvance.ETAPA)
        registrar(Tipo.ORDEN_APROBACION, severidad=Sev.ADVERTENCIA, actor=request.user.get_username(), ip=ip_de(request),
                  detalle={"orden": orden.folio, "accion": "devolver", "motivo": texto})
        return "Orden devuelta al técnico."


def _causa(request):
    pk = request.POST.get("causa", "")
    return Causa.objects.filter(pk=pk, activa=True).first() if pk.isdigit() else None
