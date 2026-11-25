from django import forms

from confiabilidad.models import TipoComponente
from telemetria.models import Senal


class _ConEstilo:
    def _dar_estilo(self):
        for campo in self.fields.values():
            if not isinstance(campo.widget, forms.CheckboxInput):
                campo.widget.attrs.setdefault("class", "campo")


class FormularioTipo(_ConEstilo, forms.ModelForm):
    class Meta:
        model = TipoComponente
        fields = ["codigo", "nombre", "subsistema", "fabricante", "modelo", "unidad_vida", "vida_garantizada",
                  "limite_prematura", "regla", "medicion", "activo", "nota"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subsistema"].choices = [(c, n) for c, n in Senal.Subsistema.choices if c != Senal.Subsistema.NODO]
        if self.instance.pk:
            self.fields["codigo"].disabled = True  # otras partes del sistema lo usan como referencia
        self._dar_estilo()


class FormularioContador(_ConEstilo, forms.Form):
    ciclos = forms.IntegerField(min_value=0)
    horas = forms.FloatField(min_value=0)
    motivo = forms.CharField(max_length=255, help_text="Queda en la bitácora junto con el valor anterior.",
                             widget=forms.TextInput(attrs={"placeholder": "Ejemplo: pieza reacondicionada, se toma el uso del proveedor"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dar_estilo()


class FormularioSenal(_ConEstilo, forms.ModelForm):
    class Meta:
        model = Senal
        fields = ["rango_min", "rango_max", "umbral_bajo", "umbral_alto", "severidad_alerta"]
        labels = {"rango_min": "Rango válido: mínimo", "rango_max": "Rango válido: máximo",
                  "umbral_bajo": "Alerta si baja de", "umbral_alto": "Alerta si sube de",
                  "severidad_alerta": "Severidad de la alerta"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dar_estilo()

    def clean(self):
        datos = super().clean()
        minimo, maximo = datos.get("rango_min"), datos.get("rango_max")
        bajo, alto = datos.get("umbral_bajo"), datos.get("umbral_alto")
        if minimo is not None and maximo is not None and minimo >= maximo:
            self.add_error("rango_max", "Debe ser mayor que el mínimo.")
        if bajo is not None and alto is not None and bajo >= alto:
            self.add_error("umbral_alto", "Debe ser mayor que el umbral bajo.")
        return datos
