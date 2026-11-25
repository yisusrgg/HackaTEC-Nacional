"""Autobuses de ejemplo para los clientes de demostración (los carga `python manage.py inicializar`).

Cada autobús tiene su gateway y un nodo, con claves generadas que no se muestran (si se quiere simular uno, se
usa «Rotar clave» o `simular --gateway <id>`, que lee la clave de la base). Los estados son fijos para que la demo
sea igual en todas las computadoras: unos con alertas, otros sin señal desde hace rato y uno que nunca se conectó.
Es idempotente: si el autobús ya existe, no se toca.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .alertas import _mensaje_umbral
from .models import Alerta, Cliente, Gateway, Nodo, Senal, Severidad, Vehiculo

NOTA_ALERTA = "Alerta de ejemplo creada por inicializar."

# (cliente, identificador, minutos desde la última señal o None, alerta o None)
# alerta = (código de señal, tipo, valor)
AUTOBUSES = [
    ("Mobility ADO", "ADO-1001", 3, ("san.fuga", Alerta.Tipo.UMBRAL_ALTO, 1)),
    ("Mobility ADO", "ADO-1002", 12, None),
    ("Mobility ADO", "ADO-1003", 6 * 60, None),
    ("ETN Turistar Lujo", "ETN-2001", 5, ("ac.temperatura", Alerta.Tipo.UMBRAL_ALTO, 33.4)),
    ("ETN Turistar Lujo", "ETN-2002", 20, None),
    ("Grupo Estrella Blanca", "EB-3001", 8, None),
    ("Grupo Estrella Blanca", "EB-3002", 45, None),
    ("Grupo Estrella Blanca", "EB-3003", None, None),
    ("Primera Plus", "PP-4001", 2, ("san.tanque_nivel", Alerta.Tipo.SENSOR_FALLA, None)),
    ("Primera Plus", "PP-4002", 2 * 60, None),
    ("Transportes y Autobuses del Pacífico (TAP)", "TAP-5001", 30, None),
    ("Transportes y Autobuses del Pacífico (TAP)", "TAP-5002", 4, ("elec.voltaje", Alerta.Tipo.UMBRAL_BAJO, 4.1)),
    ("Grupo Senda", "SEN-6001", 10, None),
    ("Grupo Senda", "SEN-6002", 3 * 60, None),
    ("Ómnibus de México", "OMB-7001", 24 * 60, None),
    ("Ómnibus de México", "OMB-7002", 15, ("ac.ventilador_rpm", Alerta.Tipo.UMBRAL_BAJO, 120)),
]


def _alerta(nodo, codigo, tipo, valor, cuando):
    senal = Senal.objects.filter(codigo=codigo).first()
    if senal is None:
        return
    if tipo == Alerta.Tipo.SENSOR_FALLA:
        clave = f"FALLA:nodo={nodo.pk}:senal={senal.pk}"
        severidad, mensaje = Severidad.ADVERTENCIA, f"{senal.nombre}: el sensor no entrega datos"
    else:
        clave = f"UMBRAL:nodo={nodo.pk}:senal={senal.pk}"
        sentido, umbral = ("alta", senal.umbral_alto) if tipo == Alerta.Tipo.UMBRAL_ALTO else ("baja", senal.umbral_bajo)
        severidad, mensaje = senal.severidad_alerta, _mensaje_umbral(senal, valor, sentido, umbral)
    Alerta.objects.create(clave_dedup=clave, tipo=tipo, severidad=severidad, mensaje=mensaje[:255], valor=valor,
                          nodo=nodo, senal=senal, gateway=nodo.gateway, ultima_vez=cuando, nota=NOTA_ALERTA)


def cargar_flota_demo():
    """Crea los autobuses de ejemplo que falten. Devuelve cuántos se crearon."""
    ahora = timezone.now()
    clientes = {c.nombre_comercial: c for c in Cliente.objects.filter(nombre_comercial__in={a[0] for a in AUTOBUSES})}
    existentes = set(Vehiculo.objects.filter(identificador__in=[a[1] for a in AUTOBUSES])
                     .values_list("identificador", flat=True))
    creados = 0
    with transaction.atomic():
        for nombre_cliente, identificador, minutos, alerta in AUTOBUSES:
            cliente = clientes.get(nombre_cliente)
            if cliente is None or identificador in existentes:
                continue
            contacto = ahora - timedelta(minutes=minutos) if minutos is not None else None
            numero = identificador.split("-")[1]
            vehiculo = Vehiculo.objects.create(identificador=identificador, nombre=f"Unidad {numero}", cliente=cliente)
            gateway = Gateway(vehiculo=vehiculo, identificador=f"GW-{identificador}", ultimo_contacto=contacto)
            gateway.generar_clave()
            gateway.save()
            nodo = Nodo(gateway=gateway, numero=1, nombre="Nodo de carrocería 1", ultimo_contacto=contacto)
            nodo.generar_clave()
            nodo.save()
            if alerta:
                _alerta(nodo, *alerta, cuando=contacto or ahora)
            creados += 1
    return creados
