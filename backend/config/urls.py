from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Telemetría de carrocería · Administración"
admin.site.site_title = "Telemetría de carrocería"
admin.site.index_title = "Administración"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("", include("telemetria.urls")),  # API del gateway: /api/v1/ingesta/ y /api/v1/hora/
    path("cuentas/", include("tablero.urls_cuentas")),
    path("", include("tablero.urls")),  # tablero web y su API de consulta
]
