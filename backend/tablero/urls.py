from django.urls import path

from . import api, autobuses, flota, views, ordenes, analisis, historial, usuarios

app_name = "tablero"

urlpatterns = [
    path("", flota.ResumenFlotaView.as_view(), name="resumen"),
    path("autobuses/", autobuses.AutobusesView.as_view(), name="autobuses"),
    path("autobuses/<int:pk>/monitoreo/", views.MonitoreoView.as_view(), name="monitoreo"),
    path("senal/<str:codigo>/", views.SenalView.as_view(), name="senal"),
    path("alertas/", views.AlertasView.as_view(), name="alertas"),
    path("alertas/<int:pk>/atender/", views.AtenderAlertaView.as_view(), name="atender_alerta"),
    path("seguridad/", views.SeguridadView.as_view(), name="seguridad"),
    path("dispositivos/<str:tipo>/<int:pk>/rotar-clave/", views.RotarClaveView.as_view(), name="rotar_clave"),
    path("dispositivos/<str:tipo>/<int:pk>/estado/", views.CambiarEstadoView.as_view(), name="cambiar_estado"),
    path("exportar/", views.ExportarView.as_view(), name="exportar"),
    path("ordenes/", ordenes.OrdenesView.as_view(), name="ordenes"),
    path("ordenes/nueva/", ordenes.NuevaOrdenView.as_view(), name="orden_nueva"),
    path("ordenes/<int:pk>/", ordenes.OrdenView.as_view(), name="orden"),
    path("ordenes/<int:pk>/accion/", ordenes.OrdenAccionView.as_view(), name="orden_accion"),
    path("analisis/", analisis.AnalisisView.as_view(), name="analisis"),
    path("historial/", historial.HistorialView.as_view(), name="historial"),
    path("historial/exportar/", historial.ExportarHistorialView.as_view(), name="historial_exportar"),
    path("usuarios/", usuarios.UsuariosView.as_view(), name="usuarios"),
    path("usuarios/nuevo/", usuarios.UsuarioNuevoView.as_view(), name="usuario_nuevo"),
    path("usuarios/<int:pk>/", usuarios.UsuarioEditarView.as_view(), name="usuario_editar"),
    path("usuarios/<int:pk>/accion/", usuarios.UsuarioAccionView.as_view(), name="usuario_accion"),
    path("api/v1/tablero/estado/", api.EstadoView.as_view(), name="api_estado"),
    path("api/v1/tablero/series/<str:codigo>/", api.SerieView.as_view(), name="api_serie"),
]
