"""Roles (grupos de Django) con mínimo privilegio. Se crean con: python manage.py inicializar

- Ingeniería analiza: ve toda la flota, las alertas, los datos y la confiabilidad, y solo edita los datos de los
  componentes (catálogo, umbrales y contadores). No toca órdenes ni dispositivos.
- Técnico (de Volvo): repara. Crea órdenes desde las alertas y las trabaja.
- Técnico de convenio: taller externo; solo sus órdenes asignadas y las unidades de esas órdenes.
- Administrador: administra usuarios y, cuando hace falta o algo falla, interviene: valida, cancela o reasigna
  órdenes, aprueba o devuelve el trabajo del técnico de convenio y gestiona los dispositivos.
"""
from django.contrib.auth.models import Group, Permission

ADMINISTRADOR = "Administrador"
INGENIERIA = "Ingeniería"
TECNICO = "Técnico"  # técnico de Volvo
TECNICO_CONVENIO = "Técnico de convenio"  # taller externo: solo sus órdenes asignadas

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
    # Sin ver_tablero: no ve la flota, solo «Mis órdenes» y las unidades de esas órdenes. Lo que registra lo
    # aprueba el Administrador antes de que cuente en los análisis.
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
