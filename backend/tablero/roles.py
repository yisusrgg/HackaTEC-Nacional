"""Roles (grupos de Django) con mínimo privilegio. Se crean con: python manage.py inicializar"""
from django.contrib.auth.models import Group, Permission

ADMINISTRADOR = "Administrador"
INGENIERIA = "Ingeniería"
TECNICO = "Técnico"  # técnico de Volvo
TECNICO_CONVENIO = "Técnico de convenio"  # taller externo: solo sus órdenes asignadas

ROLES = {
    ADMINISTRADOR: [
        "telemetria.ver_tablero",
        "telemetria.exportar_datos",
        "telemetria.atender_alerta",
        "servicio.view_ordenservicio",
        "servicio.ver_analisis",
        # Para administrar desde el panel de Django (umbrales, vehículos, usuarios):
        "telemetria.view_senal",
        "telemetria.change_senal",
        "telemetria.view_vehiculo",
        "telemetria.add_vehiculo",
        "telemetria.change_vehiculo",
        "telemetria.view_gateway",
        "telemetria.add_gateway",
        "telemetria.view_nodo",
        "telemetria.add_nodo",
        "auth.view_user",
        "auth.add_user",
        "auth.change_user",
        "auth.view_group",
    ],
    INGENIERIA: [
        "telemetria.ver_tablero",
        "telemetria.exportar_datos",
        "auditoria.ver_auditoria",
        "telemetria.gestionar_dispositivos",
        "telemetria.atender_alerta",
        "servicio.gestionar_ordenes",
        "servicio.validar_ordenes",
        "servicio.ver_analisis",
        "confiabilidad.ver_confiabilidad",
        "confiabilidad.gestionar_catalogo",
    ],
    TECNICO: ["telemetria.ver_tablero", "telemetria.atender_alerta", "servicio.gestionar_ordenes"],
    # Sin ver_tablero: no ve la flota, solo «Mis órdenes» y las unidades de esas órdenes. Lo que registra lo
    # aprueba Ingeniería antes de que cuente en los análisis.
    TECNICO_CONVENIO: ["servicio.gestionar_ordenes", "servicio.solo_asignadas"],
}


def crear_roles():
    for nombre, permisos in ROLES.items():
        grupo, _ = Group.objects.get_or_create(name=nombre)
        objetos = []
        for permiso in permisos:
            app, codename = permiso.split(".")
            objetos.append(Permission.objects.get(content_type__app_label=app, codename=codename))
        grupo.permissions.set(objetos)