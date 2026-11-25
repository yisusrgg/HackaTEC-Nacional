"""Pantallas de confiabilidad: análisis por tipo de componente, componente instalado, catálogo, calidad de los
datos y señales y umbrales."""
from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, TemplateView

from auditoria.servicios import registrar
from auditoria.utils import ip_de
from confiabilidad import analisis
from confiabilidad.models import ComponenteInstalado, Falla, TipoComponente
from confiabilidad.servicios import instalar_faltantes
from servicio.analisis import CALIDAD_MINIMA
from telemetria.models import Lote, Medicion, Nodo, Senal, Vehiculo

from . import filtros
from .forms_confiabilidad import FormularioContador, FormularioSenal, FormularioTipo
from .views import Sev, Tipo, VistaProtegida

PERIODOS = [(90, "Últimos 90 días"), (180, "Últimos 6 meses"), (365, "Último año"), (1095, "Últimos 3 años")]


def _cambios(form):
    """{campo: [antes, después]} de lo que cambió en un ModelForm, para la bitácora."""
    return {campo: [str(form.initial.get(campo, "")), str(form.cleaned_data.get(campo, ""))]
            for campo in form.changed_data}


class ConfiabilidadView(VistaProtegida, TemplateView):
    """P05: por tipo de componente, vida real a la falla contra la garantía del fabricante."""

    permission_required = "confiabilidad.ver_confiabilidad"
    template_name = "tablero/confiabilidad.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        parametros = self.request.GET
        f = filtros.leer(parametros, PERIODOS, 365)
        fabricantes = list(TipoComponente.objects.exclude(fabricante="").values_list("fabricante", flat=True)
                           .distinct().order_by("fabricante"))
        fabricante = parametros.get("fabricante", "") if parametros.get("fabricante", "") in fabricantes else ""
        por = parametros.get("comparar", "region")
        por = por if por in dict(analisis.COMPARACIONES) else "region"
        ahora = timezone.now()
        desde = ahora - timedelta(days=f["dias"])
        vehiculos = filtros.vehiculos(f)
        fallas = analisis.fallas_confirmadas(vehiculos, desde, fabricante)
        contexto.update(filtros.contexto(f))
        contexto.update(
            periodos=PERIODOS, fabricantes=fabricantes, fabricante=fabricante,
            ocultos=[("comparar", por)],
            hay_filtros=filtros.hay_filtros(f) or fabricante or f["dias"] != 365,
            limpiar=reverse("tablero:confiabilidad"),
            q=filtros.a_url(f, dias=f["dias"], fabricante=fabricante),
            tipos=analisis.por_tipo(vehiculos, desde, desde + (ahora - desde) / 2, fabricante),
            total_fallas=fallas.count(),
            prematuras=fallas.filter(momento=Falla.Momento.TEMPRANA).count(),
            pareto=analisis.pareto_causas(fallas),
            comparaciones=analisis.COMPARACIONES, por=por,
            comparacion=analisis.comparar(vehiculos, fallas, por),
            muestra_minima=analisis.MUESTRA_MINIMA,
        )
        return contexto


class TipoComponenteView(VistaProtegida, TemplateView):
    """P06: un tipo de componente a fondo: cuándo falla, dónde y por qué."""

    permission_required = "confiabilidad.ver_confiabilidad"
    template_name = "tablero/componente_tipo.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        tipo = get_object_or_404(TipoComponente, pk=self.kwargs["pk"])
        f = filtros.leer(self.request.GET, PERIODOS, 1095)
        vehiculos = filtros.vehiculos(f)
        desde = timezone.now() - timedelta(days=f["dias"])
        fallas = analisis.fallas_confirmadas(vehiculos, desde).filter(componente__tipo=tipo)
        activos = ComponenteInstalado.objects.activos().filter(tipo=tipo, vehiculo__in=vehiculos).con_vida()
        contexto.update(filtros.contexto(f))
        contexto.update(
            tipo=tipo, periodos=PERIODOS, ocultos=[],
            hay_filtros=filtros.hay_filtros(f) or f["dias"] != 1095,
            limpiar=reverse("tablero:componente_tipo", args=[tipo.pk]),
            resumen=next((fila for fila in analisis.por_tipo(vehiculos, desde, desde, "") if fila["tipo"].pk == tipo.pk), None),
            histograma=analisis.histograma(fallas, tipo.limite_prematura),
            fallas=fallas.select_related("componente__vehiculo__cliente", "causa", "orden").order_by("-fecha")[:50],
            por_operador=analisis.comparar(vehiculos, fallas, "operador"),
            por_region=analisis.comparar(vehiculos, fallas, "region"),
            reincidencias=analisis.reincidencias(tipo, vehiculos),
            instalados=activos.count(),
            cerca=activos.select_related("vehiculo__cliente").filter(vida_pct__gte=80).order_by("-vida_pct")[:10],
            pareto=analisis.pareto_causas(fallas),
            puede_editar=self.request.user.has_perm("confiabilidad.gestionar_catalogo"),
        )
        return contexto


