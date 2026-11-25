"""
Catálogo de señales del nodo de carrocería (el "DBC" del bus CAN propio).

Debe coincidir con firmware/nodo_sensores/nodo_sensores.ino y con docs/03-protocolo-can.md.
Todas las señales van en big-endian dentro de los 4 bytes de datos de la trama.
"""
from django.db import transaction

from .models import Senal

AC, SAN, LUZ, ELEC, NODO = "AC", "SAN", "LUZ", "ELEC", "NODO"

CATALOGO = [
    # --- 0x10 AC_AMBIENTE (DHT11) ---
    dict(codigo="ac.temperatura", nombre="Temperatura (A/C)", subsistema=AC, unidad="°C", decimales=1,
         mensaje=0x10, byte_inicio=0, longitud=2, con_signo=True, escala=0.1,
         rango_min=-20, rango_max=60, umbral_alto=30, orden=1),
    dict(codigo="ac.humedad", nombre="Humedad relativa (A/C)", subsistema=AC, unidad="%HR", decimales=1,
         mensaje=0x10, byte_inicio=2, longitud=2, escala=0.1, rango_min=0, rango_max=100, umbral_alto=80, orden=2),
    # --- 0x11 AC_VENTILADOR (HW-201 como tacómetro) ---
    dict(codigo="ac.ventilador_rpm", nombre="Ventilador del A/C", subsistema=AC, unidad="rpm", decimales=0,
         mensaje=0x11, byte_inicio=0, longitud=2, escala=1, rango_min=0, rango_max=20000, umbral_bajo=300, orden=3),
    # --- 0x20 SAN_TANQUE (HC-SR04) ---
    dict(codigo="san.tanque_nivel", nombre="Nivel del tanque de agua", subsistema=SAN, unidad="%", decimales=1,
         mensaje=0x20, byte_inicio=2, longitud=2, escala=0.1, rango_min=0, rango_max=100, umbral_bajo=20, orden=1),
    dict(codigo="san.tanque_distancia", nombre="Distancia sensor–agua", subsistema=SAN, unidad="mm", decimales=0,
         mensaje=0x20, byte_inicio=0, longitud=2, escala=1, rango_min=20, rango_max=4000, orden=2),
    # --- 0x21 SAN_ESTADO (HC-SR501: ocupado = hubo presencia en los últimos 15 s) ---
    dict(codigo="san.ocupado", nombre="Sanitario", subsistema=SAN, tipo="BOOL", decimales=0,
         mensaje=0x21, byte_inicio=0, longitud=1, escala=1, rango_min=0, rango_max=1,
         etiquetas={"0": "Libre", "1": "Ocupado"}, orden=3),
    dict(codigo="san.presencia", nombre="Presencia en el sanitario", subsistema=SAN, tipo="BOOL", decimales=0,
         mensaje=0x21, byte_inicio=1, longitud=1, escala=1, rango_min=0, rango_max=1,
         etiquetas={"0": "Sin movimiento", "1": "Movimiento"}, orden=4),
    dict(codigo="san.usos", nombre="Usos desde el arranque", subsistema=SAN, unidad="usos", decimales=0,
         mensaje=0x21, byte_inicio=2, longitud=2, escala=1, rango_min=0, orden=5),
    # --- 0x22 SAN_FUGA (sensor de humedad de suelo en la bandeja) ---
    dict(codigo="san.humedad_piso", nombre="Humedad en el piso", subsistema=SAN, unidad="%", decimales=0,
         mensaje=0x22, byte_inicio=0, longitud=2, escala=0.1, rango_min=0, rango_max=100, orden=6),
    dict(codigo="san.fuga", nombre="Fuga de agua", subsistema=SAN, tipo="BOOL", decimales=0,
         mensaje=0x22, byte_inicio=2, longitud=1, escala=1, rango_min=0, rango_max=1, umbral_alto=0.5,
         severidad_alerta="CRITICA", etiquetas={"0": "Sin fuga", "1": "Fuga detectada"}, orden=7),
    # --- 0x30 LUZ (1 módulo LDR analógico; los bytes 1-3 van en 0) ---
    dict(codigo="luz.interior", nombre="Iluminación interior", subsistema=LUZ, unidad="%", decimales=0,
         mensaje=0x30, byte_inicio=0, longitud=1, escala=1, rango_min=0, rango_max=100, orden=1),
    # --- 0x40 ALIMENTACION (divisor de voltaje) ---
    dict(codigo="elec.voltaje", nombre="Voltaje de alimentación", subsistema=ELEC, unidad="V", decimales=2,
         mensaje=0x40, byte_inicio=0, longitud=2, escala=0.001, rango_min=0, rango_max=36, umbral_bajo=4.5, orden=1),
    # --- 0xF0 ESTADO_NODO (latido) ---
    dict(codigo="nodo.uptime", nombre="Tiempo encendido", subsistema=NODO, unidad="min", decimales=0,
         mensaje=0xF0, byte_inicio=0, longitud=2, escala=1, orden=1),
    dict(codigo="nodo.fallas", nombre="Fallas de sensores (bits)", subsistema=NODO, decimales=0,
         mensaje=0xF0, byte_inicio=2, longitud=2, escala=1, orden=2),
]

# Señales que el firmware ya no envía. Se borran con sus mediciones: si se quedaran, el servidor seguiría
# decodificando esos bytes y el tablero mostraría valores que no vienen de ningún sensor.
SENALES_RETIRADAS = {
    "san.puerta_abierta",  # se quitó el VL53L0X; el byte 0 de SAN_ESTADO ahora es san.ocupado
    "luz.zona1", "luz.zona2", "luz.zona3",  # quedó una sola fotorresistencia: luz.interior
}

# Campos que definen qué significa cada byte de la trama: siempre se sincronizan con el firmware.
_CAMPOS_DECODIFICACION = {"subsistema", "tipo", "mensaje", "byte_inicio", "longitud", "con_signo", "escala",
                          "desplazamiento", "etiquetas"}


@transaction.atomic
def cargar_catalogo():
    """Crea o actualiza las señales y borra las retiradas. Respeta los umbrales y nombres que el administrador
    haya editado. Devuelve (creadas, actualizadas, retiradas)."""
    retiradas = 0
    for senal in Senal.objects.filter(codigo__in=SENALES_RETIRADAS):  # primero, para liberar su posición en la trama
        senal.mediciones.all().delete()
        senal.delete()
        retiradas += 1
    creadas = actualizadas = 0
    for definicion in CATALOGO:
        datos = dict(definicion)
        codigo = datos.pop("codigo")
        decodificacion = {campo: datos[campo] for campo in _CAMPOS_DECODIFICACION if campo in datos}
        _, creada = Senal.objects.update_or_create(codigo=codigo, defaults=decodificacion, create_defaults=datos)
        creadas += int(creada)
        actualizadas += int(not creada)
    return creadas, actualizadas, retiradas
