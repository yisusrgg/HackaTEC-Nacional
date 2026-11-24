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
from telemetria.models import Alerta, Medicion, Nodo, Senal

from .api import _reducir
from .views import Tipo, VistaProtegida, _Eco

DIAS_POR_DEFECTO = 7
DIAS_MAXIMOS = 92  # un trimestre por consulta, para que la base de datos responda rápido
CALIDADES = [("", "Todas"), *Medicion.Calidad.choices]
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
    nodo_id = parametros.get("nodo", "")
    nodo = Nodo.objects.select_related("gateway__vehiculo").filter(pk=nodo_id).first() if nodo_id.isdigit() else None
    senal = Senal.objects.filter(codigo=parametros.get("senal", "")).first() if parametros.get("senal") else None
    calidad = parametros.get("calidad", "")
    if calidad not in dict(CALIDADES):
        calidad = ""
    zona = timezone.get_current_timezone()
    return {
        "desde": desde, "hasta": hasta, "nodo": nodo, "senal": senal, "calidad": calidad, "avisos": avisos,
        "inicio": timezone.make_aware(datetime.combine(desde, time.min), zona),
        "fin": timezone.make_aware(datetime.combine(hasta + timedelta(days=1), time.min), zona),
    }


def parametros_url(f):
    """Los filtros como texto para enlaces (paginación y exportación)."""
    datos = {"desde": f["desde"].isoformat(), "hasta": f["hasta"].isoformat()}
    if f["nodo"]:
        datos["nodo"] = f["nodo"].pk
    if f["senal"]:
        datos["senal"] = f["senal"].codigo
    if f["calidad"]:
        datos["calidad"] = f["calidad"]
    return urlencode(datos)


def mediciones(f):
    consulta = Medicion.objects.filter(ts__gte=f["inicio"], ts__lt=f["fin"])
    if f["nodo"]:
        consulta = consulta.filter(nodo=f["nodo"])
    if f["senal"]:
        consulta = consulta.filter(senal=f["senal"])
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

        contexto.update(
            f=f, parametros=parametros_url(f),
            nodos=Nodo.objects.select_related("gateway__vehiculo").order_by("gateway_id", "numero"),
            senales=Senal.objects.filter(visible=True).order_by("subsistema", "orden"),
            calidades=CALIDADES, exportables=EXPORTABLES,
            resumen=resumen, porcentaje_validas=round(100 * resumen["validas"] / resumen["n"]) if resumen["n"] else None,
            estadisticas=estadisticas, grafica=self._grafica(f, consulta),
            puede_exportar=self.request.user.has_perm("telemetria.exportar_datos"),
        )
        return contexto

    def _grafica(self, f, consulta):
        """Solo hay gráfica cuando se elige una señal y un nodo (si no, se mezclarían unidades distintas)."""
        if not (f["senal"] and f["nodo"]):
            return None
        validas = consulta.filter(valor__isnull=False)
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
            "nodo": str(f["nodo"]) if f["nodo"] else "todos", "senal": f["senal"].codigo if f["senal"] else "todas",
            "calidad": f["calidad"] or "todas",
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
        if f["senal"]:
            consulta = consulta.filter(senal=f["senal"])
        consulta = consulta.select_related("nodo__gateway__vehiculo", "gateway__vehiculo", "senal", "atendida_por").order_by("creada")
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
        if f["nodo"]:
            consulta = consulta.filter(vehiculo=f["nodo"].gateway.vehiculo)
        if f["senal"]:
            consulta = consulta.filter(senal=f["senal"])
        consulta = consulta.select_related("vehiculo", "senal", "responsable").order_by("creada")
        encabezados = ["folio", "vehiculo", "falla", "etapa", "resultado", "senal_validacion", "responsable", "creada_utc",
                       "reparacion_utc", "cerrada_utc", "intentos", "diagnostico", "solucion"]
        filas = ([o.folio, o.vehiculo.identificador, o.titulo, o.etapa, o.resultado, o.senal.codigo if o.senal else "",
                  o.responsable.get_username() if o.responsable else "", _iso(o.creada), _iso(o.validacion_inicio),
                  _iso(o.cerrada_en), o.intentos, o.diagnostico, o.solucion] for o in consulta.iterator())
        return encabezados, filas