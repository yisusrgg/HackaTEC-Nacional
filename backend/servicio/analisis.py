"""
Indicadores de calidad para Ingeniería: qué falla más, en qué unidades, cuánto tarda la reparación,
si las reparaciones funcionan a la primera y qué tan confiables son los datos de cada sensor.

Todo se calcula con los datos que ya existen (alertas, órdenes de servicio y mediciones).
"""
from collections import Counter
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone

from telemetria.models import Alerta, Medicion, Senal, Severidad

from .models import OrdenServicio

PERIODOS = [(7, "Últimos 7 días"), (30, "Últimos 30 días"), (90, "Últimos 90 días")]
CALIDAD_MINIMA = 95  # % de lecturas válidas por debajo del cual un sensor se marca como poco confiable


def duracion_texto(segundos):
    """3 h 20 min, 2 d 4 h, 12 min…"""
    if segundos is None:
        return "—"
    minutos = int(round(segundos / 60))
    if minutos < 60:
        return f"{minutos} min"
    horas, minutos = divmod(minutos, 60)
    if horas < 24:
        return f"{horas} h {minutos} min" if minutos else f"{horas} h"
    dias, horas = divmod(horas, 24)
    return f"{dias} d {horas} h" if horas else f"{dias} d"


def _promedio_segundos(pares):
    duraciones = [(fin - inicio).total_seconds() for inicio, fin in pares if inicio and fin and fin >= inicio]
    return sum(duraciones) / len(duraciones) if duraciones else None


def _porcentaje(parte, total):
    return round(100 * parte / total) if total else 0


