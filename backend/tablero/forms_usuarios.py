from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm

from .roles import ROLES

Usuario = get_user_model()

OPCIONES_ROL = [("", "Sin rol (no puede entrar al tablero)")] + [(rol, rol) for rol in ROLES]


class _ConEstilo:
    def _dar_estilo(self):
        for campo in self.fields.values():
            campo.widget.attrs.setdefault("class", "campo")


class FormularioUsuario(_ConEstilo, forms.ModelForm):
    """Alta y edición de usuarios. El nombre de usuario no se cambia después de crearlo (la bitácora lo usa)."""

    rol = forms.ChoiceField(choices=OPCIONES_ROL, required=False,
                            help_text="Define qué pantallas ve y qué puede hacer. Revisa la tabla de roles.")

    class Meta:
        model = Usuario
        fields = ["username", "first_name", "last_name", "email"]
        labels = {"username": "Usuario", "first_name": "Nombre", "last_name": "Apellidos", "email": "Correo"}
        help_texts = {"username": "Con este nombre inicia sesión. Letras, números y . @ + - _"}

    def __init__(self, *args, editar=False, **kwargs):
        super().__init__(*args, **kwargs)
        if editar:
            del self.fields["username"]
        self._dar_estilo()


class FormularioCambiarContrasena(_ConEstilo, PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["old_password"].label = "Contraseña actual"
        self.fields["new_password1"].label = "Contraseña nueva"
        self.fields["new_password2"].label = "Repite la contraseña nueva"
        self._dar_estilo()