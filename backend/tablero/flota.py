"""Resumen de la flota: clientes, sus autobuses y cuáles necesitan atención primero.

Por cliente se muestran solo los autobuses más importantes (por defecto 2), en este orden de prioridad:
1. en línea y con alertas activas, 2. con alertas, 3. en línea, 4. el resto por última señal más reciente.
Dentro de cada grupo, la alerta crítica va antes que la advertencia.
"""
from django.db.models import Prefetch
from django.views.generic import TemplateView

from telemetria.models import Alerta, Cliente, Gateway, Severidad, Vehiculo

from .views import VistaProtegida

AUTOBUSES_POR_CLIENTE = 2
RANGO_SEVERIDAD = {Severidad.CRITICA: 0, Severidad.ADVERTENCIA: 1, Severidad.INFO: 2}


class Conexion:
    EN_LINEA = "en_linea"
    SIN_SENAL = "sin_senal"
    SIN_CONECTAR = "sin_conectar"
    REVOCADO = "revocado"
    SIN_DISPOSITIVO = "sin_dispositivo"


def _alertas_por_vehiculo():
    """Agrupa las alertas activas por autobús (vía su gateway o el gateway de su nodo)."""
    por_vehiculo, sin_autobus = {}, 0
    activas = Alerta.objects.filter(estado__in=Alerta.ACTIVAS).select_related("gateway", "nodo__gateway")
    for alerta in activas:
        gateway = alerta.gateway or (alerta.nodo.gateway if alerta.nodo else None)
        if gateway is None:
            sin_autobus += 1
            continue
        por_vehiculo.setdefault(gateway.vehiculo_id, []).append(alerta)
    for lista in por_vehiculo.values():
        lista.sort(key=lambda a: (RANGO_SEVERIDAD.get(a.severidad, 9), -a.ultima_vez.timestamp()))
    return por_vehiculo, sin_autobus


def _estado_autobus(vehiculo, alertas):
    gateways = list(vehiculo.gateways.all())
    activos = [g for g in gateways if g.activo]
    contactos = [g.ultimo_contacto for g in gateways if g.ultimo_contacto]
    ultimo_contacto = max(contactos) if contactos else None
    if not gateways:
        conexion = Conexion.SIN_DISPOSITIVO
    elif not activos:
        conexion = Conexion.REVOCADO
    elif any(g.en_linea for g in activos):
        conexion = Conexion.EN_LINEA
    elif ultimo_contacto:
        conexion = Conexion.SIN_SENAL
    else:
        conexion = Conexion.SIN_CONECTAR
    return {
        "vehiculo": vehiculo,
        "conexion": conexion,
        "en_linea": conexion == Conexion.EN_LINEA,
        "ultimo_contacto": ultimo_contacto,
        "alertas": len(alertas),
        "criticas": sum(1 for a in alertas if a.severidad == Severidad.CRITICA),
        "severidad": alertas[0].severidad if alertas else None,
        "alerta_principal": alertas[0] if alertas else None,
    }


def prioridad(autobus):
    """Menor = más importante."""
    con_alertas = autobus["alertas"] > 0
    contacto = autobus["ultimo_contacto"].timestamp() if autobus["ultimo_contacto"] else 0
    return (
        not (con_alertas and autobus["en_linea"]),
        not con_alertas,
        RANGO_SEVERIDAD.get(autobus["severidad"], 9),
        not autobus["en_linea"],
        -autobus["alertas"],
        -contacto,
    )


def resumen_flota(por_cliente=AUTOBUSES_POR_CLIENTE):
    alertas, alertas_sin_autobus = _alertas_por_vehiculo()
    vehiculos = Vehiculo.objects.prefetch_related(Prefetch("gateways", queryset=Gateway.objects.only(
        "id", "vehiculo_id", "activo", "ultimo_contacto")))
    clientes = Cliente.objects.filter(activo=True).prefetch_related(Prefetch("vehiculos", queryset=vehiculos))

    tarjetas = []
    for cliente in clientes:
        autobuses = sorted((_estado_autobus(v, alertas.get(v.pk, [])) for v in cliente.vehiculos.all()), key=prioridad)
        con_alertas = [a for a in autobuses if a["alertas"]]
        tarjetas.append({
            "cliente": cliente,
            "total": len(autobuses),
            "en_linea": sum(1 for a in autobuses if a["en_linea"]),
            "con_alertas": len(con_alertas),
            "criticas": sum(a["criticas"] for a in autobuses),
            "severidad": min((a["severidad"] for a in con_alertas), key=lambda s: RANGO_SEVERIDAD.get(s, 9), default=None),
            "destacados": autobuses[:por_cliente],
            "restantes": max(len(autobuses) - por_cliente, 0),
        })
    # Primero los clientes con alertas (críticas antes), luego los que tienen autobuses, al final los vacíos.
    tarjetas.sort(key=lambda t: (
        t["total"] == 0, t["con_alertas"] == 0, RANGO_SEVERIDAD.get(t["severidad"], 9), -t["con_alertas"],
        t["cliente"].nombre_comercial.lower(),
    ))
    return {
        "clientes": tarjetas,
        "totales": {
            "clientes": sum(1 for t in tarjetas if t["total"]),
            "autobuses": sum(t["total"] for t in tarjetas),
            "en_linea": sum(t["en_linea"] for t in tarjetas),
            "con_alertas": sum(t["con_alertas"] for t in tarjetas),
            "criticas": sum(t["criticas"] for t in tarjetas),
            "sin_autobus": alertas_sin_autobus,
        },
    }


class ResumenFlotaView(VistaProtegida, TemplateView):
    permission_required = "telemetria.ver_tablero"
    template_name = "tablero/flota.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(resumen_flota())
        return contexto
