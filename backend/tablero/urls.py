from django.urls import path

from . import analisis, api, autobuses, confiabilidad, flota, historial, ordenes, panorama, usuarios, views

app_name = "tablero"

urlpatterns = [
    path("", panorama.InicioView.as_view(), name="inicio"),
    path("panorama/", panorama.PanoramaView.as_view(), name="panorama"),
    path("flota/", flota.ResumenFlotaView.as_view(), name="resumen"),
    # Confiabilidad
    path("confiabilidad/", confiabilidad.ConfiabilidadView.as_view(), name="confiabilidad"),
    path("confiabilidad/tipos/<int:pk>/", confiabilidad.TipoComponenteView.as_view(), name="componente_tipo"),
    path("calidad/", confiabilidad.CalidadView.as_view(), name="calidad"),
    # Unidades
    path("autobuses/", autobuses.AutobusesView.as_view(), name="autobuses"),
    path("autobuses/<int:pk>/", autobuses.FichaView.as_view(), name="ficha"),
    path("autobuses/<int:pk>/monitoreo/", views.MonitoreoView.as_view(), name="monitoreo"),
    path("componentes/<int:pk>/", confiabilidad.ComponenteView.as_view(), name="componente"),
    path("componentes/<int:pk>/corregir/", confiabilidad.CorregirContadorView.as_view(), name="componente_corregir"),
    path("senal/<str:codigo>/", views.SenalView.as_view(), name="senal"),
    # Servicio
    path("alertas/", views.AlertasView.as_view(), name="alertas"),
    path("alertas/<int:pk>/atender/", views.AtenderAlertaView.as_view(), name="atender_alerta"),
    path("alertas/<int:pk>/tomar/", views.TomarAlertaView.as_view(), name="tomar_alerta"),
    path("ordenes/", ordenes.OrdenesView.as_view(), name="ordenes"),
    path("ordenes/nueva/", ordenes.NuevaOrdenView.as_view(), name="orden_nueva"),
    path("ordenes/<int:pk>/", ordenes.OrdenView.as_view(), name="orden"),
    path("ordenes/<int:pk>/accion/", ordenes.OrdenAccionView.as_view(), name="orden_accion"),
    path("analisis/", analisis.AnalisisView.as_view(), name="analisis"),
    # Datos
    path("historial/", historial.HistorialView.as_view(), name="historial"),
    path("historial/exportar/", historial.ExportarHistorialView.as_view(), name="historial_exportar"),
    path("exportar/", views.ExportarView.as_view(), name="exportar"),
    # Administración
    path("catalogo/", confiabilidad.CatalogoView.as_view(), name="catalogo"),
    path("catalogo/nuevo/", confiabilidad.TipoFormView.as_view(), name="catalogo_nuevo"),
    path("catalogo/<int:pk>/", confiabilidad.TipoFormView.as_view(), name="catalogo_editar"),
    path("senales/", confiabilidad.SenalesView.as_view(), name="senales"),
    path("senales/<int:pk>/", confiabilidad.SenalFormView.as_view(), name="senal_editar"),
    path("dispositivos/", autobuses.DispositivosView.as_view(), name="dispositivos"),
    path("dispositivos/<str:tipo>/<int:pk>/rotar-clave/", views.RotarClaveView.as_view(), name="rotar_clave"),
    path("dispositivos/<str:tipo>/<int:pk>/estado/", views.CambiarEstadoView.as_view(), name="cambiar_estado"),
    path("seguridad/", views.SeguridadView.as_view(), name="seguridad"),
    path("usuarios/", usuarios.UsuariosView.as_view(), name="usuarios"),
    path("usuarios/nuevo/", usuarios.UsuarioNuevoView.as_view(), name="usuario_nuevo"),
    path("usuarios/<int:pk>/", usuarios.UsuarioEditarView.as_view(), name="usuario_editar"),
    path("usuarios/<int:pk>/accion/", usuarios.UsuarioAccionView.as_view(), name="usuario_accion"),
    # API del tablero
    path("api/v1/tablero/estado/", api.EstadoView.as_view(), name="api_estado"),
    path("api/v1/tablero/series/<str:codigo>/", api.SerieView.as_view(), name="api_serie"),
]
