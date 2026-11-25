"""
Validación de reparaciones con datos.

Cuando el técnico termina la reparación, la orden pasa a «Validación» y empieza una ventana de tiempo
(10 min a 3 días). Si durante esa ventana la señal elegida sale de sus umbrales o el sensor falla, la falla
volvió: la orden regresa a «Reparación». Si la ventana termina con suficientes lecturas y ninguna mala,
la orden se cierra sola como «Validada con datos» y la alerta que la originó queda resuelta.

La revisión se hace al abrir el listado o el detalle de las órdenes (no hace falta un proceso aparte).
"""
from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from telemetria.models import Medicion

from .models import AvanceOrden, OrdenServicio

MIN_LECTURAS = 20  # lecturas mínimas en la ventana para dar por buena una reparación

Etapa = OrdenServicio.Etapa


@dataclass
class Validacion:
    estado: str  # sin_criterio | pendiente | en_curso | aprobada | fallida | sin_datos | terminada
    texto: str
    lecturas: int = 0
    malas: int = 0
    progreso: int = 0  # 0 a 100
    inicio: datetime | None = None
    fin: datetime | None = None


def lecturas_malas(orden):
    """Filtro de las lecturas que cuentan como falla: fuera de umbral o con el sensor sin dato."""
    senal = orden.senal
    condicion = ~Q(calidad=Medicion.Calidad.OK)
    if senal.umbral_alto is not None:
        condicion |= Q(valor__gt=senal.umbral_alto)
    if senal.umbral_bajo is not None:
        condicion |= Q(valor__lt=senal.umbral_bajo)
    return condicion


def evaluar(orden, ahora=None):
    ahora = ahora or timezone.now()
    if not orden.tiene_criterio:
        return Validacion("sin_criterio", "Sin señal para validar con datos: Ingeniería la valida manualmente.")
    if orden.etapa in (Etapa.DIAGNOSTICO, Etapa.REPARACION) or orden.validacion_inicio is None:
        return Validacion("pendiente", "La validación empieza cuando se registra la reparación.")

    inicio, fin = orden.validacion_inicio, orden.validacion_fin
    hasta = min(fin, ahora)
    consulta = Medicion.objects.filter(senal=orden.senal, nodo=orden.nodo, ts__gte=inicio, ts__lte=hasta)
    lecturas = consulta.count()
    malas = consulta.filter(lecturas_malas(orden)).count()
    total = (fin - inicio).total_seconds()
    progreso = 100 if ahora >= fin else int(max(0, (ahora - inicio).total_seconds()) * 100 / total)
    datos = dict(lecturas=lecturas, malas=malas, progreso=progreso, inicio=inicio, fin=fin)

    if orden.etapa != Etapa.VALIDACION:
        return Validacion("terminada", "Validación terminada.", **datos)
    if malas:
        return Validacion("fallida", f"La falla volvió: {malas} lecturas fuera de umbral durante la validación.", **datos)
    if ahora < fin:
        return Validacion("en_curso", f"Sin fallas hasta ahora · {lecturas} lecturas revisadas.", **datos)
    if lecturas < MIN_LECTURAS:
        return Validacion("sin_datos", f"La ventana terminó con solo {lecturas} lecturas (mínimo {MIN_LECTURAS}). "
                                       "Revisa que el nodo esté enviando datos o valida manualmente.", **datos)
    return Validacion("aprobada", f"{lecturas} lecturas dentro de los umbrales: la reparación funcionó.", **datos)


def actualizar(orden, ahora=None):
    """Aplica el resultado de la validación a la orden (regresa a reparación o la cierra). Devuelve la evaluación."""
    resultado = evaluar(orden, ahora)
    if orden.etapa != Etapa.VALIDACION or resultado.estado not in ("fallida", "aprobada"):
        return resultado

    with transaction.atomic():
        if resultado.estado == "fallida":
            orden.etapa = Etapa.REPARACION
            orden.intentos += 1
            orden.validacion_inicio = None
            orden.save(update_fields=["etapa", "intentos", "validacion_inicio", "actualizada"])
            orden.anotar(f"{resultado.texto} La orden regresa a reparación.", tipo=AvanceOrden.Tipo.SISTEMA)
        else:
            orden.etapa = Etapa.CERRADA
            orden.resultado = OrdenServicio.Resultado.VALIDADA_DATOS
            orden.cerrada_en = ahora or timezone.now()
            orden.save(update_fields=["etapa", "resultado", "cerrada_en", "actualizada"])
            orden.anotar(f"Validada automáticamente: {resultado.texto}", tipo=AvanceOrden.Tipo.SISTEMA)
            orden.resolver_alerta()
            from confiabilidad.servicios import confirmar_falla  # aquí para evitar una importación circular

            confirmar_falla(orden)  # la falla de la pieza cuenta en los análisis (salvo que falte aprobarla)
    return resultado


def actualizar_en_validacion():
    """Revisa todas las órdenes que están en validación (se llama al abrir el listado)."""
    for orden in OrdenServicio.objects.filter(etapa=Etapa.VALIDACION).select_related("senal", "alerta"):
        actualizar(orden)