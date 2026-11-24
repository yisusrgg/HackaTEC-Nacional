import hashlib
import json

from django.db import models
from django.utils import timezone

HASH_GENESIS = "0" * 64


class EventoSeguridad(models.Model):
    """
    Bitácora de seguridad de solo agregar.

    Cada evento guarda el hash del anterior (cadena tipo blockchain): si alguien modifica o borra un registro
    directamente en la base de datos, verificar_cadena() lo detecta.
    """

    class Tipo(models.TextChoices):
        LOGIN_OK = "LOGIN_OK", "Inicio de sesión"
        LOGIN_FALLIDO = "LOGIN_FALLIDO", "Inicio de sesión fallido"
        LOGIN_BLOQUEADO = "LOGIN_BLOQUEADO", "Usuario bloqueado temporalmente"
        LOGOUT = "LOGOUT", "Cierre de sesión"
        ACCESO_DENEGADO = "ACCESO_DENEGADO", "Acceso denegado"
        FIRMA_INVALIDA = "FIRMA_INVALIDA", "Firma de gateway inválida"
        GATEWAY_DESCONOCIDO = "GATEWAY_DESCONOCIDO", "Gateway desconocido"
        GATEWAY_REVOCADO = "GATEWAY_REVOCADO", "Gateway revocado intentó enviar"
        LOTE_REPETIDO = "LOTE_REPETIDO", "Lote repetido o manipulado"
        PETICION_INVALIDA = "PETICION_INVALIDA", "Petición malformada"
        MAC_INVALIDO = "MAC_INVALIDO", "Trama con MAC inválido"
        REPLAY_TRAMA = "REPLAY_TRAMA", "Trama repetida (replay)"
        NODO_DESCONOCIDO = "NODO_DESCONOCIDO", "Trama de nodo desconocido"
        NODO_REVOCADO = "NODO_REVOCADO", "Trama de nodo revocado"
        CLAVE_ROTADA = "CLAVE_ROTADA", "Clave de dispositivo rotada"
        DISPOSITIVO_ESTADO = "DISPOSITIVO_ESTADO", "Dispositivo activado o revocado"
        EXPORTACION = "EXPORTACION", "Exportación de datos"
        ALERTA_ATENDIDA = "ALERTA_ATENDIDA", "Alerta atendida"

    class Severidad(models.TextChoices):
        INFO = "INFO", "Informativo"
        ADVERTENCIA = "ADVERTENCIA", "Advertencia"
        CRITICA = "CRITICA", "Crítico"

    creado = models.DateTimeField(default=timezone.now, db_index=True)
    tipo = models.CharField(max_length=32, choices=Tipo.choices, db_index=True)
    severidad = models.CharField(max_length=12, choices=Severidad.choices, default=Severidad.INFO)
    actor = models.CharField(max_length=150, blank=True, help_text="Usuario o dispositivo que originó el evento.")
    ip = models.GenericIPAddressField(null=True, blank=True)
    detalle = models.JSONField(default=dict, blank=True)
    hash_anterior = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)

    class Meta:
        verbose_name = "evento de seguridad"
        verbose_name_plural = "eventos de seguridad"
        ordering = ["-id"]
        permissions = [("ver_auditoria", "Puede ver la bitácora de seguridad")]

    def __str__(self):
        return f"{self.creado:%Y-%m-%d %H:%M:%S} {self.get_tipo_display()} ({self.actor})"

    def calcular_hash(self) -> str:
        contenido = json.dumps(
            {
                "creado": self.creado.isoformat(),
                "tipo": self.tipo,
                "severidad": self.severidad,
                "actor": self.actor,
                "ip": self.ip or "",
                "detalle": self.detalle,
                "anterior": self.hash_anterior,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(contenido.encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("La bitácora es de solo agregar: un evento no se puede modificar.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("La bitácora es de solo agregar: un evento no se puede borrar.")


class EstadoCadena(models.Model):
    """Fila única con el último hash y el total de eventos; detecta que borren eventos del final."""

    ultimo_hash = models.CharField(max_length=64, default=HASH_GENESIS)
    total = models.PositiveBigIntegerField(default=0)

    class Meta:
        verbose_name = "estado de la cadena"
        verbose_name_plural = "estado de la cadena"
