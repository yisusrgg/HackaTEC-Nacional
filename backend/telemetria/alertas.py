from django.utils import timezone

from .models import Alerta, Medicion, Senal, Severidad


class GestorAlertas:
    """Abre, actualiza y resuelve alertas. Solo existe una alerta activa por clave para no saturar el tablero."""

    def __init__(self):
        self.activas = {a.clave_dedup: a for a in Alerta.objects.filter(estado__in=Alerta.ACTIVAS)}

    def abrir(self, clave, *, tipo, severidad, mensaje, nodo=None, senal=None, gateway=None, valor=None):
        ahora = timezone.now()
        alerta = self.activas.get(clave)
        if alerta is not None:
            alerta.ocurrencias += 1
            alerta.ultima_vez = ahora
            alerta.valor = valor
            alerta.mensaje = mensaje[:255]
            alerta.save(update_fields=["ocurrencias", "ultima_vez", "valor", "mensaje"])
            return alerta
        alerta = Alerta(
            clave_dedup=clave,
            tipo=tipo,
            severidad=severidad,
            mensaje=mensaje[:255],
            nodo=nodo,
            senal=senal,
            gateway=gateway,
            valor=valor,
            ultima_vez=ahora,
        )
        from confiabilidad import diagnostico  # aquí para evitar una importación circular entre apps

        diagnostico.aplicar(alerta)
        alerta.save()
        self.activas[clave] = alerta
        return alerta

    def resolver(self, clave):
        alerta = self.activas.pop(clave, None)
        if alerta is not None:
            alerta.estado = Alerta.Estado.RESUELTA
            alerta.resuelta_en = timezone.now()
            alerta.save(update_fields=["estado", "resuelta_en"])

    def evaluar(self, nodo, senal: Senal, valor, calidad):
        base = f"nodo={nodo.pk}:senal={senal.pk}"
        clave_falla, clave_umbral = f"FALLA:{base}", f"UMBRAL:{base}"

        if calidad != Medicion.Calidad.OK:
            detalle = "el sensor no entrega datos" if valor is None else f"lectura fuera de rango físico ({valor})"
            self.abrir(clave_falla, tipo=Alerta.Tipo.SENSOR_FALLA, severidad=Severidad.ADVERTENCIA,
                       mensaje=f"{senal.nombre}: {detalle}", nodo=nodo, senal=senal, valor=valor)
            return
        self.resolver(clave_falla)

        if senal.umbral_alto is not None and valor > senal.umbral_alto:
            self.abrir(clave_umbral, tipo=Alerta.Tipo.UMBRAL_ALTO, severidad=senal.severidad_alerta,
                       mensaje=_mensaje_umbral(senal, valor, "alta", senal.umbral_alto), nodo=nodo, senal=senal, valor=valor)
        elif senal.umbral_bajo is not None and valor < senal.umbral_bajo:
            self.abrir(clave_umbral, tipo=Alerta.Tipo.UMBRAL_BAJO, severidad=senal.severidad_alerta,
                       mensaje=_mensaje_umbral(senal, valor, "baja", senal.umbral_bajo), nodo=nodo, senal=senal, valor=valor)
        else:
            self.resolver(clave_umbral)

    def seguridad(self, clave, mensaje, *, nodo=None, gateway=None):
        return self.abrir(clave, tipo=Alerta.Tipo.SEGURIDAD, severidad=Severidad.CRITICA, mensaje=mensaje,
                          nodo=nodo, gateway=gateway)


def _mensaje_umbral(senal: Senal, valor, sentido, umbral):
    if senal.tipo == Senal.Tipo.BOOLEANA:
        return f"{senal.nombre}: {senal.formatear(valor)}"
    return f"{senal.nombre} {sentido}: {senal.formatear(valor)} (umbral {umbral:g} {senal.unidad})".strip()
