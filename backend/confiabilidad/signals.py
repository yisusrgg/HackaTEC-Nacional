"""Cada unidad nueva recibe un componente de cada tipo activo del catálogo, con su conteo en cero."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from telemetria.models import Vehiculo


@receiver(post_save, sender=Vehiculo, dispatch_uid="instalar_componentes_de_unidad_nueva")
def instalar_componentes(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        from .servicios import instalar_faltantes

        instalar_faltantes(instance)
