"""
Usuarios y roles: alta, cambio de rol, desactivación y restablecimiento de contraseña (solo Administrador),
y cambio de la propia contraseña (cualquier usuario). Todo queda en la bitácora de seguridad.
"""
import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.cache import add_never_cache_headers
from django.views import View
from django.views.generic import ListView

from auditoria.servicios import registrar
from auditoria.utils import ip_de

from .forms_usuarios import FormularioCambiarContrasena, FormularioUsuario
from .roles import ADMINISTRADOR, INGENIERIA, ROLES, TECNICO
from .views import Sev, Tipo, VistaProtegida

Usuario = get_user_model()

# Qué puede hacer cada rol, en palabras (se arma a partir de los permisos de roles.py).
ACCIONES = [
    ("telemetria.ver_tablero", "Ver resumen, alertas, dispositivos, historial y órdenes"),
    ("telemetria.atender_alerta", "Atender y resolver alertas"),
    ("servicio.gestionar_ordenes", "Crear y avanzar órdenes de servicio"),
    ("servicio.validar_ordenes", "Validar, cerrar y cancelar órdenes"),
    ("servicio.ver_analisis", "Ver el análisis de calidad"),
    ("telemetria.exportar_datos", "Exportar datos a CSV"),
    ("telemetria.gestionar_dispositivos", "Generar claves y revocar dispositivos"),
    ("auditoria.ver_auditoria", "Ver la bitácora de seguridad"),
    ("auth.change_user", "Administrar usuarios y roles"),
]


def rol_de(usuario):
    nombres = {g.name for g in usuario.groups.all()}
    return next((rol for rol in ROLES if rol in nombres), "")


def asignar_rol(usuario, rol):
    """Cada usuario tiene un solo rol. El Administrador también entra al panel de Django."""
    usuario.groups.remove(*Group.objects.filter(name__in=ROLES))
    if rol:
        usuario.groups.add(Group.objects.get(name=rol))
    if not usuario.is_superuser:
        usuario.is_staff = rol == ADMINISTRADOR
        usuario.save(update_fields=["is_staff"])


def contrasena_temporal(usuario):
    """Contraseña aleatoria que cumple las reglas de seguridad del proyecto."""
    while True:
        clave = secrets.token_urlsafe(12)
        try:
            validate_password(clave, usuario)
            return clave
        except ValidationError:
            continue


def mostrar_clave(request, usuario, clave, motivo):
    respuesta = render(request, "tablero/usuario_clave.html", {"usuario": usuario, "clave": clave, "motivo": motivo})
    add_never_cache_headers(respuesta)
    return respuesta


class UsuariosView(VistaProtegida, ListView):
    permission_required = "auth.view_user"
    template_name = "tablero/usuarios.html"
    context_object_name = "usuarios"
    paginate_by = 50

    def get_queryset(self):
        consulta = Usuario.objects.prefetch_related("groups").order_by("-is_active", "username")
        self.rol = self.request.GET.get("rol", "")
        self.buscar = self.request.GET.get("q", "").strip()[:60]
        if self.rol in ROLES:
            consulta = consulta.filter(groups__name=self.rol)
        elif self.rol == "sin":
            consulta = consulta.exclude(groups__name__in=ROLES)
        if self.buscar:
            consulta = consulta.filter(Q(username__icontains=self.buscar) | Q(first_name__icontains=self.buscar)
                                       | Q(last_name__icontains=self.buscar) | Q(email__icontains=self.buscar))
        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        for usuario in contexto["usuarios"]:
            usuario.rol = rol_de(usuario)
        columnas = [ADMINISTRADOR, INGENIERIA, TECNICO]
        matriz = [(texto, [permiso in ROLES[rol] or (permiso == "auth.change_user" and rol == ADMINISTRADOR)
                           for rol in columnas]) for permiso, texto in ACCIONES]
        contexto.update(
            roles=list(ROLES), rol=self.rol, buscar=self.buscar, columnas=columnas, matriz=matriz,
            parametros="&".join(p for p in [f"rol={self.rol}" if self.rol else "", f"q={self.buscar}" if self.buscar else ""] if p),
            total_activos=Usuario.objects.filter(is_active=True).count(),
        )
        return contexto


