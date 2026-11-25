"""
Cálculos de confiabilidad: ¿los componentes duran lo que promete el fabricante?

Solo cuentan las fallas confirmadas (orden cerrada y, si la trabajó un técnico de convenio, aprobada por
Ingeniería). Todo se calcula en la base con fallas y contadores ya sumados, sin leer las lecturas crudas.
"""
from django.db.models import Avg, Count, F, Q

from telemetria.models import Region

from .models import ComponenteInstalado, Falla, TipoComponente

MUESTRA_MINIMA = 5  # con menos fallas, la pantalla avisa que la muestra es pequeña
CUBETAS = [(0, 25), (25, 50), (50, 75), (75, 100), (100, 125), (125, None)]
COMPARACIONES = [("region", "Región"), ("operador", "Operador"), ("modelo", "Modelo de autobús"),
                 ("fabricante", "Fabricante")]
NOMBRE_REGION = dict(Region.choices)

_CONTEOS = dict(
    n=Count("id"),
    vida=Avg("porcentaje_garantia"),
    tempranas=Count("id", filter=Q(momento=Falla.Momento.TEMPRANA)),
    normales=Count("id", filter=Q(momento=Falla.Momento.NORMAL)),
    tardias=Count("id", filter=Q(momento=Falla.Momento.TARDIA)),
)


def _pct(parte, total):
    return round(100 * parte / total) if total else 0


def fallas_confirmadas(vehiculos, desde=None, fabricante=""):
    consulta = Falla.objects.filter(confirmada=True, componente__vehiculo__in=vehiculos)
    if desde:
        consulta = consulta.filter(fecha__gte=desde)
    if fabricante:
        consulta = consulta.filter(componente__tipo__fabricante=fabricante)
    return consulta


def por_tipo(vehiculos, desde, mitad, fabricante=""):
    """Una fila por tipo de componente. `mitad` parte el periodo en dos para ver si las fallas suben o bajan."""
    tipos = TipoComponente.objects.all()
    if fabricante:
        tipos = tipos.filter(fabricante=fabricante)
    activos = {fila["tipo"]: fila for fila in ComponenteInstalado.objects.activos().filter(vehiculo__in=vehiculos)
               .con_vida().values("tipo").annotate(n=Count("id"), vida=Avg("vida_pct"))}
    fallas = fallas_confirmadas(vehiculos, desde, fabricante)
    stats = {fila["componente__tipo"]: fila for fila in fallas.values("componente__tipo").annotate(
        **_CONTEOS, recientes=Count("id", filter=Q(fecha__gte=mitad)), previas=Count("id", filter=Q(fecha__lt=mitad)))}
    filas = []
    for tipo in tipos:
        a = activos.get(tipo.pk, {})
        s = stats.get(tipo.pk, {})
        n = s.get("n", 0)
        recientes, previas = s.get("recientes", 0), s.get("previas", 0)
        tendencia = "igual" if recientes == previas else ("sube" if recientes > previas else "baja")
        filas.append({
            "tipo": tipo,
            "instalados": a.get("n", 0),
            "vida_media": round(a["vida"]) if a.get("vida") is not None else None,
            "fallas": n,
            "vida_a_la_falla": round(s["vida"]) if n else None,
            "tempranas": s.get("tempranas", 0), "normales": s.get("normales", 0), "tardias": s.get("tardias", 0),
            "pct_prematuras": _pct(s.get("tempranas", 0), n),
            "tendencia": tendencia if n else None,
            "muestra_chica": 0 < n < MUESTRA_MINIMA,
            "cumple": None if not n else s["vida"] >= 100,
        })
    # Primero los que más fallan antes de tiempo.
    filas.sort(key=lambda f: (-f["pct_prematuras"], -(f["fallas"]), f["tipo"].nombre))
    return filas


def pareto_causas(fallas):
    total = fallas.count()
    filas, acumulado = [], 0
    for fila in fallas.values("causa__nombre").annotate(n=Count("id")).order_by("-n", "causa__nombre")[:10]:
        acumulado += fila["n"]
        filas.append({"nombre": fila["causa__nombre"] or "Sin causa registrada", "n": fila["n"],
                      "porcentaje": _pct(fila["n"], total), "acumulado": _pct(acumulado, total)})
    return filas


def comparar(vehiculos, fallas, por):
    """Fallas por cada 100 unidades (o piezas, si se compara por fabricante) en cada grupo."""
    if por == "fabricante":
        campo_falla, base = "componente__tipo__fabricante", (
            ComponenteInstalado.objects.activos().filter(vehiculo__in=vehiculos)
            .values(grupo=F("tipo__fabricante")).annotate(n=Count("id")))
    else:
        campo = {"region": "region", "operador": "cliente__nombre_comercial", "modelo": "modelo"}[por]
        campo_falla = {"region": "componente__vehiculo__region", "operador": "componente__vehiculo__cliente__nombre_comercial",
                       "modelo": "componente__vehiculo__modelo"}[por]
        base = vehiculos.values(grupo=F(campo)).annotate(n=Count("id"))
    denominador = {fila["grupo"]: fila["n"] for fila in base}
    filas = []
    for fila in fallas.values(campo_falla).annotate(**_CONTEOS):
        grupo = fila[campo_falla]
        n_base = denominador.get(grupo, 0)
        filas.append({
            "grupo": NOMBRE_REGION.get(grupo, grupo) if por == "region" else (grupo or "Sin dato"),
            "base": n_base, "fallas": fila["n"], "tasa": round(100 * fila["n"] / n_base) if n_base else None,
            "tempranas": fila["tempranas"], "pct_prematuras": _pct(fila["tempranas"], fila["n"]),
            "vida_a_la_falla": round(fila["vida"]) if fila["vida"] is not None else None,
        })
    for grupo, n_base in denominador.items():  # grupos sin fallas también se muestran
        nombre = NOMBRE_REGION.get(grupo, grupo) if por == "region" else (grupo or "Sin dato")
        if not any(f["grupo"] == nombre for f in filas):
            filas.append({"grupo": nombre, "base": n_base, "fallas": 0, "tasa": 0, "tempranas": 0,
                          "pct_prematuras": 0, "vida_a_la_falla": None})
    filas.sort(key=lambda f: (-(f["tasa"] or 0), f["grupo"]))
    return filas


def histograma(fallas, limite_prematura=50):
    """Cuántas fallas hubo en cada tramo de vida (% de la garantía)."""
    valores = list(fallas.values_list("porcentaje_garantia", flat=True))
    maximo = 0
    cubetas = []
    for desde, hasta in CUBETAS:
        n = sum(1 for v in valores if v >= desde and (hasta is None or v < hasta))
        maximo = max(maximo, n)
        etiqueta = f"{desde}–{hasta} %" if hasta is not None else f"más de {desde} %"
        cubetas.append({"etiqueta": etiqueta, "n": n, "despues_de_garantia": desde >= 100,
                        "prematura": hasta is not None and hasta <= limite_prematura})
    return {"cubetas": cubetas, "maximo": maximo or 1, "total": len(valores)}


def reincidencias(tipo, vehiculos):
    """Unidades en las que este tipo de componente ha fallado dos veces o más."""
    return list(Falla.objects.filter(confirmada=True, componente__tipo=tipo, componente__vehiculo__in=vehiculos)
                .values("componente__vehiculo", "componente__vehiculo__identificador",
                        "componente__vehiculo__cliente__nombre_comercial", "componente__vehiculo__region")
                .annotate(n=Count("id"), prematuras=Count("id", filter=Q(momento=Falla.Momento.TEMPRANA)))
                .filter(n__gte=2).order_by("-n"))
