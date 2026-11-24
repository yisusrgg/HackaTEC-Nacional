"""
Catálogo de señales del nodo de carrocería (el "DBC" del bus CAN propio).

Debe coincidir con los mensajes de firmware/lib/protocolo/protocolo.h y con docs/03-protocolo-can.md.
Todas las señales van en big-endian dentro de los 4 bytes de datos de la trama.
"""
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
    # --- 0x21 SAN_ESTADO (VL53L0X + HC-SR501) ---
    dict(codigo="san.puerta_abierta", nombre="Puerta del sanitario", subsistema=SAN, tipo="BOOL", decimales=0,
         mensaje=0x21, byte_inicio=0, longitud=1, escala=1, rango_min=0, rango_max=1,
         etiquetas={"0": "Cerrada", "1": "Abierta"}, orden=3),
    dict(codigo="san.presencia", nombre="Presencia en el sanitario", subsistema=SAN, tipo="BOOL", decimales=0,
         mensaje=0x21, byte_inicio=1, longitud=1, escala=1, rango_min=0, rango_max=1,
         etiquetas={"0": "Libre", "1": "Ocupado"}, orden=4),
    dict(codigo="san.usos", nombre="Usos desde el arranque", subsistema=SAN, unidad="usos", decimales=0,
         mensaje=0x21, byte_inicio=2, longitud=2, escala=1, rango_min=0, orden=5),
    # --- 0x22 SAN_FUGA (sensor de humedad de suelo en la bandeja) ---
    dict(codigo="san.humedad_piso", nombre="Humedad en el piso", subsistema=SAN, unidad="%", decimales=0,
         mensaje=0x22, byte_inicio=0, longitud=2, escala=0.1, rango_min=0, rango_max=100, orden=6),
    dict(codigo="san.fuga", nombre="Fuga de agua", subsistema=SAN, tipo="BOOL", decimales=0,
         mensaje=0x22, byte_inicio=2, longitud=1, escala=1, rango_min=0, rango_max=1, umbral_alto=0.5,
         severidad_alerta="CRITICA", etiquetas={"0": "Sin fuga", "1": "Fuga detectada"}, orden=7),
    # --- 0x30 LUZ_ZONAS (3 fotorresistencias) ---
    dict(codigo="luz.zona1", nombre="Iluminación zona delantera", subsistema=LUZ, unidad="%", decimales=0,
         mensaje=0x30, byte_inicio=0, longitud=1, escala=1, rango_min=0, rango_max=100, orden=1),
    dict(codigo="luz.zona2", nombre="Iluminación zona media", subsistema=LUZ, unidad="%", decimales=0,
         mensaje=0x30, byte_inicio=1, longitud=1, escala=1, rango_min=0, rango_max=100, orden=2),
    dict(codigo="luz.zona3", nombre="Iluminación zona trasera", subsistema=LUZ, tipo="BOOL", decimales=0,
         mensaje=0x30, byte_inicio=2, longitud=1, escala=1, rango_min=0, rango_max=1,
         etiquetas={"0": "Apagada", "1": "Encendida"}, orden=3),
    # --- 0x40 ALIMENTACION (divisor de voltaje) ---
    dict(codigo="elec.voltaje", nombre="Voltaje de alimentación", subsistema=ELEC, unidad="V", decimales=2,
         mensaje=0x40, byte_inicio=0, longitud=2, escala=0.001, rango_min=0, rango_max=36, umbral_bajo=4.5, orden=1),
    # --- 0xF0 ESTADO_NODO (latido) ---
    dict(codigo="nodo.uptime", nombre="Tiempo encendido", subsistema=NODO, unidad="min", decimales=0,
         mensaje=0xF0, byte_inicio=0, longitud=2, escala=1, orden=1),
    dict(codigo="nodo.fallas", nombre="Fallas de sensores (bits)", subsistema=NODO, decimales=0,
         mensaje=0xF0, byte_inicio=2, longitud=2, escala=1, orden=2),
]

# Campos que definen cómo se decodifica la trama: siempre se sincronizan con el firmware.
_CAMPOS_DECODIFICACION = {"subsistema", "tipo", "mensaje", "byte_inicio", "longitud", "con_signo", "escala", "desplazamiento"}


def cargar_catalogo():
    """Crea o actualiza las señales. Respeta los umbrales y nombres que el administrador haya editado."""
    creadas = actualizadas = 0
    for definicion in CATALOGO:
        datos = dict(definicion)
        codigo = datos.pop("codigo")
        decodificacion = {campo: datos[campo] for campo in _CAMPOS_DECODIFICACION if campo in datos}
        _, creada = Senal.objects.update_or_create(codigo=codigo, defaults=decodificacion, create_defaults=datos)
        creadas += int(creada)
        actualizadas += int(not creada)
    return creadas, actualizadas