class UsuarioNuevoView(VistaProtegida, View):
    permission_required = "auth.add_user"
    template_name = "tablero/usuario_form.html"

    def get(self, request):
        return render(request, self.template_name, {"form": FormularioUsuario(), "nuevo": True})

    def post(self, request):
        form = FormularioUsuario(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "nuevo": True}, status=400)
        with transaction.atomic():
            usuario = form.save(commit=False)
            clave = contrasena_temporal(usuario)
            usuario.set_password(clave)
            usuario.save()
            asignar_rol(usuario, form.cleaned_data["rol"])
        registrar(Tipo.USUARIO_CREADO, severidad=Sev.ADVERTENCIA, actor=request.user.get_username(), ip=ip_de(request),
                  detalle={"usuario": usuario.get_username(), "rol": form.cleaned_data["rol"] or "sin rol"})
        return mostrar_clave(request, usuario, clave, "nuevo")


class _EditarBase(VistaProtegida, View):
    permission_required = "auth.change_user"

    def obtener(self, request, pk):
        usuario = get_object_or_404(Usuario, pk=pk)
        if usuario.is_superuser and not request.user.is_superuser:
            raise PermissionDenied  # solo un superusuario modifica a otro superusuario
        return usuario


class UsuarioEditarView(_EditarBase):
    template_name = "tablero/usuario_form.html"

    def get(self, request, pk):
        usuario = self.obtener(request, pk)
        form = FormularioUsuario(instance=usuario, editar=True, initial={"rol": rol_de(usuario)})
        return render(request, self.template_name, {"form": form, "objetivo": usuario, "es_yo": usuario == request.user})

    def post(self, request, pk):
        usuario = self.obtener(request, pk)
        rol_anterior = rol_de(usuario)
        form = FormularioUsuario(request.POST, instance=usuario, editar=True)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "objetivo": usuario, "es_yo": usuario == request.user},
                          status=400)
        rol = form.cleaned_data["rol"]
        if usuario == request.user and rol != rol_anterior and not usuario.is_superuser:
            form.add_error("rol", "No puedes cambiar tu propio rol: pídeselo a otro administrador.")
            return render(request, self.template_name, {"form": form, "objetivo": usuario, "es_yo": True}, status=400)
        with transaction.atomic():
            form.save()
            asignar_rol(usuario, rol)
        registrar(Tipo.USUARIO_MODIFICADO, severidad=Sev.ADVERTENCIA if rol != rol_anterior else Sev.INFO,
                  actor=request.user.get_username(), ip=ip_de(request),
                  detalle={"usuario": usuario.get_username(), "rol_anterior": rol_anterior or "sin rol", "rol": rol or "sin rol"})
        messages.success(request, f"Usuario {usuario.get_username()} actualizado.")
        return redirect("tablero:usuarios")


class UsuarioAccionView(_EditarBase):
    """Desactivar, reactivar o restablecer la contraseña de un usuario."""

    def post(self, request, pk):
        usuario = self.obtener(request, pk)
        accion = request.POST.get("accion")
        nombre = usuario.get_username()
        if accion in ("desactivar", "activar"):
            if usuario == request.user:
                messages.error(request, "No puedes desactivar tu propia cuenta.")
                return redirect("tablero:usuario_editar", pk=usuario.pk)
            usuario.is_active = accion == "activar"
            usuario.save(update_fields=["is_active"])
            registrar(Tipo.USUARIO_MODIFICADO, severidad=Sev.ADVERTENCIA, actor=request.user.get_username(),
                      ip=ip_de(request), detalle={"usuario": nombre, "accion": accion})
            messages.success(request, f"{nombre} {'reactivado: ya puede entrar' if usuario.is_active else 'desactivado: ya no puede entrar'}.")
            return redirect("tablero:usuarios")
        if accion == "restablecer":
            clave = contrasena_temporal(usuario)
            usuario.set_password(clave)
            usuario.save(update_fields=["password"])  # cierra las sesiones abiertas de ese usuario
            registrar(Tipo.CONTRASENA_RESTABLECIDA, severidad=Sev.ADVERTENCIA, actor=request.user.get_username(),
                      ip=ip_de(request), detalle={"usuario": nombre})
            return mostrar_clave(request, usuario, clave, "restablecida")
        messages.error(request, "Acción no válida.")
        return redirect("tablero:usuarios")


class CambiarContrasenaView(LoginRequiredMixin, auth_views.PasswordChangeView):
    """Mi cuenta: cualquier usuario cambia su propia contraseña."""

    form_class = FormularioCambiarContrasena
    template_name = "registration/contrasena.html"
    success_url = reverse_lazy("tablero:resumen")

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        update_session_auth_hash(self.request, form.user)
        registrar(Tipo.CONTRASENA_CAMBIADA, actor=self.request.user.get_username(), ip=ip_de(self.request))
        messages.success(self.request, "Contraseña cambiada. Tus otras sesiones abiertas se cerraron.")
        return respuesta