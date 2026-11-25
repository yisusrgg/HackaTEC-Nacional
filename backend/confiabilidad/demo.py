"""
Catálogo inicial y datos de ejemplo de confiabilidad (los carga `python manage.py inicializar`).

- cargar_catalogo(): tipos de componente y causas de falla. Respeta lo que Ingeniería haya editado.
- cargar_demo(): región y modelo de las unidades de ejemplo, el uso acumulado de sus piezas y un año de fallas
  confirmadas. Todo es fijo (semilla constante) para que la demo sea igual en todas las computadoras, e idempotente.

Los fabricantes son genéricos («Proveedor A»…): son datos de ejemplo, no de proveedores reales.
La historia que cuentan los datos: la guillotina del sanitario falla antes de tiempo en las regiones de clima
cálido húmedo (sellos degradados), y la bomba de descarga falla cuando se opera con el tanque vacío.
"""
import random
from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from telemetria.models import Alerta, Region, Vehiculo

from . import diagnostico
from .models import Causa, ComponenteInstalado, Falla, Regla, TipoComponente, TipoMedicion, UnidadVida
from .servicios import instalar_faltantes

AC, SAN, LUZ, ELEC = "AC", "SAN", "LUZ", "ELEC"

TIPOS = [
    dict(codigo="ventilador_ac", nombre="Ventilador del evaporador (A/C)", subsistema=AC, fabricante="Proveedor A",
         modelo="VE-18", unidad_vida=UnidadVida.HORAS, vida_garantizada=12000, regla=Regla.VENTILADOR,
         medicion=TipoMedicion.MEDIDO),
    dict(codigo="bomba_descarga", nombre="Bomba de descarga del sanitario", subsistema=SAN, fabricante="Proveedor B",
         modelo="BD-43", unidad_vida=UnidadVida.CICLOS, vida_garantizada=40000, regla=Regla.USOS_SANITARIO),
    dict(codigo="guillotina", nombre="Guillotina del sanitario", subsistema=SAN, fabricante="Proveedor C",
         modelo="GV-30", unidad_vida=UnidadVida.CICLOS, vida_garantizada=30000, regla=Regla.USOS_SANITARIO),
    dict(codigo="valvula_llenado", nombre="Válvula de llenado del tanque", subsistema=SAN, fabricante="Proveedor B",
         modelo="VL-12", unidad_vida=UnidadVida.CICLOS, vida_garantizada=6000, regla=Regla.LLENADO_TANQUE),
    dict(codigo="lamparas_interior", nombre="Lámparas LED interiores", subsistema=LUZ, fabricante="Proveedor D",
         modelo="LED-2J", unidad_vida=UnidadVida.HORAS, vida_garantizada=30000, regla=Regla.LUZ_INTERIOR),
]

CAUSAS = [
    ("AC-MOT", "Motor del ventilador quemado", AC),
    ("AC-ROD", "Rodamientos desgastados", AC),
    ("AC-ALI", "Falla de alimentación eléctrica", AC),
    ("AC-OBS", "Evaporador sucio u obstruido", AC),
    ("SAN-SEL", "Sellos degradados por humedad y calor", SAN),
    ("SAN-SAR", "Sarro o sedimento en el mecanismo", SAN),
    ("SAN-SEC", "Bomba operada en seco (tanque vacío)", SAN),
    ("SAN-MEC", "Desgaste mecánico", SAN),
    ("SAN-OBJ", "Objeto extraño o uso indebido", SAN),
    ("LUZ-DRV", "Falla del controlador LED", LUZ),
    ("LUZ-VIB", "Conexión suelta por vibración", LUZ),
    ("ELE-ARN", "Arnés dañado", ELEC),
    ("ELE-CON", "Conector corroído", ELEC),
    ("GEN-FAB", "Defecto de fabricación", ""),
    ("GEN-INS", "Instalación incorrecta", ""),
    ("GEN-SEN", "Sensor dañado (no era falla del componente)", ""),
]

