import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Now
from django.utils import timezone

from .cifrado_campos import cifrar, descifrar


class Severidad(models.TextChoices):
    INFO = "INFO", "Informativa"
    ADVERTENCIA = "ADVERTENCIA", "Advertencia"
    CRITICA = "CRITICA", "Crítica"


class Cliente(models.Model):
    """Empresa dueña de una flota de autobuses.

    No tiene CRUD en la plataforma: los clientes se insertan directo en la base (o se importan del sistema de
    Volvo) y aquí solo se consultan. Por eso los campos opcionales tienen db_default: un INSERT a mano puede
    omitirlos y la base pone el valor por defecto.
    """

    class Tipo(models.TextChoices):
        OPERADOR = "OPERADOR", "Operador de autobuses"
        DISTRIBUIDOR = "DISTRIBUIDOR", "Distribuidor o franquicia"

    class Segmento(models.TextChoices):
        FORANEO = "FORANEO", "Foráneo (pasajeros entre ciudades)"
        URBANO = "URBANO", "Urbano"
        TURISMO = "TURISMO", "Turismo"
        PERSONAL = "PERSONAL", "Transporte de personal"

    class Origen(models.TextChoices):
        MANUAL = "MANUAL", "Insertado directo en la base"
        IMPORTADO = "IMPORTADO", "Importado del sistema de Volvo"
        DEMO = "DEMO", "Dato de demostración"

    nombre_comercial = models.CharField(max_length=120, unique=True)
    razon_social = models.CharField(max_length=200, blank=True, db_default="")
    rfc = models.CharField("RFC", max_length=13, blank=True, db_default="", validators=[RegexValidator(
        r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$", "RFC no válido: 12 o 13 caracteres en mayúsculas.")])
    tipo = models.CharField(max_length=12, choices=Tipo.choices, db_default=Tipo.OPERADOR)
    segmento = models.CharField(max_length=10, choices=Segmento.choices, blank=True, db_default="")
    estado = models.CharField("estado de la república", max_length=40, blank=True, db_default="")
    contacto_nombre = models.CharField("nombre del contacto", max_length=120, blank=True, db_default="")
    contacto_correo = models.EmailField("correo del contacto", blank=True, db_default="")
    contacto_telefono = models.CharField("teléfono del contacto", max_length=20, blank=True, db_default="")
    activo = models.BooleanField(db_default=True)
    origen = models.CharField(max_length=10, choices=Origen.choices, db_default=Origen.MANUAL)
    id_externo = models.CharField("ID en el sistema de Volvo", max_length=40, unique=True, null=True, blank=True,
                                  help_text="Se llena al importar clientes desde Volvo; evita duplicarlos.")
    nota = models.CharField(max_length=255, blank=True, db_default="")
    creado = models.DateTimeField(db_default=Now(), editable=False)

    class Meta:
        verbose_name = "cliente"
        verbose_name_plural = "clientes"
        ordering = ["nombre_comercial"]

    def __str__(self):
        return self.nombre_comercial


class Vehiculo(models.Model):
    identificador = models.CharField("número económico o VIN", max_length=40, unique=True)
    nombre = models.CharField(max_length=80)
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="vehiculos",
                                help_text="Empresa dueña del autobús. No se puede borrar un cliente que tenga autobuses.")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "vehículo"
        verbose_name_plural = "vehículos"
        ordering = ["identificador"]

    def __str__(self):
        return f"{self.nombre} ({self.identificador})"


class Dispositivo(models.Model):
    """Campos comunes de gateways y nodos: clave secreta cifrada, estado y último contacto."""

    clave_cifrada = models.TextField(editable=False)
    clave_actualizada = models.DateTimeField(null=True, blank=True, editable=False)
    activo = models.BooleanField(default=True, help_text="Desactívalo para revocar el dispositivo.")
    ultimo_contacto = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    def generar_clave(self) -> bytes:
        """Crea una clave nueva de 32 bytes, la guarda cifrada y la devuelve una sola vez para provisionar."""
        clave = secrets.token_bytes(32)
        self.clave_cifrada = cifrar(clave)
        self.clave_actualizada = timezone.now()
        return clave

    def obtener_clave(self) -> bytes:
        return descifrar(self.clave_cifrada)

    @property
    def en_linea(self) -> bool:
        if not self.ultimo_contacto:
            return False
        segundos = (timezone.now() - self.ultimo_contacto).total_seconds()
        return segundos <= settings.TELEMETRIA["SEGUNDOS_EN_LINEA"]


validar_identificador = RegexValidator(r"^[A-Za-z0-9_-]{2,32}$", "Usa de 2 a 32 letras, números, guion o guion bajo.")


class Gateway(Dispositivo):
    vehiculo = models.ForeignKey(Vehiculo, on_delete=models.PROTECT, related_name="gateways")
    identificador = models.CharField(max_length=32, unique=True, validators=[validar_identificador])
    epoca = models.PositiveIntegerField(
        default=1, editable=False, help_text="Sube cada vez que se rota la clave; la numeración de lotes reinicia."
    )
    ultimo_lote = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    firmware = models.CharField(max_length=20, blank=True, editable=False)
    estado = models.JSONField(default=dict, blank=True, editable=False, help_text="Último reporte del gateway.")

    class Meta:
        verbose_name = "gateway"
        verbose_name_plural = "gateways"
        ordering = ["identificador"]

    def __str__(self):
        return self.identificador


class Nodo(Dispositivo):
    gateway = models.ForeignKey(Gateway, on_delete=models.PROTECT, related_name="nodos")
    numero = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(7)],
        help_text="Número del nodo en el bus CAN (byte alto del ID).",
    )
    nombre = models.CharField(max_length=80)
    ultimo_contador = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    tramas_recibidas = models.PositiveBigIntegerField(default=0, editable=False)
    tramas_perdidas = models.PositiveBigIntegerField(default=0, editable=False)
    tramas_rechazadas = models.PositiveBigIntegerField(default=0, editable=False)
    reinicios = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = "nodo"
        verbose_name_plural = "nodos"
        ordering = ["gateway", "numero"]
        constraints = [models.UniqueConstraint(fields=["gateway", "numero"], name="nodo_unico_por_gateway")]
        permissions = [("gestionar_dispositivos", "Puede dar de alta, rotar claves y revocar dispositivos")]

    def __str__(self):
        return f"{self.nombre} (#{self.numero})"

    @property
    def porcentaje_perdida(self) -> float:
        total = self.tramas_recibidas + self.tramas_perdidas
        return round(100 * self.tramas_perdidas / total, 2) if total else 0.0


