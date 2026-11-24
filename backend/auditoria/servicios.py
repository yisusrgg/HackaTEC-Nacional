import ipaddress
import json
import logging

from django.db import transaction

from .models import HASH_GENESIS, EstadoCadena, EventoSeguridad

logger = logging.getLogger(__name__)

_NIVEL_LOG = {
    EventoSeguridad.Severidad.INFO: logging.INFO,
    EventoSeguridad.Severidad.ADVERTENCIA: logging.WARNING,
    EventoSeguridad.Severidad.CRITICA: logging.ERROR,
}


def _normalizar_ip(ip):
    if not ip:
        return None
    try:
        return ipaddress.ip_address(ip).compressed
    except ValueError:
        return None


def registrar(tipo, *, severidad=EventoSeguridad.Severidad.INFO, actor="", ip=None, detalle=None):
    """Agrega un evento a la bitácora encadenada. Nunca guardes claves ni contraseñas en `detalle`."""
    # Normaliza el detalle a JSON puro para que el hash sea idéntico antes y después de guardarlo.
    detalle = json.loads(json.dumps(detalle or {}, default=str, ensure_ascii=False))
    with transaction.atomic():
        estado, _ = EstadoCadena.objects.select_for_update().get_or_create(pk=1)
        evento = EventoSeguridad(
            tipo=tipo,
            severidad=severidad,
            actor=(actor or "")[:150],
            ip=_normalizar_ip(ip),
            detalle=detalle,
            hash_anterior=estado.ultimo_hash,
        )
        evento.hash = evento.calcular_hash()
        evento.save()
        estado.ultimo_hash = evento.hash
        estado.total += 1
        estado.save(update_fields=["ultimo_hash", "total"])
    logger.log(_NIVEL_LOG.get(severidad, logging.INFO), "[%s] %s %s", tipo, actor, detalle or "")
    return evento


def verificar_cadena():
    """Recorre la bitácora y confirma que nadie la alteró. Devuelve un diccionario con el resultado."""
    anterior = HASH_GENESIS
    revisados = 0
    for evento in EventoSeguridad.objects.order_by("id").iterator():
        if evento.hash_anterior != anterior or evento.calcular_hash() != evento.hash:
            return {"integra": False, "evento_id": evento.id, "revisados": revisados, "motivo": "Evento alterado o eliminado"}
        anterior = evento.hash
        revisados += 1
    estado = EstadoCadena.objects.filter(pk=1).first()
    if estado and (estado.ultimo_hash != anterior or estado.total != revisados):
        return {"integra": False, "evento_id": None, "revisados": revisados, "motivo": "Faltan eventos al final de la cadena"}
    return {"integra": True, "evento_id": None, "revisados": revisados, "motivo": ""}