def calcular(dias=30, vehiculo=None, subsistema=None, ahora=None):
    ahora = ahora or timezone.now()
    desde = ahora - timedelta(days=dias)

    # --- Fallas: alertas del periodo (las de seguridad van en la bitácora, no aquí) ---
    alertas = Alerta.objects.filter(creada__gte=desde, creada__lte=ahora).exclude(tipo=Alerta.Tipo.SEGURIDAD)
    ordenes = OrdenServicio.objects.filter(creada__gte=desde, creada__lte=ahora)
    mediciones = Medicion.objects.filter(ts__gte=desde, ts__lte=ahora).exclude(senal__subsistema=Senal.Subsistema.NODO)
    if vehiculo:
        alertas = alertas.filter(nodo__gateway__vehiculo=vehiculo)
        ordenes = ordenes.filter(vehiculo=vehiculo)
        mediciones = mediciones.filter(nodo__gateway__vehiculo=vehiculo)
    if subsistema:
        alertas = alertas.filter(senal__subsistema=subsistema)
        ordenes = ordenes.filter(senal__subsistema=subsistema)
        mediciones = mediciones.filter(senal__subsistema=subsistema)

    total_fallas = alertas.count()
    criticas = alertas.filter(severidad=Severidad.CRITICA).count()
    nombres_subsistema = dict(Senal.Subsistema.choices)

    # Pareto: qué señales generan más fallas
    pareto, acumulado = [], 0
    for fila in (alertas.filter(senal__isnull=False).values("senal__nombre", "senal__subsistema")
                 .annotate(n=Count("id")).order_by("-n", "senal__nombre")[:8]):
        acumulado += fila["n"]
        pareto.append({
            "nombre": fila["senal__nombre"], "subsistema": nombres_subsistema.get(fila["senal__subsistema"], ""),
            "n": fila["n"], "porcentaje": _porcentaje(fila["n"], total_fallas),
            "acumulado": _porcentaje(acumulado, total_fallas),
        })

    por_subsistema = [
        {"nombre": nombres_subsistema.get(fila["senal__subsistema"], "Sin señal"), "n": fila["n"],
         "porcentaje": _porcentaje(fila["n"], total_fallas)}
        for fila in alertas.values("senal__subsistema").annotate(n=Count("id")).order_by("-n")
    ]

    # Fallas por día, con los días sin fallas en cero. Se agrupa en Python para no depender de las tablas de
    # zonas horarias de MySQL (sin ellas, agrupar por fecha local en la base de datos devuelve vacío).
    conteo_dias = Counter(timezone.localdate(creada) for creada in alertas.values_list("creada", flat=True))
    hoy = timezone.localdate(ahora)
    tendencia = [{"dia": (hoy - timedelta(days=i)).isoformat(), "n": conteo_dias.get(hoy - timedelta(days=i), 0)}
                 for i in range(dias - 1, -1, -1)]

    # --- Servicio: tiempos y efectividad de las reparaciones ---
    tiempo_atencion = _promedio_segundos(alertas.filter(atendida_en__isnull=False).values_list("creada", "atendida_en"))
    cerradas = ordenes.filter(etapa=OrdenServicio.Etapa.CERRADA)
    tiempo_reparacion = _promedio_segundos(cerradas.values_list("creada", "cerrada_en"))
    total_cerradas = cerradas.count()
    a_la_primera = cerradas.filter(intentos=0).count()
    reincidencias = ordenes.aggregate(total=Sum("intentos"))["total"] or 0

    # --- Por unidad ---
    unidades = {}
    for fila in (alertas.filter(nodo__isnull=False)
                 .values("nodo__gateway__vehiculo", "nodo__gateway__vehiculo__identificador", "nodo__gateway__vehiculo__nombre")
                 .annotate(fallas=Count("id"), criticas=Count("id", filter=Q(severidad=Severidad.CRITICA)))):
        unidades[fila["nodo__gateway__vehiculo"]] = {
            "identificador": fila["nodo__gateway__vehiculo__identificador"], "nombre": fila["nodo__gateway__vehiculo__nombre"],
            "fallas": fila["fallas"], "criticas": fila["criticas"], "ordenes": 0, "a_la_primera": 0, "reincidencias": 0,
        }
    for fila in (ordenes.values("vehiculo", "vehiculo__identificador", "vehiculo__nombre")
                 .annotate(n=Count("id"), primera=Count("id", filter=Q(etapa=OrdenServicio.Etapa.CERRADA, intentos=0)),
                           reinc=Sum("intentos"))):
        unidad = unidades.setdefault(fila["vehiculo"], {
            "identificador": fila["vehiculo__identificador"], "nombre": fila["vehiculo__nombre"],
            "fallas": 0, "criticas": 0, "ordenes": 0, "a_la_primera": 0, "reincidencias": 0,
        })
        unidad.update(ordenes=fila["n"], a_la_primera=fila["primera"], reincidencias=fila["reinc"] or 0)
    por_unidad = sorted(unidades.values(), key=lambda u: (-u["fallas"], u["identificador"]))

    # --- Calidad de los datos de cada sensor ---
    calidad = []
    for fila in (mediciones.values("senal__nombre", "senal__subsistema", "senal__orden")
                 .annotate(total=Count("id"),
                           ok=Count("id", filter=Q(calidad=Medicion.Calidad.OK)),
                           sin_dato=Count("id", filter=Q(calidad=Medicion.Calidad.SIN_DATO)),
                           fuera=Count("id", filter=Q(calidad=Medicion.Calidad.FUERA_RANGO)))
                 .order_by("senal__subsistema", "senal__orden")):
        calidad.append({
            "nombre": fila["senal__nombre"], "subsistema": nombres_subsistema.get(fila["senal__subsistema"], ""),
            "total": fila["total"], "validas": _porcentaje(fila["ok"], fila["total"]),
            "sin_dato": _porcentaje(fila["sin_dato"], fila["total"]), "fuera": _porcentaje(fila["fuera"], fila["total"]),
        })

    resultado = {
        "desde": desde, "hasta": ahora, "dias": dias,
        "total_fallas": total_fallas, "criticas": criticas,
        "tiempo_atencion": duracion_texto(tiempo_atencion),
        "tiempo_reparacion": duracion_texto(tiempo_reparacion),
        "total_ordenes": ordenes.count(), "total_cerradas": total_cerradas,
        "a_la_primera": _porcentaje(a_la_primera, total_cerradas) if total_cerradas else None,
        "reincidencias": reincidencias,
        "pareto": pareto, "por_subsistema": por_subsistema, "tendencia": tendencia,
        "por_unidad": por_unidad, "calidad": calidad, "calidad_minima": CALIDAD_MINIMA,
    }
    resultado["hallazgos"] = hallazgos(resultado)
    return resultado


def hallazgos(r):
    """Frases cortas con lo más importante del periodo, para leer de un vistazo."""
    frases = []
    if not r["total_fallas"]:
        frases.append("No hubo fallas en el periodo elegido.")
    if r["pareto"]:
        primera = r["pareto"][0]
        frases.append(f"{primera['nombre']} concentra el {primera['porcentaje']} % de las fallas "
                      f"({primera['n']} de {r['total_fallas']}).")
    if len(r["por_unidad"]) > 1 and r["por_unidad"][0]["fallas"]:
        unidad = r["por_unidad"][0]
        frases.append(f"{unidad['identificador']} es la unidad con más fallas ({unidad['fallas']}).")
    if r["reincidencias"]:
        frases.append(f"La falla volvió {r['reincidencias']} {'vez' if r['reincidencias'] == 1 else 'veces'} después de "
                      "una reparación: conviene revisar la causa raíz.")
    if r["a_la_primera"] is not None and r["a_la_primera"] == 100:
        frases.append("Todas las reparaciones cerradas funcionaron a la primera.")
    malos = [c for c in r["calidad"] if c["validas"] < r["calidad_minima"]]
    if malos:
        peor = min(malos, key=lambda c: c["validas"])
        frases.append(f"El sensor de {peor['nombre'].lower()} solo entregó {peor['validas']} % de lecturas válidas: "
                      "revisa su conexión o calibración.")
    return frases