"""
Confiabilidad de componentes: qué piezas lleva cada unidad, cuánto se han usado y cuándo fallan.

- TipoComponente: el catálogo (qué promete el fabricante y cómo se cuenta su uso).
- ComponenteInstalado: una pieza concreta en una unidad, con sus ciclos y horas acumulados.
- Causa: lista cerrada de causas de falla, para que todos registren igual.
- Falla: se crea al registrar la reparación de una orden en la que se cambió la pieza; cuenta en los análisis
  cuando la orden se cierra.
- EstadoConteo: memoria interna del conteo (último estado de cada nodo) para contar transiciones entre lotes.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Case, ExpressionWrapper, F, FloatField, When
from django.utils import timezone

from telemetria.models import Nodo, Senal, Vehiculo

PORCENTAJE_CERCA_DEL_LIMITE = 80  # vida consumida a partir de la cual un componente se vigila


class UnidadVida(models.TextChoices):
    CICLOS = "CICLOS", "Ciclos"
    HORAS = "HORAS", "Horas"


class Regla(models.TextChoices):
    """Cómo se cuenta el uso a partir de las señales que ya manda el nodo (ver conteo.py)."""

    VENTILADOR = "VENTILADOR", "Ventilador del A/C: arranque al pasar de menos a más de 300 rpm; horas con 300 rpm o más"
    USOS_SANITARIO = "USOS_SANITARIO", "Sanitario: cada uso que registra el nodo cuenta un ciclo"
    LLENADO_TANQUE = "LLENADO_TANQUE", "Tanque: un ciclo por llenado (sube de menos de 40 % a más de 80 %)"
    LUZ_INTERIOR = "LUZ_INTERIOR", "Iluminación: encendido al pasar de menos a más de 50 %; horas encendida"


class TipoMedicion(models.TextChoices):
    MEDIDO = "MEDIDO", "Medido"
    ESTIMADO = "ESTIMADO", "Estimado"


class TipoComponente(models.Model):
    codigo = models.CharField(max_length=40, unique=True, validators=[RegexValidator(r"^[a-z0-9_]+$")],
                              help_text="Identificador interno, en minúsculas y sin espacios.")
    nombre = models.CharField(max_length=80)
    subsistema = models.CharField(max_length=4, choices=Senal.Subsistema.choices)
    fabricante = models.CharField(max_length=80, blank=True)
    modelo = models.CharField("modelo o número de parte", max_length=80, blank=True)
    unidad_vida = models.CharField("la vida se mide en", max_length=6, choices=UnidadVida.choices)
    vida_garantizada = models.PositiveIntegerField(validators=[MinValueValidator(1)],
                                                   help_text="Ciclos u horas que promete el fabricante.")
    limite_prematura = models.PositiveSmallIntegerField(
        "límite de falla prematura (%)", default=50, validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Una falla antes de este porcentaje de la vida garantizada se marca como prematura.")
    regla = models.CharField("regla de conteo", max_length=16, choices=Regla.choices)
    medicion = models.CharField("el uso es", max_length=8, choices=TipoMedicion.choices, default=TipoMedicion.ESTIMADO,
                                help_text="Medido: hay un sensor sobre la pieza. Estimado: se deduce de otra señal.")
    activo = models.BooleanField(default=True, help_text="Los tipos inactivos ya no se instalan en unidades nuevas.")
    nota = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "tipo de componente"
        verbose_name_plural = "tipos de componente"
        ordering = ["subsistema", "nombre"]
        permissions = [
            ("ver_confiabilidad", "Puede ver la confiabilidad de componentes y la calidad de los datos"),
            ("gestionar_catalogo", "Puede editar el catálogo de componentes, las señales y corregir contadores"),
        ]

    def __str__(self):
        return self.nombre

    @property
    def unidad_texto(self):
        return "ciclos" if self.unidad_vida == UnidadVida.CICLOS else "h"


class Causa(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=120)
    subsistema = models.CharField(max_length=4, choices=Senal.Subsistema.choices, blank=True)
    descripcion = models.CharField("descripción", max_length=255, blank=True)
    activa = models.BooleanField(default=True, help_text="Una causa usada no se borra: se desactiva.")

    class Meta:
        verbose_name = "causa de falla"
        verbose_name_plural = "causas de falla"
        ordering = ["subsistema", "nombre"]

    def __str__(self):
        return self.nombre


class ComponenteQuerySet(models.QuerySet):
    def activos(self):
        return self.filter(retirado_en__isnull=True)

    def con_vida(self):
        """Agrega `uso` (ciclos u horas, según el tipo) y `vida_pct` (% de la vida garantizada) calculados en la base."""
        uso = Case(When(tipo__unidad_vida=UnidadVida.CICLOS, then=F("ciclos")), default=F("horas"),
                   output_field=FloatField())
        return self.annotate(uso_db=uso).annotate(
            vida_pct=ExpressionWrapper(F("uso_db") * 100.0 / F("tipo__vida_garantizada"), output_field=FloatField()))


class ComponenteInstalado(models.Model):
    tipo = models.ForeignKey(TipoComponente, on_delete=models.PROTECT, related_name="instalados")
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.PROTECT, related_name="componentes")
    numero_serie = models.CharField("número de serie", max_length=60, blank=True)
    instalado_en = models.DateTimeField(default=timezone.now)
    retirado_en = models.DateTimeField(null=True, blank=True)
    ciclos = models.PositiveIntegerField(default=0)
    horas = models.FloatField(default=0)

    objects = ComponenteQuerySet.as_manager()

    class Meta:
        verbose_name = "componente instalado"
        verbose_name_plural = "componentes instalados"
        ordering = ["vehiculo", "tipo__subsistema", "tipo__nombre", "-instalado_en"]
        indexes = [models.Index(fields=["vehiculo", "retirado_en"], name="componente_vehiculo_activo")]

    def __str__(self):
        serie = f" · {self.numero_serie}" if self.numero_serie else ""
        return f"{self.tipo.nombre}{serie}"

    @property
    def activo(self):
        return self.retirado_en is None

    @property
    def uso(self):
        return self.ciclos if self.tipo.unidad_vida == UnidadVida.CICLOS else self.horas

    @property
    def porcentaje_vida(self):
        return round(100 * self.uso / self.tipo.vida_garantizada, 1) if self.tipo.vida_garantizada else 0

    @property
    def nivel(self):
        """ok | cerca | vencido: para el color de la barra de vida."""
        pct = self.porcentaje_vida
        if pct >= 100:
            return "vencido"
        return "cerca" if pct >= PORCENTAJE_CERCA_DEL_LIMITE else "ok"


class Falla(models.Model):
    class Momento(models.TextChoices):
        TEMPRANA = "TEMPRANA", "Prematura"
        NORMAL = "NORMAL", "Dentro de garantía"
        TARDIA = "TARDIA", "Después de la garantía"

    componente = models.ForeignKey(ComponenteInstalado, on_delete=models.PROTECT, related_name="fallas",
                                   help_text="La pieza que falló (queda retirada).")
    reemplazo = models.ForeignKey(ComponenteInstalado, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="reemplaza_a", help_text="La pieza nueva que se instaló.")
    orden = models.ForeignKey("servicio.OrdenServicio", on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="fallas")
    causa = models.ForeignKey(Causa, on_delete=models.PROTECT, null=True, blank=True, related_name="fallas")
    fecha = models.DateTimeField(default=timezone.now, db_index=True)
    uso_al_fallar = models.FloatField(help_text="Ciclos u horas que llevaba la pieza al fallar.")
    porcentaje_garantia = models.FloatField(help_text="Uso al fallar como % de la vida garantizada.")
    momento = models.CharField(max_length=8, choices=Momento.choices)
    confirmada = models.BooleanField(default=False, db_index=True,
                                     help_text="Solo las confirmadas cuentan en los análisis de confiabilidad.")
    registrada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="fallas_registradas")

    class Meta:
        verbose_name = "falla"
        verbose_name_plural = "fallas"
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.componente} · {self.fecha:%d/%m/%Y}"

    @property
    def prematura(self):
        return self.momento == self.Momento.TEMPRANA

    @classmethod
    def clasificar(cls, porcentaje, limite_prematura):
        if porcentaje < limite_prematura:
            return cls.Momento.TEMPRANA
        return cls.Momento.NORMAL if porcentaje <= 100 else cls.Momento.TARDIA


class EstadoConteo(models.Model):
    """Último estado visto de cada nodo para una regla, para contar cambios aunque lleguen en lotes distintos."""

    nodo = models.ForeignKey(Nodo, on_delete=models.CASCADE, related_name="+")
    regla = models.CharField(max_length=16, choices=Regla.choices)
    encendido = models.BooleanField(default=False)
    valor = models.FloatField(null=True, blank=True)
    ts = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "estado de conteo"
        verbose_name_plural = "estados de conteo"
        constraints = [models.UniqueConstraint(fields=["nodo", "regla"], name="estado_conteo_unico")]
