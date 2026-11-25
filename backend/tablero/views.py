import csv
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from auditoria.models import EventoSeguridad
from auditoria.servicios import registrar, verificar_cadena
from auditoria.utils import ip_de
from telemetria.models import Alerta, Cliente, Gateway, Medicion, Nodo, Senal, Severidad, Vehiculo

from .api import alertas_del_autobus, nodo_seleccionado
from .permisos import PermisoAuditadoMixin

Tipo = EventoSeguridad.Tipo
Sev = EventoSeguridad.Severidad


class VistaProtegida(LoginRequiredMixin, PermisoAuditadoMixin):
    """Exige sesión iniciada y el permiso indicado; registra los intentos sin permiso."""


def _fecha(texto):
    """'AAAA-MM-DD' → date; cualquier otra cosa → None."""
    try:
        return datetime.strptime(texto or "", "%Y-%m-%d").date()
    except ValueError:
        return None


class MonitoreoView(VistaProtegida, TemplateView):
    """Monitoreo en vivo de UN autobús: /autobuses/<id>/monitoreo/?nodo=<id del nodo>."""

    permission_required = "telemetria.ver_tablero"
    template_name = "tablero/monitoreo.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        vehiculo = get_object_or_404(Vehiculo.objects.select_related("cliente"), pk=self.kwargs["pk"])
        nodo = nodo_seleccionado(self.request.GET.get("nodo"), vehiculo=vehiculo)
        senales = list(Senal.objects.filter(visible=True).exclude(subsistema=Senal.Subsistema.NODO))
        grupos = []
        for valor, etiqueta in Senal.Subsistema.choices:
            lista = [s for s in senales if s.subsistema == valor]
            if lista:
                grupos.append({"codigo": valor, "nombre": etiqueta, "senales": lista})
        contexto.update(
            vehiculo=vehiculo,
            nodo=nodo,
            nodos=Nodo.objects.filter(gateway__vehiculo=vehiculo).order_by("gateway_id", "numero"),
            grupos=grupos,
            alertas=alertas_del_autobus(vehiculo).select_related("senal", "nodo").distinct()[:6],
            config={
                "urlEstado": reverse("tablero:api_estado") + (f"?nodo={nodo.pk}" if nodo else ""),
                "intervaloMs": 2000,
            },
        )
        return contexto


class SenalView(VistaProtegida, DetailView):
    permission_required = "telemetria.ver_tablero"
    template_name = "tablero/senal.html"
    model = Senal
    slug_field = "codigo"
    slug_url_kwarg = "codigo"
    context_object_name = "senal"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        senal = self.object
        nodo = nodo_seleccionado(self.request.GET.get("nodo"))
        contexto.update(
            nodo=nodo,
            nodos=Nodo.objects.filter(gateway__vehiculo_id=nodo.gateway.vehiculo_id if nodo else None)
                              .order_by("gateway_id", "numero"),
            rangos=[(15, "15 min"), (60, "1 h"), (360, "6 h"), (1440, "24 h"), (10080, "7 días")],
            config={
                "codigo": senal.codigo,
                "nombre": senal.nombre,
                "unidad": senal.unidad,
                "tipo": senal.tipo,
                "decimales": senal.decimales,
                "etiquetas": senal.etiquetas,
                "nodo": nodo.pk if nodo else None,
                "urlSerie": reverse("tablero:api_serie", args=[senal.codigo]),
                "urlExportar": reverse("tablero:exportar"),
            },
        )
        return contexto


