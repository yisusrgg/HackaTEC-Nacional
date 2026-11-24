from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import EventoSeguridad
from .servicios import registrar
from .utils import ip_de

Tipo = EventoSeguridad.Tipo


@receiver(user_logged_in)
def _inicio_sesion(sender, request, user, **kwargs):
    registrar(Tipo.LOGIN_OK, actor=user.get_username(), ip=ip_de(request))


@receiver(user_login_failed)
def _inicio_fallido(sender, credentials, request=None, **kwargs):
    registrar(
        Tipo.LOGIN_FALLIDO,
        severidad=EventoSeguridad.Severidad.ADVERTENCIA,
        actor=str(credentials.get("username", "")),
        ip=ip_de(request),
    )


@receiver(user_logged_out)
def _cierre_sesion(sender, request, user, **kwargs):
    if user is not None:
        registrar(Tipo.LOGOUT, actor=user.get_username(), ip=ip_de(request))