class ComponenteView(VistaProtegida, TemplateView):
    """P12: una pieza concreta: cuánto lleva, cuánto le queda y qué le ha pasado."""

    permission_required = "telemetria.ver_tablero"
    template_name = "tablero/componente.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        componente = get_object_or_404(ComponenteInstalado.objects.select_related("tipo", "vehiculo__cliente"),
                                       pk=self.kwargs["pk"])
        puede_corregir = self.request.user.has_perm("confiabilidad.gestionar_catalogo")
        contexto.update(
            componente=componente,
            fallas=componente.fallas.select_related("causa", "orden"),
            reemplaza_a=componente.reemplaza_a.select_related("componente").first(),
            ordenes=componente.ordenes.select_related("responsable").order_by("-creada"),
            posicion=(ComponenteInstalado.objects.filter(vehiculo=componente.vehiculo, tipo=componente.tipo)
                      .prefetch_related("fallas__causa").order_by("-instalado_en")),
            puede_corregir=puede_corregir and componente.activo,
            form=FormularioContador(initial={"ciclos": componente.ciclos, "horas": round(componente.horas, 1)}),
        )
        return contexto


class CorregirContadorView(VistaProtegida, View):
    permission_required = "confiabilidad.gestionar_catalogo"

    def post(self, request, pk):
        componente = get_object_or_404(ComponenteInstalado.objects.select_related("tipo", "vehiculo"), pk=pk)
        form = FormularioContador(request.POST)
        if not componente.activo or not form.is_valid():
            errores = "; ".join(e for lista in form.errors.values() for e in lista) or "la pieza ya está retirada"
            messages.error(request, f"No se corrigió el contador: {errores}.")
            return redirect("tablero:componente", pk=pk)
        antes = {"ciclos": componente.ciclos, "horas": round(componente.horas, 1)}
        componente.ciclos, componente.horas = form.cleaned_data["ciclos"], form.cleaned_data["horas"]
        componente.save(update_fields=["ciclos", "horas"])
        registrar(Tipo.CONTADOR_CORREGIDO, severidad=Sev.ADVERTENCIA, actor=request.user.get_username(), ip=ip_de(request),
                  detalle={"componente": componente.pk, "tipo": componente.tipo.nombre,
                           "unidad": componente.vehiculo.identificador, "antes": antes,
                           "despues": {"ciclos": componente.ciclos, "horas": componente.horas},
                           "motivo": form.cleaned_data["motivo"]})
        messages.success(request, "Contador corregido. El cambio quedó en la bitácora.")
        return redirect("tablero:componente", pk=pk)


class CatalogoView(VistaProtegida, ListView):
    """P20: qué dura cada pieza según el fabricante y cómo se cuenta su uso."""

    permission_required = "confiabilidad.gestionar_catalogo"
    template_name = "tablero/catalogo.html"
    context_object_name = "tipos"

    def get_queryset(self):
        return TipoComponente.objects.annotate(
            en_uso=Count("instalados", filter=Q(instalados__retirado_en__isnull=True)))


class TipoFormView(VistaProtegida, View):
    permission_required = "confiabilidad.gestionar_catalogo"
    template_name = "tablero/catalogo_form.html"

    def _tipo(self, pk):
        return get_object_or_404(TipoComponente, pk=pk) if pk else None

    def get(self, request, pk=None):
        tipo = self._tipo(pk)
        return render(request, self.template_name, {"form": FormularioTipo(instance=tipo), "tipo": tipo})

    def post(self, request, pk=None):
        tipo = self._tipo(pk)
        form = FormularioTipo(request.POST, instance=tipo)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "tipo": tipo}, status=400)
        with transaction.atomic():
            cambios = _cambios(form) if tipo else None
            tipo = form.save()
            instalados = 0
            if cambios is None and tipo.activo:  # un tipo nuevo se instala en todas las unidades, en cero
                instalados = sum(instalar_faltantes(v, tipos=[tipo]) for v in Vehiculo.objects.all())
        registrar(Tipo.CATALOGO_MODIFICADO, actor=request.user.get_username(), ip=ip_de(request),
                  detalle={"catalogo": "componentes", "tipo": tipo.codigo,
                           "accion": "alta" if cambios is None else "cambio", "cambios": cambios or {}})
        messages.success(request, f"«{tipo.nombre}» guardado." + (f" Se instaló en {instalados} unidades." if instalados else ""))
        return redirect("tablero:catalogo")