class AlertasView(VistaProtegida, ListView):
    permission_required = "telemetria.ver_tablero"
    template_name = "tablero/alertas.html"
    context_object_name = "alertas"
    paginate_by = 25

    FILTROS = {"activas": Alerta.ACTIVAS, "resueltas": (Alerta.Estado.RESUELTA,), "todas": None}

    SEVERIDADES = [("", "Todas"), (Severidad.CRITICA, "Crítica"), (Severidad.ADVERTENCIA, "Advertencia"),
                   (Severidad.INFO, "Informativa")]

    def _leer_filtros(self):
        """?cliente=<id>&autobus=<id>&severidad=<valor>&desde=AAAA-MM-DD&hasta=AAAA-MM-DD (lo inválido se ignora)."""
        p = self.request.GET
        cliente_id, autobus_id = p.get("cliente", ""), p.get("autobus", "")
        cliente = Cliente.objects.filter(pk=cliente_id).first() if cliente_id.isdigit() else None
        autobus = Vehiculo.objects.select_related("cliente").filter(pk=autobus_id).first() if autobus_id.isdigit() else None
        if autobus and cliente and autobus.cliente_id != cliente.pk:
            autobus = None  # se cambió de cliente: la unidad anterior ya no aplica
        severidad = p.get("severidad", "")
        desde, hasta = _fecha(p.get("desde")), _fecha(p.get("hasta"))
        if desde and hasta and desde > hasta:
            desde, hasta = hasta, desde
        return {"cliente": cliente, "autobus": autobus, "severidad": severidad if severidad in dict(self.SEVERIDADES) else "",
                "desde": desde, "hasta": hasta}

    def get_queryset(self):
        self.filtro = self.request.GET.get("estado", "activas")
        if self.filtro not in self.FILTROS:
            self.filtro = "activas"
        f = self.f = self._leer_filtros()
        consulta = Alerta.objects.select_related("senal", "nodo__gateway__vehiculo", "gateway__vehiculo", "atendida_por")
        # La unidad de una alerta es la de su gateway o la del gateway de su nodo.
        if f["autobus"]:
            consulta = consulta.filter(Q(gateway__vehiculo=f["autobus"]) | Q(nodo__gateway__vehiculo=f["autobus"]))
        elif f["cliente"]:
            consulta = consulta.filter(Q(gateway__vehiculo__cliente=f["cliente"]) | Q(nodo__gateway__vehiculo__cliente=f["cliente"]))
        if f["severidad"]:
            consulta = consulta.filter(severidad=f["severidad"])
        zona = timezone.get_current_timezone()
        if f["desde"]:
            consulta = consulta.filter(creada__gte=timezone.make_aware(datetime.combine(f["desde"], time.min), zona))
        if f["hasta"]:
            consulta = consulta.filter(creada__lt=timezone.make_aware(datetime.combine(f["hasta"] + timedelta(days=1), time.min), zona))
        estados = self.FILTROS[self.filtro]
        consulta = consulta.filter(estado__in=estados) if estados else consulta
        return consulta.distinct()

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        f = self.f
        datos = {"cliente": f["cliente"].pk if f["cliente"] else "", "autobus": f["autobus"].pk if f["autobus"] else "",
                 "severidad": f["severidad"], "desde": f["desde"].isoformat() if f["desde"] else "",
                 "hasta": f["hasta"].isoformat() if f["hasta"] else ""}
        extra = urlencode({k: v for k, v in datos.items() if v})
        autobuses = Vehiculo.objects.select_related("cliente").order_by("cliente__nombre_comercial", "identificador")
        if f["cliente"]:
            autobuses = autobuses.filter(cliente=f["cliente"])
        contexto.update(
            filtro=self.filtro, f=f, hay_filtros=bool(extra),
            parametros=f"estado={self.filtro}" + (f"&{extra}" if extra else ""),  # paginación
            parametro_filtros=f"&{extra}" if extra else "",  # pestañas Activas / Resueltas / Todas
            filtros=[("activas", "Activas"), ("resueltas", "Resueltas"), ("todas", "Todas")],
            clientes=Cliente.objects.filter(vehiculos__isnull=False).distinct().order_by("nombre_comercial"),
            autobuses=autobuses, severidades=self.SEVERIDADES,
        )
        return contexto


class AtenderAlertaView(VistaProtegida, View):
    permission_required = "telemetria.atender_alerta"

    def post(self, request, pk):
        alerta = get_object_or_404(Alerta, pk=pk)
        accion = request.POST.get("accion")
        nota = request.POST.get("nota", "").strip()[:255]
        ahora = timezone.now()
        if accion == "atender" and alerta.estado == Alerta.Estado.ABIERTA:
            alerta.estado = Alerta.Estado.ATENDIDA
        elif accion == "resolver" and alerta.activa:
            alerta.estado = Alerta.Estado.RESUELTA
            alerta.resuelta_en = ahora
        else:
            messages.error(request, "La alerta ya no admite esa acción.")
            return self._volver(request)
        alerta.atendida_por = request.user
        alerta.atendida_en = ahora
        if nota:
            alerta.nota = nota
        alerta.save()
        registrar(Tipo.ALERTA_ATENDIDA, actor=request.user.get_username(), ip=ip_de(request),
                  detalle={"alerta": alerta.pk, "accion": accion, "mensaje": alerta.mensaje})
        messages.success(request, "Alerta actualizada.")
        return self._volver(request)

    def _volver(self, request):
        destino = request.POST.get("siguiente", "")
        if url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(destino)
        return redirect("tablero:alertas")


class SeguridadView(VistaProtegida, ListView):
    permission_required = "auditoria.ver_auditoria"
    template_name = "tablero/seguridad.html"
    context_object_name = "eventos"
    paginate_by = 50

    def get_queryset(self):
        consulta = EventoSeguridad.objects.all()
        self.severidad = self.request.GET.get("severidad", "")
        if self.severidad in Sev.values:
            consulta = consulta.filter(severidad=self.severidad)
        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(verificacion=verificar_cadena(), severidad=self.severidad, severidades=Sev.choices,
                        parametros=f"severidad={self.severidad}" if self.severidad else "")
        return contexto


