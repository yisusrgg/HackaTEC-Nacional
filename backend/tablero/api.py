"""API de consulta para las gráficas y tarjetas del tablero (sesión de usuario + permiso)."""
from datetime import timedelta

from django.conf import settings
from django.db.models import Avg, Count, Max, Min, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from auditoria.models import EventoSeguridad
from telemetria.models import Alerta, Medicion, Nodo, Senal

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from .acceso import puede_entrar, puede_ver_unidad


class VerTablero(BasePermission):
    """Toda la flota (telemetria.ver_tablero) o, para el técnico de convenio, las unidades de sus órdenes."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and puede_entrar(request.user))


def _exigir_nodo(request, nodo):
    if nodo is None:
        if not request.user.has_perm("telemetria.ver_tablero"):
            raise PermissionDenied
        return
    if not puede_ver_unidad(request.user, nodo.gateway.vehiculo_id):
        raise PermissionDenied


def nodo_seleccionado(nodo_id, vehiculo=None):
    """Nodo por su id (?nodo=<id>, único entre todos los autobuses). Sin id, el primero del autobús indicado."""
    consulta = Nodo.objects.select_related("gateway__vehiculo").order_by("gateway_id", "numero")
    if vehiculo is not None:
        consulta = consulta.filter(gateway__vehiculo=vehiculo)
    nodo_id = str(nodo_id or "")
    if nodo_id.isdigit():
        return consulta.filter(pk=nodo_id).first() or (consulta.first() if vehiculo is not None else None)
    return consulta.first()


def alertas_del_autobus(vehiculo):
    """Alertas activas de un autobús, ya sea por su gateway o por alguno de sus nodos."""
    return Alerta.objects.filter(estado__in=Alerta.ACTIVAS).filter(
        Q(gateway__vehiculo=vehiculo) | Q(nodo__gateway__vehiculo=vehiculo)
    )


def _iso(fecha):
    return fecha.isoformat() if fecha else None


class EstadoView(APIView):
    """GET /api/v1/tablero/estado/?nodo=<id> — último valor de cada señal y salud del autobús de ese nodo."""

    permission_classes = [VerTablero]

    def get(self, request):
        nodo = nodo_seleccionado(request.query_params.get("nodo"))
        _exigir_nodo(request, nodo)
        ahora = timezone.now()
        alertas = alertas_del_autobus(nodo.gateway.vehiculo) if nodo else Alerta.objects.filter(estado__in=Alerta.ACTIVAS)
        respuesta = {
            "servidor": _iso(ahora),
            "alertas_activas": alertas.distinct().count(),
            "eventos_criticos_24h": EventoSeguridad.objects.filter(
                severidad=EventoSeguridad.Severidad.CRITICA, creado__gte=ahora - timedelta(hours=24)
            ).count(),
            "nodo": None,
            "gateway": None,
            "senales": [],
        }
        if nodo is None:
            return Response(respuesta)

        gw = nodo.gateway
        respuesta["nodo"] = {
            "numero": nodo.numero, "nombre": nodo.nombre, "en_linea": nodo.en_linea, "activo": nodo.activo,
            "ultimo_contacto": _iso(nodo.ultimo_contacto), "recibidas": nodo.tramas_recibidas,
            "perdidas": nodo.tramas_perdidas, "porcentaje_perdida": nodo.porcentaje_perdida,
            "rechazadas": nodo.tramas_rechazadas, "reinicios": nodo.reinicios,
        }
        respuesta["gateway"] = {
            "identificador": gw.identificador, "en_linea": gw.en_linea, "activo": gw.activo,
            "ultimo_contacto": _iso(gw.ultimo_contacto), "ultimo_lote": gw.ultimo_lote, "estado": gw.estado,
        }

        claves_activas = set(
            Alerta.objects.filter(estado__in=Alerta.ACTIVAS, nodo=nodo).values_list("clave_dedup", flat=True)
        )
        limite_desactualizado = settings.TELEMETRIA["SEGUNDOS_EN_LINEA"] * 2
        for senal in Senal.objects.filter(visible=True):
            ultima = Medicion.objects.filter(senal=senal, nodo=nodo).order_by("-ts").first()
            respuesta["senales"].append(_resumen_senal(senal, nodo, ultima, claves_activas, ahora, limite_desactualizado))
        return Response(respuesta)


def _resumen_senal(senal, nodo, medicion, claves_activas, ahora, limite_desactualizado):
    base = f"nodo={nodo.pk}:senal={senal.pk}"
    if medicion is None:
        nivel = "sin_datos"
    elif medicion.calidad != Medicion.Calidad.OK:
        nivel = "falla"
    elif f"UMBRAL:{base}" in claves_activas:
        nivel = "alerta"
    elif (ahora - medicion.ts).total_seconds() > limite_desactualizado:
        nivel = "desactualizado"
    else:
        nivel = "ok"
    return {
        "codigo": senal.codigo,
        "valor": medicion.valor if medicion else None,
        "texto": senal.formatear(medicion.valor) if medicion else "—",
        "calidad": medicion.calidad if medicion else None,
        "ts": _iso(medicion.ts) if medicion else None,
        "nivel": nivel,
    }


class SerieView(APIView):
    """GET /api/v1/tablero/series/<codigo>/?nodo=<id>&minutos=60 — puntos para la gráfica de una señal."""

    permission_classes = [VerTablero]

    def get(self, request, codigo):
        senal = get_object_or_404(Senal, codigo=codigo)
        nodo = nodo_seleccionado(request.query_params.get("nodo"))
        _exigir_nodo(request, nodo)
        try:
            minutos = int(request.query_params.get("minutos", 60))
        except ValueError:
            minutos = 60
        minutos = max(5, min(minutos, 7 * 24 * 60))
        hasta = timezone.now()
        desde = hasta - timedelta(minutes=minutos)

        respuesta = {
            "codigo": senal.codigo, "desde": int(desde.timestamp() * 1000), "hasta": int(hasta.timestamp() * 1000),
            "umbral_bajo": senal.umbral_bajo, "umbral_alto": senal.umbral_alto,
            "puntos": [], "estadisticas": {"n": 0, "min": None, "max": None, "promedio": None},
        }
        if nodo is None:
            return Response(respuesta)

        consulta = Medicion.objects.filter(senal=senal, nodo=nodo, ts__gte=desde, valor__isnull=False)
        stats = consulta.aggregate(n=Count("id"), minimo=Min("valor"), maximo=Max("valor"), promedio=Avg("valor"))
        respuesta["estadisticas"] = {
            "n": stats["n"], "min": stats["minimo"], "max": stats["maximo"],
            "promedio": round(stats["promedio"], 3) if stats["promedio"] is not None else None,
        }
        filas = consulta.order_by("ts").values_list("ts", "valor")
        respuesta["puntos"] = _reducir(filas, desde, hasta, settings.TELEMETRIA["PUNTOS_MAX_SERIE"], stats["n"])
        return Response(respuesta)


def _reducir(filas, desde, hasta, maximo, total):
    """Si hay demasiados puntos, promedia por intervalos de tiempo para que la gráfica siga fluida."""
    if total <= maximo:
        return [[int(ts.timestamp() * 1000), valor] for ts, valor in filas]
    inicio_ms = desde.timestamp() * 1000
    ancho = (hasta.timestamp() * 1000 - inicio_ms) / maximo
    cubetas = {}
    for ts, valor in filas.iterator():
        indice = int((ts.timestamp() * 1000 - inicio_ms) // ancho)
        suma, cuenta = cubetas.get(indice, (0.0, 0))
        cubetas[indice] = (suma + valor, cuenta + 1)
    return [[int(inicio_ms + (i + 0.5) * ancho), round(s / c, 4)] for i, (s, c) in sorted(cubetas.items())]