# Datos de las unidades de ejemplo: (región, modelo, año de fabricación)
UNIDADES = {
    "BUS-DEMO-001": (Region.CENTRO, "Volvo 9700", 2024),
    "ADO-1001": (Region.GOLFO, "Volvo 9800", 2022), "ADO-1002": (Region.SURESTE, "Volvo 9800", 2023),
    "ADO-1003": (Region.GOLFO, "Volvo 9700", 2021),
    "ETN-2001": (Region.OCCIDENTE, "Volvo 9800", 2022), "ETN-2002": (Region.CENTRO, "Volvo 9800", 2023),
    "EB-3001": (Region.CENTRO, "Volvo 9700", 2020), "EB-3002": (Region.NORTE, "Volvo 9700", 2021),
    "EB-3003": (Region.CENTRO, "Volvo 9700", 2024),
    "PP-4001": (Region.OCCIDENTE, "Volvo 9700", 2021), "PP-4002": (Region.OCCIDENTE, "Volvo 9700", 2022),
    "TAP-5001": (Region.PACIFICO, "Volvo 9700", 2020), "TAP-5002": (Region.PACIFICO, "Volvo 9800", 2022),
    "SEN-6001": (Region.NORESTE, "Volvo 9700", 2021), "SEN-6002": (Region.NORESTE, "Volvo 9700", 2023),
    "OMB-7001": (Region.NORTE, "Volvo 9700", 2020), "OMB-7002": (Region.NORTE, "Volvo 9700", 2021),
}
CALIDO_HUMEDO = {Region.GOLFO, Region.PACIFICO, Region.SURESTE}

# Vida consumida fija para que la demo tenga piezas cerca de su límite (el resto se reparte entre 10 % y 70 %).
VIDA_FIJA = {
    ("ADO-1001", "guillotina"): 0.88, ("TAP-5002", "guillotina"): 0.93, ("ETN-2001", "ventilador_ac"): 0.84,
    ("OMB-7002", "ventilador_ac"): 1.04, ("PP-4001", "bomba_descarga"): 0.81, ("SEN-6001", "lamparas_interior"): 0.86,
}

# Fallas del último año: (tipo, regiones donde ocurre o None = cualquiera, rango de % de garantía, causas, cuántas)
HISTORIA = [
    ("guillotina", CALIDO_HUMEDO, (24, 47), ["SAN-SEL", "SAN-SEL", "SAN-SAR"], 8),
    ("guillotina", None, (72, 112), ["SAN-MEC", "SAN-OBJ"], 3),
    ("bomba_descarga", None, (22, 44), ["SAN-SEC"], 3),
    ("bomba_descarga", None, (78, 125), ["SAN-MEC", "GEN-FAB"], 5),
    ("ventilador_ac", {Region.NORTE, Region.NORESTE}, (30, 45), ["AC-ALI"], 1),
    ("ventilador_ac", None, (84, 132), ["AC-ROD", "AC-MOT", "AC-OBS"], 6),
    ("valvula_llenado", None, (40, 48), ["SAN-SAR"], 1),
    ("valvula_llenado", None, (58, 108), ["SAN-MEC", "SAN-SAR"], 5),
    ("lamparas_interior", None, (88, 145), ["LUZ-DRV", "LUZ-VIB"], 5),
]


@transaction.atomic
def cargar_catalogo():
    """Crea los tipos de componente y las causas que falten. Devuelve (tipos nuevos, causas nuevas)."""
    tipos = sum(TipoComponente.objects.get_or_create(codigo=t["codigo"], defaults={k: v for k, v in t.items()
                                                                                    if k != "codigo"})[1]
                for t in TIPOS)
    causas = sum(Causa.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre, "subsistema": subsistema})[1]
                 for codigo, nombre, subsistema in CAUSAS)
    return tipos, causas


def _usos_iniciales(componente, fraccion, rnd):
    vida = componente.tipo.vida_garantizada
    if componente.tipo.unidad_vida == UnidadVida.HORAS:
        componente.horas = round(vida * fraccion, 1)
        componente.ciclos = int(componente.horas / rnd.uniform(2.5, 4.0))  # arranques
    else:
        componente.ciclos = int(vida * fraccion)


