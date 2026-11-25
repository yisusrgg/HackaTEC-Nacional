"""Filtros de flota que comparten varias pantallas: operador, región y modelo de autobús (y periodo)."""
from urllib.parse import urlencode

from telemetria.models import Cliente, Region, Vehiculo


def leer(parametros, periodos=None, periodo_inicial=None):
    """?operador=<id>&region=<clave>&modelo=<texto>&dias=<n>. Lo que no es válido se ignora."""
    operador_id = parametros.get("operador", "")
    region = parametros.get("region", "")
    modelo = parametros.get("modelo", "").strip()[:60]
    f = {
        "operador": Cliente.objects.filter(pk=operador_id).first() if operador_id.isdigit() else None,
        "region": region if region in Region.values else "",
        "modelo": modelo if modelo in modelos() else "",
    }
    if periodos:
        try:
            dias = int(parametros.get("dias", periodo_inicial))
        except (TypeError, ValueError):
            dias = periodo_inicial
        f["dias"] = dias if dias in dict(periodos) else periodo_inicial
    return f


def modelos():
    return list(Vehiculo.objects.exclude(modelo="").values_list("modelo", flat=True).distinct().order_by("modelo"))


def vehiculos(f, consulta=None):
    consulta = consulta if consulta is not None else Vehiculo.objects.all()
    if f.get("operador"):
        consulta = consulta.filter(cliente=f["operador"])
    if f.get("region"):
        consulta = consulta.filter(region=f["region"])
    if f.get("modelo"):
        consulta = consulta.filter(modelo=f["modelo"])
    return consulta


def hay_filtros(f):
    return bool(f.get("operador") or f.get("region") or f.get("modelo"))


def a_url(f, **extra):
    datos = {"operador": f["operador"].pk if f.get("operador") else "", "region": f.get("region", ""),
             "modelo": f.get("modelo", ""), **extra}
    return urlencode({k: v for k, v in datos.items() if v not in ("", None)})


def contexto(f):
    """Opciones de los selectores para la plantilla _filtros_flota.html."""
    return {
        "f": f,
        "operadores": Cliente.objects.filter(vehiculos__isnull=False).distinct().order_by("nombre_comercial"),
        "regiones": Region.choices,
        "modelos": modelos(),
    }
