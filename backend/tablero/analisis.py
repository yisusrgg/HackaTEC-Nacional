"""Pantalla de análisis de calidad (Ingeniería y Administración)."""
from django.views.generic import TemplateView

from servicio import analisis
from telemetria.models import Senal, Vehiculo

from .views import VistaProtegida


class AnalisisView(VistaProtegida, TemplateView):
    permission_required = "servicio.ver_analisis"
    template_name = "tablero/analisis.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        parametros = self.request.GET
        try:
            dias = int(parametros.get("dias", 30))
        except ValueError:
            dias = 30
        if dias not in dict(analisis.PERIODOS):
            dias = 30
        vehiculo = Vehiculo.objects.filter(pk=parametros.get("vehiculo")).first() if parametros.get("vehiculo", "").isdigit() else None
        subsistemas = [(c, n) for c, n in Senal.Subsistema.choices if c != Senal.Subsistema.NODO]
        subsistema = parametros.get("subsistema") if parametros.get("subsistema") in dict(subsistemas) else None

        datos = analisis.calcular(dias=dias, vehiculo=vehiculo, subsistema=subsistema)
        contexto.update(
            datos=datos,
            grafica={"tendencia": datos["tendencia"]},
            periodos=analisis.PERIODOS, dias=dias,
            vehiculos=Vehiculo.objects.order_by("identificador"), vehiculo=vehiculo,
            subsistemas=subsistemas, subsistema=subsistema,
        )
        return contexto