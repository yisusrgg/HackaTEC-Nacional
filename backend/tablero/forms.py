from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.cache import cache

from auditoria.models import EventoSeguridad
from auditoria.servicios import registrar
from auditoria.utils import ip_de

MAX_INTENTOS = 5
BLOQUEO_SEGUNDOS = 15 * 60


class FormularioLogin(AuthenticationForm):
    """Inicio de sesión con bloqueo temporal tras varios intentos fallidos (usuario + IP)."""

    username = forms.CharField(
        label="Usuario", max_length=150,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username", "class": "campo"}),
    )
    password = forms.CharField(
        label="Contraseña", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "class": "campo"}),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "bloqueado": "Demasiados intentos fallidos. Espera 15 minutos e inténtalo de nuevo.",
    }

    def clean(self):
        usuario = (self.cleaned_data.get("username") or "").strip().lower()
        ip = ip_de(self.request)
        clave = f"login-fallos:{usuario}:{ip}"
        if cache.get(clave, 0) >= MAX_INTENTOS:
            registrar(EventoSeguridad.Tipo.LOGIN_BLOQUEADO, severidad=EventoSeguridad.Severidad.CRITICA, actor=usuario, ip=ip)
            raise forms.ValidationError(self.error_messages["bloqueado"], code="bloqueado")
        try:
            datos = super().clean()
        except forms.ValidationError:
            if cache.add(clave, 1, BLOQUEO_SEGUNDOS) is False:
                try:
                    cache.incr(clave)
                except ValueError:
                    cache.set(clave, 1, BLOQUEO_SEGUNDOS)
            raise
        cache.delete(clave)
        return datos
