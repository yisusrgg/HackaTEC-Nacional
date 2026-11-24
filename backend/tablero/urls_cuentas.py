from django.contrib.auth import views as auth_views
from django.urls import path

from .forms import FormularioLogin

app_name = "cuentas"

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(authentication_form=FormularioLogin, redirect_authenticated_user=True),
         name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
