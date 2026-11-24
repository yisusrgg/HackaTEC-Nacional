"""API que consume el gateway (ESP32 #2)."""
import hashlib
import hmac
import time

from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from auditoria.models import EventoSeguridad
from auditoria.servicios import registrar
from auditoria.utils import ip_de

from .alertas import GestorAlertas
from .ingesta import LoteRechazado, procesar_lote
from .models import Gateway
from .serializers import LoteSerializer

Tipo = EventoSeguridad.Tipo
Sev = EventoSeguridad.Severidad


class GatewayAutenticado:
    """Identidad de un gateway ya autenticado; no es una cuenta humana ni puede entrar al tablero."""

    is_authenticated = True
    is_anonymous = False

    def __init__(self, gateway: Gateway):
        self.gateway = gateway
        self.pk = f"gw-{gateway.pk}"  # lo usa el limitador de peticiones

    def __str__(self):
        return self.gateway.identificador


class FirmaGatewayAuthentication(BaseAuthentication):
    """
    Autenticación de dispositivo: el gateway firma el cuerpo exacto de la petición con HMAC-SHA256 y su clave.
    Cabeceras:  X-Gateway: <identificador>    X-Firma: <hex de 64 caracteres>
    """

    def authenticate(self, request):
        identificador = request.headers.get("X-Gateway", "").strip()
        firma = request.headers.get("X-Firma", "").strip().lower()
        if not identificador or not firma:
            raise AuthenticationFailed("Faltan las cabeceras X-Gateway y X-Firma.")

        ip = ip_de(request)
        gateway = Gateway.objects.filter(identificador=identificador).first()
        if gateway is None:
            registrar(Tipo.GATEWAY_DESCONOCIDO, severidad=Sev.ADVERTENCIA, actor=identificador, ip=ip)
            raise AuthenticationFailed("Credenciales inválidas.")
        if not gateway.activo:
            registrar(Tipo.GATEWAY_REVOCADO, severidad=Sev.CRITICA, actor=identificador, ip=ip)
            raise AuthenticationFailed("Credenciales inválidas.")

        esperada = hmac.new(gateway.obtener_clave(), request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(esperada, firma):
            registrar(Tipo.FIRMA_INVALIDA, severidad=Sev.CRITICA, actor=identificador, ip=ip,
                      detalle={"bytes": len(request.body)})
            GestorAlertas().seguridad(f"SEG_FIRMA:gw={gateway.pk}",
                                      f"Peticiones con firma inválida a nombre de {identificador}", gateway=gateway)
            raise AuthenticationFailed("Credenciales inválidas.")
        return GatewayAutenticado(gateway), None

    def authenticate_header(self, request):
        return 'HMAC-SHA256 realm="gateway"'


class EsGateway(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.user, GatewayAutenticado)


class IngestaView(APIView):
    """POST /api/v1/ingesta/ — recibe un lote de tramas del bus CAN."""

    authentication_classes = [FirmaGatewayAuthentication]
    permission_classes = [EsGateway]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ingesta"

    def post(self, request):
        gateway = request.user.gateway
        ip = ip_de(request)
        serializer = LoteSerializer(data=request.data)
        if not serializer.is_valid():
            registrar(Tipo.PETICION_INVALIDA, severidad=Sev.ADVERTENCIA, actor=gateway.identificador, ip=ip,
                      detalle={"errores": str(serializer.errors)[:500]})
            return Response({"error": "Lote malformado.", "detalle": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        datos = serializer.validated_data
        if datos["gw"] != gateway.identificador:
            registrar(Tipo.PETICION_INVALIDA, severidad=Sev.ADVERTENCIA, actor=gateway.identificador, ip=ip,
                      detalle={"motivo": "El identificador del cuerpo no coincide con la cabecera", "cuerpo": datos["gw"]})
            return Response({"error": "El identificador del cuerpo no coincide con la cabecera."},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            resultado = procesar_lote(gateway, datos, request.body, ip)
        except LoteRechazado as exc:
            registrar(Tipo.LOTE_REPETIDO, severidad=Sev.CRITICA, actor=gateway.identificador, ip=ip,
                      detalle={"lote": datos["lote"], "motivo": str(exc)})
            GestorAlertas().seguridad(f"SEG_LOTE:gw={gateway.pk}",
                                      f"{gateway.identificador}: lote repetido o manipulado (#{datos['lote']})",
                                      gateway=gateway)
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response({"estado": resultado.estado, "lote": resultado.lote,
                         "aceptadas": resultado.aceptadas, "rechazadas": resultado.rechazadas})


class HoraView(APIView):
    """GET /api/v1/hora/ — hora del servidor para que el gateway selle las tramas sin depender de internet."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "hora"

    def get(self, request):
        return Response({"epoch_ms": int(time.time() * 1000)})
