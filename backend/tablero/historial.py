"""
Historial de datos: consulta de lecturas guardadas por unidad, nodo, señal, fechas y calidad,
y exportación a CSV de lecturas, alertas u órdenes de servicio (cada descarga queda en la bitácora con su motivo).
"""
import csv
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.db.models import Avg, Count, Max, Min, Q
from django.http import StreamingHttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from auditoria.servicios import registrar
from auditoria.utils import ip_de
from servicio.models import OrdenServicio
from telemetria.models import Alerta, Cliente, Medicion, Nodo, Senal, Vehiculo

from .api import _reducir
from .views import Tipo, VistaProtegida, _Eco

DIAS_POR_DEFECTO = 7
DIAS_MAXIMOS = 92  # un trimestre por consulta, para que la base de datos responda rápido
CALIDADES = [("", "Todas"), *Medicion.Calidad.choices]
SUBSISTEMAS = [("", "Todos"), *[(v, e) for v, e in Senal.Subsistema.choices if v != Senal.Subsistema.NODO]]
EXPORTABLES = [("mediciones", "Lecturas de sensores"), ("alertas", "Alertas"), ("ordenes", "Órdenes de servicio")]


def _fecha(texto):
    try:
        return datetime.strptime(texto or "", "%Y-%m-%d").date()
    except ValueError:
        return None


def leer_filtros(parametros):
    """Convierte los parámetros de la URL en filtros válidos. Lo que no sea válido se ignora."""
    hoy = timezone.localdate()
    hasta = _fecha(parametros.get("hasta")) or hoy
    desde = _fecha(parametros.get("desde")) or hasta - timedelta(days=DIAS_POR_DEFECTO - 1)
    avisos = []
    if desde > hasta:
        desde, hasta = hasta, desde
    if (hasta - desde).days >= DIAS_MAXIMOS:
        desde = hasta - timedelta(days=DIAS_MAXIMOS - 1)
        avisos.append(f"El periodo se limitó a {DIAS_MAXIMOS} días.")
    # Cliente → unidad → nodo: cada nivel debe pertenecer al anterior; si no, se descarta (p. ej. al cambiar de cliente).
    cliente_id, autobus_id, nodo_id = (parametros.get(k, "") for k in ("cliente", "autobus", "nodo"))
    cliente = Cliente.objects.filter(pk=cliente_id).first() if cliente_id.isdigit() else None
    autobus = Vehiculo.objects.select_related("cliente").filter(pk=autobus_id).first() if autobus_id.isdigit() else None
    nodo = Nodo.objects.select_related("gateway__vehiculo").filter(pk=nodo_id).first() if nodo_id.isdigit() else None
    if nodo and autobus is None and not autobus_id:
        autobus = nodo.gateway.vehiculo  # enlaces anteriores que solo traían ?nodo=
    if autobus and cliente and autobus.cliente_id != cliente.pk:
        autobus = None
    if nodo and (autobus is None or nodo.gateway.vehiculo_id != autobus.pk):
        nodo = None
    subsistema = parametros.get("subsistema", "")
    if subsistema not in dict(SUBSISTEMAS):
        subsistema = ""
    senal = Senal.objects.filter(codigo=parametros.get("senal", "")).first() if parametros.get("senal") else None
    if senal and subsistema and senal.subsistema != subsistema:
        senal = None
    calidad = parametros.get("calidad", "")
    if calidad not in dict(CALIDADES):
        calidad = ""
    # Para la gráfica hace falta un solo nodo: el elegido, o el único que tenga la unidad.
    nodo_grafica = nodo
    if nodo_grafica is None and autobus is not None:
        nodos_unidad = list(Nodo.objects.select_related("gateway__vehiculo").filter(gateway__vehiculo=autobus)[:2])
        nodo_grafica = nodos_unidad[0] if len(nodos_unidad) == 1 else None
    zona = timezone.get_current_timezone()
    return {
        "desde": desde, "hasta": hasta, "cliente": cliente, "autobus": autobus, "nodo": nodo,
        "nodo_grafica": nodo_grafica, "subsistema": subsistema, "senal": senal, "calidad": calidad, "avisos": avisos,
        "inicio": timezone.make_aware(datetime.combine(desde, time.min), zona),
        "fin": timezone.make_aware(datetime.combine(hasta + timedelta(days=1), time.min), zona),
    }


def parametros_url(f):
    """Los filtros como texto para enlaces (paginación y exportación)."""
    datos = {"desde": f["desde"].isoformat(), "hasta": f["hasta"].isoformat()}
    if f["cliente"]:
        datos["cliente"] = f["cliente"].pk
    if f["autobus"]:
        datos["autobus"] = f["autobus"].pk
    if f["nodo"]:
        datos["nodo"] = f["nodo"].pk
    if f["subsistema"]:
        datos["subsistema"] = f["subsistema"]
    if f["senal"]:
        datos["senal"] = f["senal"].codigo
    if f["calidad"]:
        datos["calidad"] = f["calidad"]
    return urlencode(datos)


