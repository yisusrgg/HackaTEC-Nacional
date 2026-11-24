from django.urls import path

from . import api, views, ordenes

app_name = "tablero"

urlpatterns = [
    path("", views.ResumenView.as_view(), name="resumen"),
    path("senal/<str:codigo>/", views.SenalView.as_view(), name="senal"),
    path("alertas/", views.AlertasView.as_view(), name="alertas"),
    path("alertas/<int:pk>/atender/", views.AtenderAlertaView.as_view(), name="atender_alerta"),
    path("seguridad/", views.SeguridadView.as_view(), name="seguridad"),
    path("dispositivos/", views.DispositivosView.as_view(), name="dispositivos"),
    path("dispositivos/<str:tipo>/<int:pk>/rotar-clave/", views.RotarClaveView.as_view(), name="rotar_clave"),
    path("dispositivos/<str:tipo>/<int:pk>/estado/", views.CambiarEstadoView.as_view(), name="cambiar_estado"),
    path("exportar/", views.ExportarView.as_view(), name="exportar"),
    path("ordenes/", ordenes.OrdenesView.as_view(), name="ordenes"),
    path("ordenes/nueva/", ordenes.NuevaOrdenView.as_view(), name="orden_nueva"),
    path("ordenes/<int:pk>/", ordenes.OrdenView.as_view(), name="orden"),
    path("ordenes/<int:pk>/accion/", ordenes.OrdenAccionView.as_view(), name="orden_accion"),
    path("api/v1/tablero/estado/", api.EstadoView.as_view(), name="api_estado"),
    path("api/v1/tablero/series/<str:codigo>/", api.SerieView.as_view(), name="api_serie"),
]
