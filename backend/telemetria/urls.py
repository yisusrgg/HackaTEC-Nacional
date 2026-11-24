from django.urls import path

from .api import HoraView, IngestaView

app_name = "telemetria"

urlpatterns = [
    path("api/v1/ingesta/", IngestaView.as_view(), name="ingesta"),
    path("api/v1/hora/", HoraView.as_view(), name="hora"),
]
