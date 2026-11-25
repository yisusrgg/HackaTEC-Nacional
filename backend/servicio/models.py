from datetime import timedelta

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from telemetria.models import Alerta, Nodo, Senal, Vehiculo


class OrdenServicio(models.Model):
    """Una reparación: del diagnóstico a la validación con datos de que la falla ya no se repite."""

    class Etapa(models.TextChoices):
        DIAGNOSTICO = "DIAGNOSTICO", "Diagnóstico"
        REPARACION = "REPARACION", "Reparación"
        VALIDACION = "VALIDACION", "Validación"
        CERRADA = "CERRADA", "Cerrada"
        CANCELADA = "CANCELADA", "Cancelada"

    class Resultado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        VALIDADA_DATOS = "VALIDADA_DATOS", "Validada con datos"
        VALIDADA_MANUAL = "VALIDADA_MANUAL", "Cerrada manualmente"
        CANCELADA = "CANCELADA", "Cancelada"

    class Aprobacion(models.TextChoices):
        """Lo que registra un técnico de convenio lo revisa Ingeniería antes de que la falla cuente."""

        NO_REQUIERE = "NO_REQUIERE", "No requiere"
        PENDIENTE = "PENDIENTE", "Por validar por Ingeniería"
        APROBADA = "APROBADA", "Aprobada por Ingeniería"
        DEVUELTA = "DEVUELTA", "Devuelta al técnico"

    ABIERTAS = (Etapa.DIAGNOSTICO, Etapa.REPARACION, Etapa.VALIDACION)
    PASOS = (Etapa.DIAGNOSTICO, Etapa.REPARACION, Etapa.VALIDACION, Etapa.CERRADA)

    DURACIONES = [
        (10, "10 minutos (demostración)"),
        (60, "1 hora"),
        (360, "6 horas"),
        (1440, "24 horas"),
        (4320, "3 días"),
    ]

    folio = models.CharField(max_length=20, unique=True, null=True, blank=True, editable=False)
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.PROTECT, related_name="ordenes")
    nodo = models.ForeignKey(Nodo, on_delete=models.SET_NULL, null=True, blank=True, related_name="ordenes",
                             help_text="Nodo cuyas lecturas sirven para validar la reparación.")
    senal = models.ForeignKey(Senal, on_delete=models.SET_NULL, null=True, blank=True, related_name="ordenes",
                              verbose_name="señal a validar",
                              help_text="Si se elige, la orden se valida sola cuando esta señal se mantiene dentro de sus umbrales.")
    alerta = models.ForeignKey(Alerta, on_delete=models.SET_NULL, null=True, blank=True, related_name="ordenes")
    titulo = models.CharField("falla", max_length=120)
    diagnostico = models.TextField(blank=True)
    solucion = models.TextField("solución aplicada", blank=True)
    etapa = models.CharField(max_length=12, choices=Etapa.choices, default=Etapa.DIAGNOSTICO, db_index=True)
    resultado = models.CharField(max_length=16, choices=Resultado.choices, default=Resultado.PENDIENTE)
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="ordenes_asignadas")
    creada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="ordenes_creadas", editable=False)
    minutos_validacion = models.PositiveIntegerField("duración de la validación", choices=DURACIONES, default=1440)
    validacion_inicio = models.DateTimeField(null=True, blank=True, editable=False,
                                             help_text="Momento en que se terminó la reparación.")
    intentos = models.PositiveSmallIntegerField(default=0, editable=False,
                                                help_text="Veces que la falla volvió durante la validación.")
    componente = models.ForeignKey("confiabilidad.ComponenteInstalado", on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="ordenes", verbose_name="componente afectado")
    causa = models.ForeignKey("confiabilidad.Causa", on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="ordenes", verbose_name="causa de la falla")
    diagnostico_confirmado = models.BooleanField(
        null=True, blank=True, help_text="Si el técnico confirmó (sí) o rechazó (no) el diagnóstico del sistema.")
    aprobacion = models.CharField(max_length=12, choices=Aprobacion.choices, default=Aprobacion.NO_REQUIERE)
    creada = models.DateTimeField(auto_now_add=True)
    actualizada = models.DateTimeField(auto_now=True)
    cerrada_en = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "orden de servicio"
        verbose_name_plural = "órdenes de servicio"
        ordering = ["-creada"]
        permissions = [
            ("gestionar_ordenes", "Puede crear y avanzar órdenes de servicio"),
            ("validar_ordenes", "Puede validar, cerrar y cancelar órdenes de servicio"),
            ("ver_analisis", "Puede ver el análisis de calidad"),
            ("solo_asignadas", "Solo ve las órdenes que tiene asignadas y las unidades de esas órdenes"),
        ]

    def __str__(self):
        return self.folio or f"Orden {self.pk}"

    def save(self, *args, **kwargs):
        nueva = self.pk is None
        with transaction.atomic():
            super().save(*args, **kwargs)
            if nueva and not self.folio:
                self.folio = f"OS-{self.creada:%Y}-{self.pk:04d}"
                super().save(update_fields=["folio"])

    @property
    def abierta(self):
        return self.etapa in self.ABIERTAS

    @property
    def indice_etapa(self):
        """Posición en el recorrido Diagnóstico → Reparación → Validación → Cerrada (-1 si se canceló)."""
        return self.PASOS.index(self.etapa) if self.etapa in self.PASOS else -1

    @property
    def validacion_fin(self):
        if self.validacion_inicio is None:
            return None
        return self.validacion_inicio + timedelta(minutes=self.minutos_validacion)

    @property
    def tiene_criterio(self):
        """Se puede validar con datos si hay una señal con umbrales y un nodo que la mida."""
        return bool(self.senal_id and self.nodo_id and self.senal
                    and (self.senal.umbral_alto is not None or self.senal.umbral_bajo is not None))

    def anotar(self, texto, *, autor=None, tipo=None):
        return self.avances.create(texto=texto, autor=autor, tipo=tipo or AvanceOrden.Tipo.NOTA)

    def resolver_alerta(self, usuario=None):
        """Al cerrar la orden, la alerta que la originó queda resuelta."""
        alerta = self.alerta
        if alerta is None or not alerta.activa:
            return
        ahora = timezone.now()
        alerta.estado = Alerta.Estado.RESUELTA
        alerta.resuelta_en = ahora
        if usuario is not None:
            alerta.atendida_por = usuario
            alerta.atendida_en = alerta.atendida_en or ahora
        alerta.nota = f"Resuelta con la orden {self.folio}"[:255]
        alerta.save(update_fields=["estado", "resuelta_en", "atendida_por", "atendida_en", "nota"])


class AvanceOrden(models.Model):
    """Registro de lo que pasó en la orden: notas del técnico, cambios de etapa y avisos del sistema."""

    class Tipo(models.TextChoices):
        NOTA = "NOTA", "Nota"
        ETAPA = "ETAPA", "Cambio de etapa"
        SISTEMA = "SISTEMA", "Sistema"

    orden = models.ForeignKey(OrdenServicio, on_delete=models.CASCADE, related_name="avances")
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    tipo = models.CharField(max_length=8, choices=Tipo.choices, default=Tipo.NOTA)
    texto = models.TextField()
    creado = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "avance de orden"
        verbose_name_plural = "avances de orden"
        ordering = ["-creado", "-id"]

    def __str__(self):
        return f"{self.orden} · {self.get_tipo_display()}"