"""Roles (grupos de Django) con mínimo privilegio. Se crean con: python manage.py inicializar

- Ingeniería analiza: ve toda la flota, las alertas, los datos y la confiabilidad, y solo edita los datos de los
  componentes (catálogo, umbrales y contadores). No toca órdenes ni dispositivos.
- Técnico (de Volvo o de un taller con convenio: un solo rol; quién hizo cada cosa se sabe por su usuario):
  repara. Crea órdenes desde las alertas y las trabaja.
- Administrador: administra usuarios y, cuando hace falta o algo falla, interviene: valida, cancela o reasigna
  órdenes y gestiona los dispositivos.
"""
from django.contrib.auth.models import Group, Permission

ADMINISTRADOR = "Administrador"
INGENIERIA = "Ingeniería"
TECNICO = "Técnico"
# Rol que existió antes; sus usuarios pasan a Técnico al correr crear_roles().
ROLES_RETIRADOS = ["Técnico de convenio"]

ROLES = {
    ADMINISTRADOR: [
        "telemetria.ver_tablero",
        "telemetria.exportar_datos",
        "telemetria.gestionar_dispositivos",
        "servicio.gestionar_ordenes",
        "servicio.validar_ordenes",
        "servicio.ver_analisis",
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
    INGENIERIA: [
        "telemetria.ver_tablero",
        "telemetria.exportar_datos",
        "auditoria.ver_auditoria",
        "servicio.ver_analisis",
        "confiabilidad.ver_confiabilidad",
        "confiabilidad.gestionar_catalogo",
    ],
    TECNICO: ["telemetria.ver_tablero", "servicio.gestionar_ordenes"],
}


def crear_roles():
    for nombre, permisos in ROLES.items():
        grupo, _ = Group.objects.get_or_create(name=nombre)
        objetos = []
        for permiso in permisos:
            app, codename = permiso.split(".")
            objetos.append(Permission.objects.get(content_type__app_label=app, codename=codename))
        grupo.permissions.set(objetos)
    tecnico = Group.objects.get(name=TECNICO)
    for retirado in Group.objects.filter(name__in=ROLES_RETIRADOS):
        tecnico.user_set.add(*retirado.user_set.all())
        retirado.delete()
