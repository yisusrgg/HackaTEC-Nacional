"""
Acceso por unidad.

Ingeniería, Técnico Volvo y Administración ven todas las unidades (permiso telemetria.ver_tablero). El técnico de
convenio (permiso servicio.solo_asignadas) solo ve las órdenes que tiene asignadas y las unidades de esas órdenes;
si intenta abrir otra, recibe «Acceso denegado» y el intento queda en la bitácora.
"""
from django.core.exceptions import PermissionDenied

from auditoria.models import EventoSeguridad
from auditoria.servicios import registrar
from auditoria.utils import ip_de

VER_TODO = "telemetria.ver_tablero"
SOLO_ASIGNADAS = "servicio.solo_asignadas"


def solo_asignadas(usuario):
    return not usuario.has_perm(VER_TODO) and usuario.has_perm(SOLO_ASIGNADAS)


def puede_entrar(usuario):
    return usuario.has_perm(VER_TODO) or usuario.has_perm(SOLO_ASIGNADAS)


def unidades_permitidas(usuario):
    """None = todas. Si no, el conjunto de ids de las unidades de sus órdenes asignadas."""
    if usuario.has_perm(VER_TODO):
        return None
    if usuario.has_perm(SOLO_ASIGNADAS):
        from servicio.models import OrdenServicio

        return set(OrdenServicio.objects.filter(responsable=usuario).values_list("vehiculo_id", flat=True))
    return set()


def puede_ver_unidad(usuario, vehiculo_id):
    permitidas = unidades_permitidas(usuario)
    return permitidas is None or vehiculo_id in permitidas


def negar(request, motivo, **detalle):
    registrar(EventoSeguridad.Tipo.ACCESO_DENEGADO, severidad=EventoSeguridad.Severidad.ADVERTENCIA,
              actor=request.user.get_username(), ip=ip_de(request), detalle={"ruta": request.path, "motivo": motivo,
                                                                             **detalle})
    raise PermissionDenied


def exigir_unidad(request, vehiculo_id):
    if not puede_ver_unidad(request.user, vehiculo_id):
        negar(request, "unidad fuera de sus órdenes asignadas", unidad=vehiculo_id)


class AccesoPorUnidadMixin:
    """Deja entrar a quien ve toda la flota o al técnico de convenio (que luego se limita a sus unidades)."""

    permission_required = (VER_TODO,)

    def has_permission(self):
        return puede_entrar(self.request.user)
