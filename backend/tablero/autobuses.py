"""Autobuses: todos los autobuses con sus dispositivos (gateway y nodos), filtros y acceso al monitoreo en vivo.

Filtros por URL (se ignoran los valores no válidos):
  ?cliente=<id>  ?alertas=con|criticas|sin  ?conexion=en_linea|fuera  ?q=<identificador o nombre>
"""
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Prefetch, Q
from django.utils import timezone
from django.views.generic import ListView

from telemetria.models import Alerta, Cliente, Gateway, Nodo, Severidad, Vehiculo

from .flota import _alertas_por_vehiculo, _estado_autobus
from .views import VistaProtegida

FILTRO_ALERTAS = [("", "Todos"), ("con", "Con alertas activas"), ("criticas", "Con alertas críticas"),
                  ("sin", "Sin alertas")]
FILTRO_CONEXION = [("", "Todas"), ("en_linea", "En línea"), ("fuera", "Fuera de línea")]


def _vehiculos_con_alertas(solo_criticas=False):
    activas = Alerta.objects.filter(estado__in=Alerta.ACTIVAS)
    if solo_criticas:
        activas = activas.filter(severidad=Severidad.CRITICA)
    ids = set(activas.filter(gateway__isnull=False).values_list("gateway__vehiculo_id", flat=True))
    ids |= set(activas.filter(nodo__isnull=False).values_list("nodo__gateway__vehiculo_id", flat=True))
    return ids


def leer_filtros(parametros):
    cliente_id = parametros.get("cliente", "")
    alertas = parametros.get("alertas", "")
    conexion = parametros.get("conexion", "")
    return {
        "cliente": Cliente.objects.filter(pk=cliente_id).first() if cliente_id.isdigit() else None,
        "alertas": alertas if alertas in dict(FILTRO_ALERTAS) else "",
        "conexion": conexion if conexion in dict(FILTRO_CONEXION) else "",
        "q": parametros.get("q", "").strip()[:40],
    }


def filtrar(consulta, f):
    if f["cliente"]:
        consulta = consulta.filter(cliente=f["cliente"])
    if f["q"]:
        consulta = consulta.filter(Q(identificador__icontains=f["q"]) | Q(nombre__icontains=f["q"]))
    if f["alertas"] == "con":
        consulta = consulta.filter(pk__in=_vehiculos_con_alertas())
    elif f["alertas"] == "criticas":
        consulta = consulta.filter(pk__in=_vehiculos_con_alertas(solo_criticas=True))
    elif f["alertas"] == "sin":
        consulta = consulta.exclude(pk__in=_vehiculos_con_alertas())
    if f["conexion"]:
        limite = timezone.now() - timedelta(seconds=settings.TELEMETRIA["SEGUNDOS_EN_LINEA"])
        en_linea = Q(gateways__activo=True, gateways__ultimo_contacto__gte=limite)
        consulta = consulta.filter(en_linea) if f["conexion"] == "en_linea" else consulta.exclude(en_linea)
    return consulta.distinct()


def parametros_url(f):
    datos = {"cliente": f["cliente"].pk if f["cliente"] else "", "alertas": f["alertas"],
             "conexion": f["conexion"], "q": f["q"]}
    return urlencode({k: v for k, v in datos.items() if v})


class AutobusesView(VistaProtegida, ListView):
    permission_required = "telemetria.ver_tablero"
    template_name = "tablero/autobuses.html"
    context_object_name = "vehiculos"
    paginate_by = 10

    def get_queryset(self):
        self.filtros = leer_filtros(self.request.GET)
        gateways = Gateway.objects.prefetch_related(Prefetch("nodos", queryset=Nodo.objects.order_by("numero")))
        consulta = Vehiculo.objects.select_related("cliente").prefetch_related(Prefetch("gateways", queryset=gateways))
        return filtrar(consulta, self.filtros).order_by("cliente__nombre_comercial", "identificador")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        alertas, _ = _alertas_por_vehiculo()
        contexto.update(
            autobuses=[_estado_autobus(v, alertas.get(v.pk, [])) for v in contexto["vehiculos"]],
            f=self.filtros,
            parametros=parametros_url(self.filtros),
            clientes=Cliente.objects.filter(vehiculos__isnull=False).distinct().order_by("nombre_comercial"),
            filtro_alertas=FILTRO_ALERTAS,
            filtro_conexion=FILTRO_CONEXION,
            puede_gestionar=self.request.user.has_perm("telemetria.gestionar_dispositivos"),
        )
        return contexto
