"""
Conteo de ciclos y horas de los componentes a partir de las lecturas que ya manda el nodo.

Se llama al procesar cada lote (telemetria.ingesta). Por nodo y por regla guarda el último estado visto
(EstadoConteo), así un arranque se cuenta bien aunque el «antes» y el «después» lleguen en lotes distintos.

Reglas:
- VENTILADOR: arranque = ac.ventilador_rpm pasa de menos de 300 a 300 o más; horas = tiempo con 300 rpm o más.
- LUZ_INTERIOR: encendido = luz.interior pasa de menos de 50 % a 50 % o más; horas = tiempo encendida.
- USOS_SANITARIO: san.usos es un contador del nodo que vuelve a 0 cuando el nodo se reinicia. Se suman las
  diferencias; si el valor baja, el nodo se reinició y se suma el valor nuevo completo.
- LLENADO_TANQUE: un llenado = san.tanque_nivel baja de 40 % y después sube de 80 %.

Solo se usan lecturas válidas. Un hueco de más de HUECO_MAXIMO entre dos lecturas no suma horas (el autobús
pudo estar apagado o sin señal) y las lecturas fuera de orden se ignoran.
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import F

from telemetria.models import Medicion

from .models import ComponenteInstalado, EstadoConteo, Regla

HUECO_MAXIMO = timedelta(minutes=10)
UMBRAL_VENTILADOR_RPM = 300
UMBRAL_LUZ = 50
TANQUE_BAJO, TANQUE_LLENO = 40, 80

SENAL_DE_REGLA = {
    "ac.ventilador_rpm": Regla.VENTILADOR,
    "luz.interior": Regla.LUZ_INTERIOR,
    "san.usos": Regla.USOS_SANITARIO,
    "san.tanque_nivel": Regla.LLENADO_TANQUE,
}


def _paso(regla, estado, valor, ts):
    """Aplica una lectura al estado guardado. Devuelve (ciclos, horas) que se suman."""
    ciclos, horas = 0, 0.0
    if regla in (Regla.VENTILADOR, Regla.LUZ_INTERIOR):
        umbral = UMBRAL_VENTILADOR_RPM if regla == Regla.VENTILADOR else UMBRAL_LUZ
        encendido = valor >= umbral
        if encendido and estado.encendido and estado.ts is not None:
            hueco = ts - estado.ts
            if hueco <= HUECO_MAXIMO:
                horas = hueco.total_seconds() / 3600
        if encendido and not estado.encendido:
            ciclos = 1
        estado.encendido = encendido
    elif regla == Regla.USOS_SANITARIO:
        usos = int(valor)
        if estado.valor is not None:
            anterior = int(estado.valor)
            ciclos = usos - anterior if usos >= anterior else usos  # si bajó, el nodo se reinició
    elif regla == Regla.LLENADO_TANQUE:
        if valor < TANQUE_BAJO:
            estado.encendido = True  # aquí «encendido» significa «el tanque estuvo bajo»
        elif valor > TANQUE_LLENO and estado.encendido:
            ciclos = 1
            estado.encendido = False
    estado.valor = valor
    estado.ts = ts
    return ciclos, horas


def contar(mediciones):
    """Suma ciclos y horas a los componentes instalados según las lecturas válidas del lote."""
    lecturas = defaultdict(list)  # (nodo, regla) -> [(ts, valor)]
    for m in mediciones:
        regla = SENAL_DE_REGLA.get(m.senal.codigo)
        if regla and m.calidad == Medicion.Calidad.OK and m.valor is not None:
            lecturas[(m.nodo, regla)].append((m.ts, m.valor))
    if not lecturas:
        return {}

    nodos = {nodo.pk for nodo, _ in lecturas}
    estados = {(e.nodo_id, e.regla): e for e in EstadoConteo.objects.filter(nodo_id__in=nodos)}
    sumas = defaultdict(lambda: [0, 0.0])  # (vehiculo_id, regla) -> [ciclos, horas]
    for (nodo, regla), filas in lecturas.items():
        estado = estados.get((nodo.pk, regla)) or EstadoConteo(nodo=nodo, regla=regla)
        for ts, valor in sorted(filas, key=lambda fila: fila[0]):
            if estado.ts is not None and ts <= estado.ts:
                continue
            ciclos, horas = _paso(regla, estado, valor, ts)
            suma = sumas[(nodo.gateway.vehiculo_id, regla)]
            suma[0] += ciclos
            suma[1] += horas
        estado.save()

    for (vehiculo_id, regla), (ciclos, horas) in sumas.items():
        if ciclos or horas:
            ComponenteInstalado.objects.activos().filter(vehiculo_id=vehiculo_id, tipo__regla=regla).update(
                ciclos=F("ciclos") + ciclos, horas=F("horas") + horas)
    return {clave: tuple(valor) for clave, valor in sumas.items()}