class Senal(models.Model):
    """Catálogo de señales: dice qué significa cada byte de cada mensaje CAN (como un archivo DBC)."""

    class Subsistema(models.TextChoices):
        AIRE = "AC", "Aire acondicionado"
        SANITARIO = "SAN", "Sanitario"
        ILUMINACION = "LUZ", "Iluminación interior"
        ELECTRICO = "ELEC", "Sistema eléctrico"
        NODO = "NODO", "Diagnóstico del nodo"

    class Tipo(models.TextChoices):
        NUMERICA = "NUM", "Numérica"
        BOOLEANA = "BOOL", "Sí / No"

    codigo = models.CharField(max_length=40, unique=True, validators=[RegexValidator(r"^[a-z0-9_.]+$")])
    nombre = models.CharField(max_length=80)
    subsistema = models.CharField(max_length=4, choices=Subsistema.choices)
    tipo = models.CharField(max_length=4, choices=Tipo.choices, default=Tipo.NUMERICA)
    unidad = models.CharField(max_length=12, blank=True)
    decimales = models.PositiveSmallIntegerField(default=1)

    mensaje = models.PositiveSmallIntegerField(validators=[MaxValueValidator(255)], help_text="Byte bajo del ID CAN.")
    byte_inicio = models.PositiveSmallIntegerField(validators=[MaxValueValidator(3)])
    longitud = models.PositiveSmallIntegerField(choices=[(1, "1 byte"), (2, "2 bytes")])
    con_signo = models.BooleanField(default=False)
    escala = models.FloatField(default=1.0)
    desplazamiento = models.FloatField(default=0.0)

    rango_min = models.FloatField(null=True, blank=True, help_text="Límite físico; fuera de él la lectura es sospechosa.")
    rango_max = models.FloatField(null=True, blank=True)
    umbral_bajo = models.FloatField(null=True, blank=True, help_text="Alerta si el valor baja de aquí.")
    umbral_alto = models.FloatField(null=True, blank=True, help_text="Alerta si el valor sube de aquí.")
    severidad_alerta = models.CharField(max_length=12, choices=Severidad.choices, default=Severidad.ADVERTENCIA)
    etiquetas = models.JSONField(default=dict, blank=True, help_text='Texto por valor, p. ej. {"0": "Cerrada", "1": "Abierta"}.')
    orden = models.PositiveSmallIntegerField(default=0)
    visible = models.BooleanField(default=True)

    class Meta:
        verbose_name = "señal"
        verbose_name_plural = "señales"
        ordering = ["subsistema", "orden", "codigo"]
        constraints = [models.UniqueConstraint(fields=["mensaje", "byte_inicio"], name="senal_posicion_unica")]

    def __str__(self):
        return f"{self.nombre} [{self.codigo}]"

    def clean(self):
        if self.byte_inicio is not None and self.longitud and self.byte_inicio + self.longitud > 4:
            raise ValidationError("La señal se sale de los 4 bytes de datos de la trama.")

    @property
    def valor_invalido(self) -> int:
        """Valor crudo que el nodo manda cuando el sensor no respondió (0x7FFF, 0xFFFF o 0xFF)."""
        bits = 8 * self.longitud
        return (1 << (bits - 1)) - 1 if self.con_signo else (1 << bits) - 1

    def decodificar(self, datos: bytes):
        fragmento = datos[self.byte_inicio : self.byte_inicio + self.longitud]
        crudo = int.from_bytes(fragmento, "big", signed=self.con_signo)
        if crudo == self.valor_invalido:
            return None, Medicion.Calidad.SIN_DATO
        valor = round(crudo * self.escala + self.desplazamiento, 6)
        fuera = (self.rango_min is not None and valor < self.rango_min) or (
            self.rango_max is not None and valor > self.rango_max
        )
        return valor, Medicion.Calidad.FUERA_RANGO if fuera else Medicion.Calidad.OK

    def formatear(self, valor) -> str:
        if valor is None:
            return "—"
        etiqueta = self.etiquetas.get(str(int(round(valor))))
        if etiqueta:
            return etiqueta
        if self.tipo == self.Tipo.BOOLEANA:
            return "Sí" if valor >= 0.5 else "No"
        return f"{valor:.{self.decimales}f} {self.unidad}".strip()