def mediciones(f):
    consulta = Medicion.objects.filter(ts__gte=f["inicio"], ts__lt=f["fin"])
    if f["nodo"]:
        consulta = consulta.filter(nodo=f["nodo"])
    elif f["autobus"]:
        consulta = consulta.filter(nodo__gateway__vehiculo=f["autobus"])
    elif f["cliente"]:
        consulta = consulta.filter(nodo__gateway__vehiculo__cliente=f["cliente"])
    if f["senal"]:
        consulta = consulta.filter(senal=f["senal"])
    elif f["subsistema"]:
        consulta = consulta.filter(senal__subsistema=f["subsistema"])
    if f["calidad"]:
        consulta = consulta.filter(calidad=f["calidad"])
    return consulta


class HistorialView(VistaProtegida, ListView):
    permission_required = "telemetria.ver_tablero"
    template_name = "tablero/historial.html"
    context_object_name = "lecturas"
    paginate_by = 50

    def get_queryset(self):
        self.filtros = leer_filtros(self.request.GET)
        return mediciones(self.filtros).select_related("senal", "nodo__gateway__vehiculo").order_by("-ts", "-id")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        f = self.filtros
        for lectura in contexto["lecturas"]:
            lectura.texto = lectura.senal.formatear(lectura.valor)

        consulta = mediciones(f)
        resumen = consulta.aggregate(n=Count("id"), validas=Count("id", filter=Q(calidad=Medicion.Calidad.OK)),
                                     primera=Min("ts"), ultima=Max("ts"))
        estadisticas = None
        if f["senal"]:
            valores = consulta.filter(valor__isnull=False).aggregate(minimo=Min("valor"), maximo=Max("valor"), promedio=Avg("valor"))
            senal = f["senal"]
            if senal.tipo == Senal.Tipo.BOOLEANA:
                estadisticas = {"booleana": True, "activo": round(100 * valores["promedio"]) if valores["promedio"] is not None else None}
            else:
                estadisticas = {"booleana": False, **{k: senal.formatear(v) for k, v in valores.items()}}

        autobuses = Vehiculo.objects.select_related("cliente").order_by("cliente__nombre_comercial", "identificador")
        if f["cliente"]:
            autobuses = autobuses.filter(cliente=f["cliente"])
        senales = Senal.objects.filter(visible=True).order_by("subsistema", "orden")
        if f["subsistema"]:
            senales = senales.filter(subsistema=f["subsistema"])
        contexto.update(
            f=f, parametros=parametros_url(f),
            clientes=Cliente.objects.filter(vehiculos__isnull=False).distinct().order_by("nombre_comercial"),
            autobuses=autobuses,
            nodos=Nodo.objects.filter(gateway__vehiculo=f["autobus"]).order_by("gateway_id", "numero") if f["autobus"] else [],
            subsistemas=SUBSISTEMAS,
            senales=senales,
            calidades=CALIDADES, exportables=EXPORTABLES,
            resumen=resumen, porcentaje_validas=round(100 * resumen["validas"] / resumen["n"]) if resumen["n"] else None,
            estadisticas=estadisticas, grafica=self._grafica(f, consulta),
            puede_exportar=self.request.user.has_perm("telemetria.exportar_datos"),
        )
        return contexto

    def _grafica(self, f, consulta):
        """Solo hay gráfica con una señal y un solo nodo (si no, se mezclarían unidades distintas)."""
        if not (f["senal"] and f["nodo_grafica"]):
            return None
        validas = consulta.filter(valor__isnull=False, nodo=f["nodo_grafica"])
        filas = validas.order_by("ts").values_list("ts", "valor")
        senal = f["senal"]
        return {
            "puntos": _reducir(filas, f["inicio"], f["fin"], settings.TELEMETRIA["PUNTOS_MAX_SERIE"], validas.count()),
            "desde": int(f["inicio"].timestamp() * 1000), "hasta": int(f["fin"].timestamp() * 1000),
            "umbral_alto": senal.umbral_alto, "umbral_bajo": senal.umbral_bajo,
            "unidad": senal.unidad, "tipo": senal.tipo, "decimales": senal.decimales, "etiquetas": senal.etiquetas,
        }


