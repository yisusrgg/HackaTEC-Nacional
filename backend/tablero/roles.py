"""Roles (grupos de Django) con mínimo privilegio. Se crean con: python manage.py inicializar"""
from django.contrib.auth.models import Group, Permission

ADMINISTRADOR = "Administrador"
INGENIERIA = "Ingeniería"
TECNICO = "Técnico"

ROLES = {
    ADMINISTRADOR: [
        "telemetria.ver_tablero",
        "telemetria.exportar_datos",
        "telemetria.atender_alerta",
        "telemetria.gestionar_dispositivos",
        "auditoria.ver_auditoria",
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
    INGENIERIA: ["telemetria.ver_tablero", "telemetria.exportar_datos"],
    TECNICO: ["telemetria.ver_tablero", "telemetria.atender_alerta"],
}


def crear_roles():
    for nombre, permisos in ROLES.items():
        grupo, _ = Group.objects.get_or_create(name=nombre)
        objetos = []
        for permiso in permisos:
            app, codename = permiso.split(".")
            objetos.append(Permission.objects.get(content_type__app_label=app, codename=codename))
        grupo.permissions.set(objetos)
