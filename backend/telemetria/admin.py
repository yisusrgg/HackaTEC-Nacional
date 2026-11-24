from django.contrib import admin, messages

from .models import Alerta, Gateway, Lote, Medicion, Nodo, Senal, Vehiculo


class DispositivoAdmin(admin.ModelAdmin):
    """Al dar de alta un dispositivo se genera su clave, pero no se muestra aquí: se obtiene con «Rotar clave»."""

    def save_model(self, request, obj, form, change):
        if not obj.clave_cifrada:
            obj.generar_clave()
            messages.info(request, "Clave generada. Para provisionar el dispositivo usa «Rotar clave» en Tablero → Dispositivos.")
        super().save_model(request, obj, form, change)


@admin.register(Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = ("identificador", "nombre", "creado")
    search_fields = ("identificador", "nombre")


@admin.register(Gateway)
class GatewayAdmin(DispositivoAdmin):
    list_display = ("identificador", "vehiculo", "activo", "ultimo_contacto", "ultimo_lote", "firmware")
    list_filter = ("activo",)
    readonly_fields = ("epoca", "ultimo_lote", "ultimo_contacto", "firmware", "estado", "clave_actualizada")


@admin.register(Nodo)
class NodoAdmin(DispositivoAdmin):
    list_display = ("nombre", "numero", "gateway", "activo", "ultimo_contacto", "tramas_recibidas", "tramas_rechazadas")
    list_filter = ("activo", "gateway")
    readonly_fields = ("ultimo_contador", "ultimo_contacto", "tramas_recibidas", "tramas_perdidas",
                       "tramas_rechazadas", "reinicios", "clave_actualizada")


@admin.register(Senal)
class SenalAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "subsistema", "unidad", "umbral_bajo", "umbral_alto", "visible")
    list_editable = ("umbral_bajo", "umbral_alto", "visible")
    list_filter = ("subsistema",)
    search_fields = ("codigo", "nombre")


@admin.register(Medicion)
class MedicionAdmin(admin.ModelAdmin):
    list_display = ("ts", "nodo", "senal", "valor", "calidad")
    list_filter = ("calidad", "senal__subsistema")
    list_select_related = ("nodo", "senal")
    date_hierarchy = "ts"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Lote)
class LoteAdmin(admin.ModelAdmin):
    list_display = ("recibido", "gateway", "epoca", "numero", "tramas", "aceptadas", "rechazadas", "ip")
    list_filter = ("gateway",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Alerta)
class AlertaAdmin(admin.ModelAdmin):
    list_display = ("ultima_vez", "tipo", "severidad", "estado", "mensaje", "ocurrencias")
    list_filter = ("estado", "tipo", "severidad")
    search_fields = ("mensaje",)
