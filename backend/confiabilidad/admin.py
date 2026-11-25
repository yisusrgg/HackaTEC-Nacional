from django.contrib import admin

from .models import Causa, ComponenteInstalado, Falla, TipoComponente


@admin.register(TipoComponente)
class TipoComponenteAdmin(admin.ModelAdmin):
    list_display = ("nombre", "subsistema", "fabricante", "vida_garantizada", "unidad_vida", "regla", "activo")
    list_filter = ("subsistema", "activo")


@admin.register(Causa)
class CausaAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "subsistema", "activa")
    list_filter = ("subsistema", "activa")
    search_fields = ("codigo", "nombre")

    def has_delete_permission(self, request, obj=None):
        # Una causa usada no se borra: se desactiva, para no perder el historial.
        return obj is None or not obj.fallas.exists()


@admin.register(ComponenteInstalado)
class ComponenteInstaladoAdmin(admin.ModelAdmin):
    list_display = ("tipo", "vehiculo", "numero_serie", "ciclos", "horas", "instalado_en", "retirado_en")
    list_filter = ("tipo",)
    search_fields = ("vehiculo__identificador", "numero_serie")
    readonly_fields = ("ciclos", "horas")


@admin.register(Falla)
class FallaAdmin(admin.ModelAdmin):
    list_display = ("componente", "fecha", "porcentaje_garantia", "momento", "causa", "confirmada")
    list_filter = ("momento", "confirmada")
    readonly_fields = ("uso_al_fallar", "porcentaje_garantia", "momento")