class CalidadView(VistaProtegida, TemplateView):
    """P08: separar un sensor dañado o un equipo sin reportar de una falla real del autobús."""

    permission_required = "confiabilidad.ver_confiabilidad"
    template_name = "tablero/calidad.html"
    PERIODOS = [(1, "Últimas 24 h"), (7, "Últimos 7 días"), (30, "Últimos 30 días")]

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        f = filtros.leer(self.request.GET, self.PERIODOS, 1)
        ahora = timezone.now()
        desde = ahora - timedelta(days=f["dias"])
        vehiculos = filtros.vehiculos(f)
        ids = vehiculos.values("pk")

        sin_reportar = (vehiculos.exclude(gateways__ultimo_contacto__gte=ahora - timedelta(hours=24))
                        .select_related("cliente").order_by("identificador"))
        nodos = Nodo.objects.filter(gateway__vehiculo__in=ids)
        totales = nodos.aggregate(recibidas=Sum("tramas_recibidas"), perdidas=Sum("tramas_perdidas"))
        enviadas = (totales["recibidas"] or 0) + (totales["perdidas"] or 0)
        lecturas = (Medicion.objects.filter(ts__gte=desde, nodo__gateway__vehiculo__in=ids)
                    .exclude(senal__subsistema=Senal.Subsistema.NODO))
        global_ = lecturas.aggregate(total=Count("id"), ok=Count("id", filter=Q(calidad=Medicion.Calidad.OK)))
        sensores = []
        for fila in (lecturas.values("nodo__gateway__vehiculo", "nodo__gateway__vehiculo__identificador", "nodo",
                                     "senal", "senal__nombre", "senal__codigo")
                     .annotate(total=Count("id"), ok=Count("id", filter=Q(calidad=Medicion.Calidad.OK)),
                               sin_dato=Count("id", filter=Q(calidad=Medicion.Calidad.SIN_DATO)))):
            validas = round(100 * fila["ok"] / fila["total"], 1)
            if validas < CALIDAD_MINIMA:
                sensores.append({**fila, "validas": validas})
        sensores.sort(key=lambda s: s["validas"])

        contexto.update(filtros.contexto(f))
        contexto.update(
            periodos=self.PERIODOS, ocultos=[], hay_filtros=filtros.hay_filtros(f) or f["dias"] != 1,
            limpiar=reverse("tablero:calidad"),
            total_unidades=vehiculos.count(), sin_reportar=sin_reportar, n_sin_reportar=sin_reportar.count(),
            perdida=round(100 * (totales["perdidas"] or 0) / enviadas, 2) if enviadas else None,
            validas=round(100 * global_["ok"] / global_["total"], 1) if global_["total"] else None,
            lecturas=global_["total"], sensores=sensores[:30], calidad_minima=CALIDAD_MINIMA,
            gateways=(Lote.objects.filter(recibido__gte=desde, gateway__vehiculo__in=ids)
                      .values("gateway__identificador", "gateway__vehiculo", "gateway__vehiculo__identificador")
                      .annotate(rechazadas=Sum("rechazadas"), tramas=Sum("tramas")).filter(rechazadas__gt=0)
                      .order_by("-rechazadas")[:10]),
            nodos_reinicios=nodos.filter(reinicios__gt=0).select_related("gateway__vehiculo").order_by("-reinicios")[:10],
            nodos_rechazos=nodos.filter(tramas_rechazadas__gt=0).select_related("gateway__vehiculo")
                                .order_by("-tramas_rechazadas")[:10],
        )
        return contexto


class SenalesView(VistaProtegida, ListView):
    """P23: rangos válidos y umbrales de alerta de las señales, sin entrar al panel de Django."""

    permission_required = "confiabilidad.gestionar_catalogo"
    template_name = "tablero/senales.html"
    context_object_name = "senales"
    queryset = Senal.objects.order_by("subsistema", "orden", "codigo")


class SenalFormView(VistaProtegida, View):
    permission_required = "confiabilidad.gestionar_catalogo"
    template_name = "tablero/senal_form.html"

    def get(self, request, pk):
        senal = get_object_or_404(Senal, pk=pk)
        return render(request, self.template_name, {"form": FormularioSenal(instance=senal), "senal": senal})

    def post(self, request, pk):
        senal = get_object_or_404(Senal, pk=pk)
        form = FormularioSenal(request.POST, instance=senal)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "senal": senal}, status=400)
        cambios = _cambios(form)
        form.save()
        if cambios:
            registrar(Tipo.CATALOGO_MODIFICADO, severidad=Sev.ADVERTENCIA, actor=request.user.get_username(),
                      ip=ip_de(request), detalle={"catalogo": "señales", "senal": senal.codigo, "cambios": cambios})
            messages.success(request, f"Umbrales de «{senal.nombre}» guardados. Las alertas nuevas ya los usan.")
        else:
            messages.info(request, "No hubo cambios.")
        return redirect("tablero:senales")