@transaction.atomic
def cargar_demo():
    """Datos de ejemplo de las unidades demo. Devuelve un resumen de lo que creó."""
    ahora = timezone.now()
    vehiculos = {v.identificador: v for v in Vehiculo.objects.filter(identificador__in=UNIDADES)}
    tipos = {t.codigo: t for t in TipoComponente.objects.all()}
    resumen = {"unidades": 0, "instalados": 0, "fallas": 0, "diagnosticos": 0}

    for identificador, vehiculo in vehiculos.items():
        region, modelo, anio = UNIDADES[identificador]
        if not vehiculo.region:
            vehiculo.region, vehiculo.modelo, vehiculo.fecha_fabricacion = region, modelo, date(anio, 3, 1)
            vehiculo.save(update_fields=["region", "modelo", "fecha_fabricacion"])
            resumen["unidades"] += 1
    # Toda unidad (de ejemplo o no) lleva un componente de cada tipo del catálogo.
    for vehiculo in Vehiculo.objects.all():
        fabricacion = (timezone.make_aware(datetime.combine(vehiculo.fecha_fabricacion, time.min))
                       if vehiculo.fecha_fabricacion else ahora)
        resumen["instalados"] += instalar_faltantes(vehiculo, cuando=fabricacion)

    if Falla.objects.exists() or not tipos:
        resumen["diagnosticos"] = _diagnosticar_alertas()
        return resumen

    rnd = random.Random(2026)
    candidatos = [v for k, v in vehiculos.items() if k != "BUS-DEMO-001"]  # la unidad real cuenta desde cero
    ultima_falla = {}  # (vehiculo, tipo) -> (fecha, falla)
    for codigo, regiones, (minimo, maximo), causas, cuantas in HISTORIA:
        tipo = tipos.get(codigo)
        posibles = [v for v in candidatos if regiones is None or v.region in regiones]
        if tipo is None or not posibles:
            continue
        for _ in range(cuantas):
            vehiculo = rnd.choice(posibles)
            # Las prematuras de la guillotina se concentran en los últimos meses: la tendencia va al alza.
            dias = rnd.randint(5, 150) if codigo == "guillotina" and maximo < 50 else rnd.randint(10, 360)
            fecha = ahora - timedelta(days=dias, hours=rnd.randint(0, 23))
            porcentaje = round(rnd.uniform(minimo, maximo), 1)
            uso = tipo.vida_garantizada * porcentaje / 100
            retirado = ComponenteInstalado.objects.create(
                tipo=tipo, vehiculo=vehiculo, numero_serie=f"{tipo.modelo}-{rnd.randint(10000, 99999)}",
                instalado_en=fecha - timedelta(days=rnd.randint(200, 900)), retirado_en=fecha,
                ciclos=int(uso) if tipo.unidad_vida == UnidadVida.CICLOS else int(uso / 3),
                horas=round(uso, 1) if tipo.unidad_vida == UnidadVida.HORAS else 0)
            falla = Falla.objects.create(
                componente=retirado, causa=Causa.objects.get(codigo=rnd.choice(causas)), fecha=fecha,
                uso_al_fallar=round(uso, 1), porcentaje_garantia=porcentaje,
                momento=Falla.clasificar(porcentaje, tipo.limite_prematura), confirmada=True)
            clave = (vehiculo.pk, tipo.pk)
            if clave not in ultima_falla or ultima_falla[clave][0] < fecha:
                ultima_falla[clave] = (fecha, falla)
            resumen["fallas"] += 1

    for componente in (ComponenteInstalado.objects.activos().select_related("tipo", "vehiculo")
                       .filter(vehiculo__in=candidatos)):
        semilla = random.Random(f"{componente.vehiculo.identificador}:{componente.tipo.codigo}")
        reemplazo = ultima_falla.get((componente.vehiculo_id, componente.tipo_id))
        if reemplazo:
            # La pieza actual es la que se instaló cuando falló la anterior: lleva poco uso.
            fecha, falla = reemplazo
            componente.instalado_en = fecha
            componente.numero_serie = f"{componente.tipo.modelo}-{semilla.randint(10000, 99999)}"
            fraccion = min(0.3, (ahora - fecha).days / 1500)
            falla.reemplazo = componente
            falla.save(update_fields=["reemplazo"])
        else:
            if componente.vehiculo.fecha_fabricacion:  # la pieza original: viene de fábrica
                componente.instalado_en = timezone.make_aware(
                    datetime.combine(componente.vehiculo.fecha_fabricacion, time.min))
            componente.numero_serie = componente.numero_serie or f"{componente.tipo.modelo}-{semilla.randint(10000, 99999)}"
            fraccion = VIDA_FIJA.get((componente.vehiculo.identificador, componente.tipo.codigo),
                                     semilla.uniform(0.10, 0.70))
        _usos_iniciales(componente, fraccion, semilla)
        componente.save(update_fields=["instalado_en", "numero_serie", "ciclos", "horas"])

    resumen["diagnosticos"] = _diagnosticar_alertas()
    return resumen


def _diagnosticar_alertas():
    """Agrega el diagnóstico probable a las alertas activas que se crearon antes de que existiera."""
    n = 0
    for alerta in Alerta.objects.filter(estado__in=Alerta.ACTIVAS, diagnostico="").select_related("senal", "nodo"):
        if diagnostico.aplicar(alerta):
            alerta.save(update_fields=["diagnostico", "certeza"])
            n += 1
    return n
