from django.contrib import admin

from .models import EventoSeguridad


@admin.register(EventoSeguridad)
class EventoSeguridadAdmin(admin.ModelAdmin):
    """Solo lectura: la bitácora no se edita ni se borra, ni siquiera desde el admin."""

    list_display = ("creado", "tipo", "severidad", "actor", "ip")
    list_filter = ("severidad", "tipo")
    search_fields = ("actor", "ip")
    date_hierarchy = "creado"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
