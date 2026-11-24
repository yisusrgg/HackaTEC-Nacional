from django.contrib.auth.mixins import PermissionRequiredMixin
from rest_framework.permissions import BasePermission

from auditoria.models import EventoSeguridad
from auditoria.servicios import registrar
from auditoria.utils import ip_de


class PermisoAuditadoMixin(PermissionRequiredMixin):
    """Como PermissionRequiredMixin, pero deja registro cuando un usuario intenta entrar sin permiso."""

    def handle_no_permission(self):
        usuario = self.request.user
        if usuario.is_authenticated:
            registrar(
                EventoSeguridad.Tipo.ACCESO_DENEGADO,
                severidad=EventoSeguridad.Severidad.ADVERTENCIA,
                actor=usuario.get_username(),
                ip=ip_de(self.request),
                detalle={"ruta": self.request.path, "permiso": list(self.get_permission_required())},
            )
        return super().handle_no_permission()


def permiso_drf(codename):
    """Clase de permiso de DRF que exige un permiso de Django (p. ej. 'telemetria.ver_tablero')."""

    class TienePermiso(BasePermission):
        def has_permission(self, request, view):
            usuario = request.user
            return bool(usuario and usuario.is_authenticated and usuario.has_perm(codename))

    TienePermiso.__name__ = f"TienePermiso_{codename.replace('.', '_')}"
    return TienePermiso
