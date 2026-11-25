"""
Diagnóstico probable de cada alerta, con su nivel de certeza.

Son reglas fijas, escritas a partir de lo que mide el nodo. Cada regla propone una causa probable y, cuando
aplica, el tipo de componente sospechoso (para llenar sola la orden de servicio).

Niveles de certeza:
- PROBABLE: los datos apuntan claramente a una causa.
- SIN_DETERMINAR: hay síntoma, pero la causa puede ser varias cosas.
- CONFIRMADO: lo confirmó el técnico en la orden (no lo pone esta regla).
"""
from dataclasses import dataclass

from telemetria.models import Alerta

Certeza = Alerta.Certeza
Tipo = Alerta.Tipo


@dataclass(frozen=True)
class Diagnostico:
    texto: str
    certeza: str
    componente: str | None = None  # código del TipoComponente sospechoso


REGLAS = {
    ("ac.ventilador_rpm", Tipo.UMBRAL_BAJO): Diagnostico(
        "Ventilador del A/C detenido o frenado: motor, rodamientos o su alimentación.", Certeza.PROBABLE, "ventilador_ac"),
    ("ac.temperatura", Tipo.UMBRAL_ALTO): Diagnostico(
        "El A/C no enfría lo suficiente: revisar ventilador, filtro y carga de gas.", Certeza.SIN_DETERMINAR, "ventilador_ac"),
    ("ac.humedad", Tipo.UMBRAL_ALTO): Diagnostico(
        "Humedad alta en cabina: drenaje del evaporador tapado o A/C apagado.", Certeza.SIN_DETERMINAR),
    ("san.fuga", Tipo.UMBRAL_ALTO): Diagnostico(
        "Fuga en el sanitario: sellos de la guillotina o conexiones de la bomba de descarga.", Certeza.PROBABLE, "guillotina"),
    ("san.tanque_nivel", Tipo.UMBRAL_BAJO): Diagnostico(
        "Tanque de agua bajo: no se rellenó en terminal o falla la válvula de llenado.", Certeza.SIN_DETERMINAR,
        "valvula_llenado"),
    ("elec.voltaje", Tipo.UMBRAL_BAJO): Diagnostico(
        "Caída de voltaje: conexión floja, arnés dañado o batería débil.", Certeza.PROBABLE),
}

SENSOR = Diagnostico("El sensor no entrega lecturas válidas: sensor dañado o desconectado; no indica una falla "
                     "del autobús.", Certeza.PROBABLE)
SEGURIDAD = Diagnostico("Posible ataque o equipo manipulado: revisar el dispositivo y rotar su clave.", Certeza.PROBABLE)

# Cuando dos alertas del mismo nodo aparecen juntas, una explica a la otra.
VENTILADOR_POR_VOLTAJE = Diagnostico(
    "El ventilador se frenó por la caída de voltaje: revisar primero la alimentación, no el ventilador.",
    Certeza.PROBABLE)
TEMPERATURA_POR_VENTILADOR = Diagnostico(
    "El A/C no enfría porque el ventilador está detenido: revisar el ventilador.", Certeza.PROBABLE, "ventilador_ac")


def _activa(nodo, codigo):
    return nodo is not None and Alerta.objects.filter(
        nodo=nodo, senal__codigo=codigo, estado__in=Alerta.ACTIVAS, tipo__in=[Tipo.UMBRAL_ALTO, Tipo.UMBRAL_BAJO]
    ).exists()


def proponer(tipo, senal=None, nodo=None):
    """Diagnóstico para una alerta nueva (o None si no hay regla)."""
    if tipo == Tipo.SEGURIDAD:
        return SEGURIDAD
    if tipo == Tipo.SENSOR_FALLA:
        return SENSOR
    if senal is None:
        return None
    if senal.codigo == "ac.ventilador_rpm" and _activa(nodo, "elec.voltaje"):
        return VENTILADOR_POR_VOLTAJE
    if senal.codigo == "ac.temperatura" and _activa(nodo, "ac.ventilador_rpm"):
        return TEMPERATURA_POR_VENTILADOR
    return REGLAS.get((senal.codigo, tipo))


def aplicar(alerta):
    """Llena el diagnóstico y la certeza de la alerta (sin guardarla). Devuelve el diagnóstico o None."""
    diagnostico = proponer(alerta.tipo, alerta.senal, alerta.nodo)
    if diagnostico:
        alerta.diagnostico = diagnostico.texto
        alerta.certeza = diagnostico.certeza
    return diagnostico


def componente_sugerido(alerta):
    """Código del tipo de componente sospechoso de una alerta, para llenar la orden de servicio."""
    if alerta is None:
        return None
    diagnostico = proponer(alerta.tipo, alerta.senal, alerta.nodo)
    return diagnostico.componente if diagnostico else None
