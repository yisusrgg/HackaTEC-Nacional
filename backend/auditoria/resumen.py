"""
Resumen de seguridad que se muestra arriba de la bitácora: qué pasó en las últimas 24 horas y qué falta atender.
Solo cuenta lo que ya está registrado; no cambia nada.
"""
from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from telemetria.models import Alerta, Gateway, Nodo

from .models import EventoSeguridad

T = EventoSeguridad.Tipo
HORAS = 24
DIAS_CLAVE = 90  # una clave de dispositivo con más días que esto conviene renovarla

# Indicadores del resumen. Cada uno agrupa tipos de evento de la bitácora y sirve también como filtro.
GRUPOS = [
    {"clave": "integridad", "titulo": "Firmas inválidas", "nivel": "critico",
     "ayuda": "tramas o lotes con firma que no coincide: datos alterados o clave equivocada",
     "tipos": [T.MAC_INVALIDO, T.FIRMA_INVALIDA]},
    {"clave": "repeticion", "titulo": "Intentos de repetición", "nivel": "critico",
     "ayuda": "tramas o lotes que ya se habían recibido y alguien volvió a enviar",
     "tipos": [T.REPLAY_TRAMA, T.LOTE_REPETIDO]},
    {"clave": "dispositivos", "titulo": "Dispositivos no autorizados", "nivel": "critico",
     "ayuda": "gateways o nodos desconocidos o revocados que intentaron enviar",
     "tipos": [T.GATEWAY_DESCONOCIDO, T.GATEWAY_REVOCADO, T.NODO_DESCONOCIDO, T.NODO_REVOCADO, T.PETICION_INVALIDA]},
    {"clave": "accesos", "titulo": "Accesos fallidos", "nivel": "aviso",
     "ayuda": "contraseñas incorrectas, cuentas bloqueadas y entradas a pantallas sin permiso",
     "tipos": [T.LOGIN_FALLIDO, T.LOGIN_BLOQUEADO, T.ACCESO_DENEGADO]},
]
GRUPOS_POR_CLAVE = {g["clave"]: g for g in GRUPOS}


def calcular(ahora=None):
    ahora = ahora or timezone.now()
    desde = ahora - timedelta(hours=HORAS)
    recientes = EventoSeguridad.objects.filter(creado__gte=desde)

    # Un evento de tramas puede agrupar varias (detalle["cantidad"]); se suman para contar tramas reales.
    por_tipo = {}
    for tipo, detalle in recientes.values_list("tipo", "detalle"):
        cantidad = detalle.get("cantidad", 1) if isinstance(detalle, dict) else 1
        por_tipo[tipo] = por_tipo.get(tipo, 0) + (cantidad if isinstance(cantidad, int) and cantidad > 0 else 1)

    indicadores = []
    for grupo in GRUPOS:
        total = sum(por_tipo.get(t, 0) for t in grupo["tipos"])
        indicadores.append({**grupo, "total": total, "nivel": grupo["nivel"] if total else "ok"})

    return {
        "horas": HORAS,
        "indicadores": indicadores,
        "pendientes": pendientes(ahora),
        "exportaciones": recientes.filter(tipo=T.EXPORTACION).count(),
        "cambios_usuarios": recientes.filter(tipo__in=[T.USUARIO_CREADO, T.USUARIO_MODIFICADO,
                                                        T.CONTRASENA_RESTABLECIDA]).count(),
    }


def pendientes(ahora):
    """Lo que alguien de seguridad debería revisar hoy, con el enlace a donde se resuelve."""
    lista = []
    dispositivos = reverse("tablero:autobuses")

    alertas = Alerta.objects.filter(tipo=Alerta.Tipo.SEGURIDAD, estado__in=Alerta.ACTIVAS).count()
    if alertas:
        lista.append({"nivel": "critico", "enlace": reverse("tablero:alertas"),
                      "texto": f"{alertas} {'alerta' if alertas == 1 else 'alertas'} de seguridad sin resolver"})

    limite = ahora - timedelta(days=DIAS_CLAVE)
    viejas = sum(modelo.objects.filter(activo=True).filter(Q(clave_actualizada__lt=limite) | Q(clave_actualizada__isnull=True)).count()
                 for modelo in (Gateway, Nodo))
    if viejas:
        lista.append({"nivel": "aviso", "enlace": dispositivos,
                      "texto": f"{viejas} {'clave' if viejas == 1 else 'claves'} de dispositivo con más de {DIAS_CLAVE} días: genera una nueva"})

    sin_contacto = Gateway.objects.filter(activo=True).filter(
        Q(ultimo_contacto__lt=ahora - timedelta(hours=HORAS)) | Q(ultimo_contacto__isnull=True)).count()
    if sin_contacto:
        lista.append({"nivel": "aviso", "enlace": dispositivos,
                      "texto": f"{sin_contacto} {'gateway' if sin_contacto == 1 else 'gateways'} sin enviar datos en {HORAS} h"})

    revocados = Gateway.objects.filter(activo=False).count() + Nodo.objects.filter(activo=False).count()
    if revocados:
        lista.append({"nivel": "info", "enlace": dispositivos,
                      "texto": f"{revocados} {'dispositivo revocado' if revocados == 1 else 'dispositivos revocados'}: si ya se reemplazó la placa, puede quedarse así"})
    return lista