def _dispositivo(tipo, pk, bloquear=False):
    modelos = {"gateway": Gateway, "nodo": Nodo}
    if tipo not in modelos:
        raise Http404
    consulta = modelos[tipo].objects.select_for_update() if bloquear else modelos[tipo].objects
    return get_object_or_404(consulta, pk=pk)


class RotarClaveView(VistaProtegida, View):
    """Genera una clave nueva y la muestra UNA sola vez para provisionar el dispositivo por el puerto serie."""

    permission_required = "telemetria.gestionar_dispositivos"

    def post(self, request, tipo, pk):
        with transaction.atomic():
            dispositivo = _dispositivo(tipo, pk, bloquear=True)
            clave = dispositivo.generar_clave()
            if tipo == "nodo":
                dispositivo.ultimo_contador = None  # el nodo reinicia su contador al recibir la clave nueva
            else:
                dispositivo.epoca += 1  # el gateway reinicia su numeración de lotes
                dispositivo.ultimo_lote = None
            dispositivo.save()
        registrar(Tipo.CLAVE_ROTADA, severidad=Sev.ADVERTENCIA, actor=request.user.get_username(), ip=ip_de(request),
                  detalle={"tipo": tipo, "dispositivo": str(dispositivo)})
        respuesta = render(request, "tablero/clave_generada.html",
                           {"dispositivo": dispositivo, "tipo": tipo, "clave_hex": clave.hex().upper()})
        add_never_cache_headers(respuesta)
        return respuesta


class CambiarEstadoView(VistaProtegida, View):
    permission_required = "telemetria.gestionar_dispositivos"

    def post(self, request, tipo, pk):
        dispositivo = _dispositivo(tipo, pk)
        dispositivo.activo = not dispositivo.activo
        dispositivo.save(update_fields=["activo"])
        registrar(Tipo.DISPOSITIVO_ESTADO, severidad=Sev.ADVERTENCIA, actor=request.user.get_username(),
                  ip=ip_de(request), detalle={"tipo": tipo, "dispositivo": str(dispositivo), "activo": dispositivo.activo})
        messages.success(request, f"{dispositivo} {'reactivado' if dispositivo.activo else 'revocado'}.")
        siguiente = request.POST.get("siguiente", "")
        if siguiente and url_has_allowed_host_and_scheme(siguiente, {request.get_host()}, request.is_secure()):
            return redirect(siguiente)  # regresa a la misma página de Autobuses, con sus filtros
        return redirect("tablero:autobuses")


class _Eco:
    """Objeto tipo archivo para que csv.writer escriba fila por fila en una respuesta en streaming."""

    def write(self, valor):
        return valor


class ExportarView(VistaProtegida, View):
    """CSV de mediciones: ?senal=<codigo>&nodo=<id del nodo>&minutos=<n>"""

    permission_required = "telemetria.exportar_datos"

    def get(self, request):
        try:
            minutos = max(5, min(int(request.GET.get("minutos", 60)), 30 * 24 * 60))
        except ValueError:
            minutos = 60
        consulta = Medicion.objects.select_related("senal", "nodo__gateway__vehiculo").filter(
            ts__gte=timezone.now() - timedelta(minutes=minutos)
        )
        codigo = request.GET.get("senal")
        if codigo:
            consulta = consulta.filter(senal__codigo=codigo)
        nodo = nodo_seleccionado(request.GET.get("nodo")) if request.GET.get("nodo") else None
        if nodo:
            consulta = consulta.filter(nodo=nodo)
        registrar(Tipo.EXPORTACION, actor=request.user.get_username(), ip=ip_de(request),
                  detalle={"senal": codigo or "todas", "nodo": str(nodo) if nodo else "todos", "autobus": nodo.gateway.vehiculo.identificador if nodo else "todos", "minutos": minutos})

        escritor = csv.writer(_Eco())

        def filas():
            yield escritor.writerow(["fecha_utc", "vehiculo", "gateway", "nodo", "senal", "valor", "unidad", "calidad"])
            for m in consulta.order_by("ts").iterator():
                yield escritor.writerow([m.ts.isoformat(), m.nodo.gateway.vehiculo.identificador, m.nodo.gateway.identificador,
                                         m.nodo.numero, m.senal.codigo, m.valor, m.senal.unidad, m.calidad])

        respuesta = StreamingHttpResponse(filas(), content_type="text/csv; charset=utf-8")
        nombre = f"mediciones_{codigo or 'todas'}_{timezone.now():%Y%m%d_%H%M}.csv"
        respuesta["Content-Disposition"] = f'attachment; filename="{nombre}"'
        return respuesta