class Lote(models.Model):
    """Paquete de tramas enviado por un gateway. Su número evita repeticiones y hace idempotente el reenvío."""

    gateway = models.ForeignKey(Gateway, on_delete=models.CASCADE, related_name="lotes")
    epoca = models.PositiveIntegerField(default=1)
    numero = models.PositiveBigIntegerField()
    huella = models.CharField(max_length=64, help_text="SHA-256 del cuerpo recibido.")
    recibido = models.DateTimeField(auto_now_add=True, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    tramas = models.PositiveIntegerField(default=0)
    aceptadas = models.PositiveIntegerField(default=0)
    rechazadas = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "lote"
        verbose_name_plural = "lotes"
        ordering = ["-id"]
        constraints = [models.UniqueConstraint(fields=["gateway", "epoca", "numero"], name="lote_unico")]

    def __str__(self):
        return f"{self.gateway} · lote {self.numero}"


class Medicion(models.Model):
    class Calidad(models.TextChoices):
        OK = "OK", "Válida"
        SIN_DATO = "SIN_DATO", "Sensor sin dato"
        FUERA_RANGO = "FUERA_RANGO", "Fuera de rango"

    nodo = models.ForeignKey(Nodo, on_delete=models.PROTECT, related_name="mediciones")
    senal = models.ForeignKey(Senal, on_delete=models.PROTECT, related_name="mediciones")
    valor = models.FloatField(null=True, blank=True)
    calidad = models.CharField(max_length=12, choices=Calidad.choices, default=Calidad.OK)
    contador = models.PositiveBigIntegerField(help_text="Contador de frescura de la trama de origen.")
    ts = models.DateTimeField("momento de la lectura")
    recibido = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "medición"
        verbose_name_plural = "mediciones"
        ordering = ["-ts"]
        indexes = [models.Index(fields=["senal", "nodo", "-ts"], name="medicion_senal_nodo_ts")]
        permissions = [
            ("ver_tablero", "Puede ver el tablero"),
            ("exportar_datos", "Puede exportar mediciones"),
        ]

    def __str__(self):
        return f"{self.senal.codigo}={self.valor} ({self.ts:%H:%M:%S})"


class Alerta(models.Model):
    class Tipo(models.TextChoices):
        UMBRAL_ALTO = "UMBRAL_ALTO", "Valor alto"
        UMBRAL_BAJO = "UMBRAL_BAJO", "Valor bajo"
        SENSOR_FALLA = "SENSOR_FALLA", "Falla de sensor"
        SEGURIDAD = "SEGURIDAD", "Seguridad"

    class Estado(models.TextChoices):
        ABIERTA = "ABIERTA", "Abierta"
        ATENDIDA = "ATENDIDA", "Atendida"
        RESUELTA = "RESUELTA", "Resuelta"

    ACTIVAS = (Estado.ABIERTA, Estado.ATENDIDA)

    clave_dedup = models.CharField(max_length=120, db_index=True, help_text="Evita alertas duplicadas.")
    tipo = models.CharField(max_length=16, choices=Tipo.choices)
    severidad = models.CharField(max_length=12, choices=Severidad.choices, default=Severidad.ADVERTENCIA)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ABIERTA, db_index=True)
    mensaje = models.CharField(max_length=255)
    valor = models.FloatField(null=True, blank=True)
    nodo = models.ForeignKey(Nodo, on_delete=models.SET_NULL, null=True, blank=True, related_name="alertas")
    senal = models.ForeignKey(Senal, on_delete=models.SET_NULL, null=True, blank=True, related_name="alertas")
    gateway = models.ForeignKey(Gateway, on_delete=models.SET_NULL, null=True, blank=True, related_name="alertas")
    ocurrencias = models.PositiveIntegerField(default=1)
    creada = models.DateTimeField(auto_now_add=True)
    ultima_vez = models.DateTimeField(default=timezone.now)
    atendida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="alertas_atendidas"
    )
    atendida_en = models.DateTimeField(null=True, blank=True)
    nota = models.CharField(max_length=255, blank=True)
    resuelta_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "alerta"
        verbose_name_plural = "alertas"
        ordering = ["-ultima_vez"]
        permissions = [("atender_alerta", "Puede atender y resolver alertas")]

    def __str__(self):
        return self.mensaje

    @property
    def activa(self) -> bool:
        return self.estado in self.ACTIVAS
