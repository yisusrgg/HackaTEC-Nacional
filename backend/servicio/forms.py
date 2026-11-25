from django import forms
from django.contrib.auth import get_user_model

from confiabilidad.models import ComponenteInstalado
from tablero.roles import INGENIERIA, TECNICO, TECNICO_CONVENIO
from telemetria.models import Nodo, Senal

from .models import OrdenServicio


class FormularioOrden(forms.ModelForm):
    class Meta:
        model = OrdenServicio
        fields = ["vehiculo", "titulo", "diagnostico", "componente", "responsable", "nodo", "senal", "minutos_validacion"]
        labels = {
            "vehiculo": "Unidad",
            "titulo": "Falla",
            "diagnostico": "Diagnóstico inicial",
            "componente": "Componente afectado",
            "responsable": "Asignar a",
            "nodo": "Nodo que mide la falla",
            "senal": "Señal para validar la reparación",
            "minutos_validacion": "Duración de la validación",
        }
        help_texts = {
            "componente": "De los instalados en la unidad. Si se cambia, sus ciclos u horas quedan como vida a la falla.",
            "responsable": "Técnico Volvo, Ingeniería o técnico de un taller con convenio (lo que registre lo valida Ingeniería).",
            "senal": "Después de la reparación, la orden se valida sola si esta señal se mantiene dentro de sus umbrales.",
            "minutos_validacion": "Tiempo que la señal debe mantenerse sin fallas para cerrar la orden.",
        }
        widgets = {
            "titulo": forms.TextInput(attrs={"placeholder": "Ejemplo: fuga de agua en el sanitario", "maxlength": 120}),
            "diagnostico": forms.Textarea(attrs={"rows": 3, "placeholder": "Qué se observó y la causa probable"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Usuario = get_user_model()
        self.fields["responsable"].queryset = (
            Usuario.objects.filter(is_active=True, groups__name__in=[TECNICO, INGENIERIA, TECNICO_CONVENIO])
            .distinct().order_by("username").prefetch_related("groups")
        )

        def etiqueta_usuario(u):
            nombre = u.get_full_name() or u.get_username()
            return f"{nombre} · convenio" if any(g.name == TECNICO_CONVENIO for g in u.groups.all()) else nombre

        self.fields["responsable"].label_from_instance = etiqueta_usuario
        self.fields["componente"].queryset = (ComponenteInstalado.objects.activos()
                                              .select_related("tipo", "vehiculo").order_by("vehiculo__identificador", "tipo__nombre"))
        self.fields["componente"].label_from_instance = (
            lambda c: f"{c.vehiculo.identificador} · {c.tipo.nombre} · {c.porcentaje_vida:.0f} % de vida")
        self.fields["nodo"].queryset = Nodo.objects.select_related("gateway__vehiculo").order_by("gateway_id", "numero")
        self.fields["nodo"].label_from_instance = lambda n: f"{n.gateway.vehiculo.identificador} · {n}"
        self.fields["senal"].queryset = (
            Senal.objects.filter(visible=True).exclude(subsistema=Senal.Subsistema.NODO)
            .exclude(umbral_alto__isnull=True, umbral_bajo__isnull=True)
        )
        self.fields["senal"].label_from_instance = lambda s: f"{s.nombre} · {s.get_subsistema_display()}"
        for campo in self.fields.values():
            campo.widget.attrs.setdefault("class", "campo")

    def clean(self):
        datos = super().clean()
        vehiculo, nodo, senal = datos.get("vehiculo"), datos.get("nodo"), datos.get("senal")
        if nodo and vehiculo and nodo.gateway.vehiculo_id != vehiculo.pk:
            self.add_error("nodo", "Ese nodo está instalado en otra unidad.")
        componente = datos.get("componente")
        if componente and vehiculo and componente.vehiculo_id != vehiculo.pk:
            self.add_error("componente", "Ese componente está instalado en otra unidad.")
        if senal and not nodo:
            self.add_error("nodo", "Elige el nodo que mide la señal para poder validarla con datos.")
        return datos