def _celda(valor):
    """Evita que Excel interprete como fórmula un texto que empieza con = + - @ (inyección en CSV)."""
    if isinstance(valor, str) and valor[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + valor
    return "" if valor is None else valor


def _iso(fecha):
    return fecha.isoformat() if fecha else ""


class ExportarHistorialView(VistaProtegida, View):
    """CSV con los mismos filtros del historial. Pide un motivo, que queda en la bitácora de seguridad."""

    permission_required = "telemetria.exportar_datos"

    def get(self, request):
        f = leer_filtros(request.GET)
        tipo = request.GET.get("tipo", "mediciones")
        if tipo not in dict(EXPORTABLES):
            tipo = "mediciones"
        motivo = request.GET.get("motivo", "").strip()[:200]
        if not motivo:
            messages.error(request, "Escribe el motivo de la exportación: queda registrado en la bitácora.")
            return redirect(f"{reverse('tablero:historial')}?{parametros_url(f)}")

        encabezados, filas = getattr(self, f"_{tipo}")(f)
        registrar(Tipo.EXPORTACION, actor=request.user.get_username(), ip=ip_de(request), detalle={
            "tipo": tipo, "motivo": motivo, "desde": f["desde"].isoformat(), "hasta": f["hasta"].isoformat(),
            "cliente": f["cliente"].nombre_comercial if f["cliente"] else "todos",
            "autobus": f["autobus"].identificador if f["autobus"] else "todos",
            "nodo": str(f["nodo"]) if f["nodo"] else "todos", "subsistema": f["subsistema"] or "todos",
            "senal": f["senal"].codigo if f["senal"] else "todas", "calidad": f["calidad"] or "todas",
        })

        escritor = csv.writer(_Eco())

        def contenido():
            yield "\ufeff"  # marca BOM: así Excel abre bien los acentos
            yield escritor.writerow(encabezados)
            for fila in filas:
                yield escritor.writerow([_celda(v) for v in fila])

        respuesta = StreamingHttpResponse(contenido(), content_type="text/csv; charset=utf-8")
        nombre = f"{tipo}_{f['desde']:%Y%m%d}-{f['hasta']:%Y%m%d}.csv"
        respuesta["Content-Disposition"] = f'attachment; filename="{nombre}"'
        return respuesta

    def _mediciones(self, f):
        consulta = mediciones(f).select_related("senal", "nodo__gateway__vehiculo").order_by("ts", "id")
        encabezados = ["fecha_utc", "vehiculo", "gateway", "nodo", "senal", "valor", "unidad", "calidad", "contador"]
        filas = ([_iso(m.ts), m.nodo.gateway.vehiculo.identificador, m.nodo.gateway.identificador, m.nodo.numero,
                  m.senal.codigo, m.valor, m.senal.unidad, m.calidad, m.contador] for m in consulta.iterator())
        return encabezados, filas

    def _alertas(self, f):
        consulta = Alerta.objects.filter(creada__gte=f["inicio"], creada__lt=f["fin"])
        if f["nodo"]:
            consulta = consulta.filter(Q(nodo=f["nodo"]) | Q(gateway=f["nodo"].gateway, nodo__isnull=True))
        elif f["autobus"]:
            consulta = consulta.filter(Q(nodo__gateway__vehiculo=f["autobus"]) | Q(gateway__vehiculo=f["autobus"]))
        elif f["cliente"]:
            consulta = consulta.filter(Q(nodo__gateway__vehiculo__cliente=f["cliente"]) | Q(gateway__vehiculo__cliente=f["cliente"]))
        if f["senal"]:
            consulta = consulta.filter(senal=f["senal"])
        elif f["subsistema"]:
            consulta = consulta.filter(senal__subsistema=f["subsistema"])
        consulta = consulta.distinct().select_related("nodo__gateway__vehiculo", "gateway__vehiculo", "senal",
                                                      "atendida_por").order_by("creada")
        encabezados = ["creada_utc", "vehiculo", "nodo", "senal", "tipo", "severidad", "estado", "mensaje", "ocurrencias",
                       "ultima_vez_utc", "atendida_por", "atendida_utc", "resuelta_utc", "nota"]

        def filas():
            for a in consulta.iterator():
                gateway = a.nodo.gateway if a.nodo else a.gateway
                yield [_iso(a.creada), gateway.vehiculo.identificador if gateway else "", a.nodo.numero if a.nodo else "",
                       a.senal.codigo if a.senal else "", a.tipo, a.severidad, a.estado, a.mensaje, a.ocurrencias,
                       _iso(a.ultima_vez), a.atendida_por.get_username() if a.atendida_por else "", _iso(a.atendida_en),
                       _iso(a.resuelta_en), a.nota]
        return encabezados, filas()

    def _ordenes(self, f):
        consulta = OrdenServicio.objects.filter(creada__gte=f["inicio"], creada__lt=f["fin"])
        if f["autobus"]:
            consulta = consulta.filter(vehiculo=f["autobus"])
        elif f["cliente"]:
            consulta = consulta.filter(vehiculo__cliente=f["cliente"])
        if f["senal"]:
            consulta = consulta.filter(senal=f["senal"])
        elif f["subsistema"]:
            consulta = consulta.filter(senal__subsistema=f["subsistema"])
        consulta = consulta.select_related("vehiculo", "senal", "responsable").order_by("creada")
        encabezados = ["folio", "vehiculo", "falla", "etapa", "resultado", "senal_validacion", "responsable", "creada_utc",
                       "reparacion_utc", "cerrada_utc", "intentos", "diagnostico", "solucion"]
        filas = ([o.folio, o.vehiculo.identificador, o.titulo, o.etapa, o.resultado, o.senal.codigo if o.senal else "",
                  o.responsable.get_username() if o.responsable else "", _iso(o.creada), _iso(o.validacion_inicio),
                  _iso(o.cerrada_en), o.intentos, o.diagnostico, o.solucion] for o in consulta.iterator())
        return encabezados, filas