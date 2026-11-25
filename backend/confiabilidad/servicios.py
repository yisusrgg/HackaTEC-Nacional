"""Operaciones de confiabilidad que usan las pantallas: instalar piezas, registrar fallas y confirmarlas."""
from django.db import transaction
from django.utils import timezone

from .models import ComponenteInstalado, Falla, TipoComponente


def instalar_faltantes(vehiculo, tipos=None, cuando=None):
    """Instala en la unidad un componente de cada tipo activo que todavía no tenga. Devuelve cuántos instaló."""
    tipos = list(tipos if tipos is not None else TipoComponente.objects.filter(activo=True))
    tiene = set(ComponenteInstalado.objects.activos().filter(vehiculo=vehiculo).values_list("tipo_id", flat=True))
    nuevos = [ComponenteInstalado(tipo=t, vehiculo=vehiculo, instalado_en=cuando or timezone.now())
              for t in tipos if t.pk not in tiene]
    ComponenteInstalado.objects.bulk_create(nuevos)
    return len(nuevos)


def falla_de(orden):
    return orden.fallas.select_related("componente__tipo", "reemplazo", "causa").first() if orden.pk else None


@transaction.atomic
def registrar_reparacion(orden, *, usuario, causa, reemplazar=False, serie_nueva=""):
    """
    Guarda la causa en la orden y, si se cambió la pieza, retira el componente afectado, instala el nuevo (su
    conteo empieza en cero) y crea la falla con los ciclos u horas que llevaba la pieza. La falla queda sin
    confirmar hasta que la orden se cierra (ver confirmar_falla).

    Si la orden ya tenía una falla (la reparación se registró antes y la falla volvió), no se duplica: solo se
    actualizan la causa y la serie de la pieza nueva.
    """
    orden.causa = causa
    falla = falla_de(orden)
    if falla is not None:
        falla.causa = causa
        falla.save(update_fields=["causa"])
        if serie_nueva and falla.reemplazo_id:
            falla.reemplazo.numero_serie = serie_nueva
            falla.reemplazo.save(update_fields=["numero_serie"])
        return falla
    componente = orden.componente
    if not reemplazar or componente is None or not componente.activo:
        return None

    ahora = timezone.now()
    componente = ComponenteInstalado.objects.select_for_update().select_related("tipo").get(pk=componente.pk)
    tipo = componente.tipo
    uso = componente.uso
    porcentaje = round(100 * uso / tipo.vida_garantizada, 1)
    componente.retirado_en = ahora
    componente.save(update_fields=["retirado_en"])
    nuevo = ComponenteInstalado.objects.create(tipo=tipo, vehiculo=componente.vehiculo, numero_serie=serie_nueva,
                                               instalado_en=ahora)
    return Falla.objects.create(
        componente=componente, reemplazo=nuevo, orden=orden, causa=causa, fecha=ahora, uso_al_fallar=uso,
        porcentaje_garantia=porcentaje, momento=Falla.clasificar(porcentaje, tipo.limite_prematura),
        registrada_por=usuario,
    )


def confirmar_falla(orden):
    """Al cerrar la orden, su falla empieza a contar en los análisis."""
    from servicio.models import OrdenServicio

    if orden.etapa != OrdenServicio.Etapa.CERRADA:
        return 0
    return orden.fallas.filter(confirmada=False).update(confirmada=True)
