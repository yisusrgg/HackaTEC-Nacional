from django.apps import AppConfig


class ConfiabilidadConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "confiabilidad"
    verbose_name = "Confiabilidad de componentes"

    def ready(self):
        from . import signals  # noqa: F401  (instala los componentes de cada unidad nueva)
