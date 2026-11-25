"""
Panorama (pantalla de inicio de Ingeniería): cómo está toda la flota y qué necesita atención primero.

Abre siempre con toda la flota de todos los operadores; operador, región y modelo son filtros opcionales.
Cada número enlaza a la lista ya filtrada.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from confiabilidad.models import PORCENTAJE_CERCA_DEL_LIMITE, ComponenteInstalado, Falla
from servicio.models import OrdenServicio
from telemetria.models import Alerta, Medicion, Senal, Severidad

from . import filtros
from .views import VistaProtegida

PERIODOS = [(90, "Últimos 90 días"), (180, "Últimos 6 meses"), (365, "Último año")]
SEMANAS = 12
SUBSISTEMAS = [(c, n) for c, n in Senal.Subsistema.choices if c != Senal.Subsistema.NODO]


class InicioView(LoginRequiredMixin, View):
    """Cada rol entra a su pantalla: Ingeniería → Panorama, Administrador → Usuarios, técnicos → Mis pendientes."""

    def get(self, request):
        u = request.user
        if u.has_perm("confiabilidad.ver_confiabilidad"):
            return redirect("tablero:panorama")
        if u.has_perm("auth.change_user"):
            return redirect("tablero:usuarios")
        if u.has_perm("telemetria.ver_tablero") and u.has_perm("servicio.gestionar_ordenes"):
            return redirect(reverse("tablero:ordenes") + "?estado=mis")
        if u.has_perm("telemetria.ver_tablero"):
            return redirect("tablero:panorama")
        raise PermissionDenied


def alertas_de(vehiculos):
    return Alerta.objects.filter(Q(gateway__vehiculo__in=vehiculos) | Q(nodo__gateway__vehiculo__in=vehiculos))


def calcular(f, ahora=None):
    ahora = ahora or timezone.now()
    vehiculos = filtros.vehiculos(f)
    ids = vehiculos.values("pk")
    hace_24h = ahora - timedelta(hours=24)

    total = vehiculos.count()
    reportando = vehiculos.filter(gateways__ultimo_contacto__gte=hace_24h).distinct().count()
    lecturas = (Medicion.objects.filter(ts__gte=hace_24h, nodo__gateway__vehiculo__in=ids)
                .exclude(senal__subsistema=Senal.Subsistema.NODO)
                .aggregate(total=Count("id"), validas=Count("id", filter=Q(calidad=Medicion.Calidad.OK))))
    activas = alertas_de(ids).filter(estado__in=Alerta.ACTIVAS).distinct()
    criticas_sin_orden = activas.filter(severidad=Severidad.CRITICA, estado=Alerta.Estado.ABIERTA)  # «Atendida» = ya tiene orden
    componentes = ComponenteInstalado.objects.activos().filter(vehiculo__in=ids).con_vida()
    fallas = Falla.objects.filter(confirmada=True, componente__vehiculo__in=ids)
    ordenes = OrdenServicio.objects.filter(vehiculo__in=ids)

    return {
        "indicadores": {
            "total": total,
            "reportando": reportando,
            "lecturas": lecturas["total"],
            "validas": round(100 * lecturas["validas"] / lecturas["total"], 1) if lecturas["total"] else None,
            "criticas_sin_orden": criticas_sin_orden.count(),
            "activas": activas.count(),
            "cerca": componentes.filter(vida_pct__gte=PORCENTAJE_CERCA_DEL_LIMITE).count(),
            "prematuras_mes": fallas.filter(momento=Falla.Momento.TEMPRANA, fecha__gte=ahora - timedelta(days=30)).count(),
            "en_validacion": ordenes.filter(etapa=OrdenServicio.Etapa.VALIDACION).count(),
            "abiertas": ordenes.filter(etapa__in=OrdenServicio.ABIERTAS).count(),
        },
        "atencion": list(criticas_sin_orden.select_related("senal", "nodo__gateway__vehiculo__cliente",
                                                            "gateway__vehiculo__cliente").order_by("creada")[:10]),
        "cerca_del_limite": list(componentes.select_related("tipo", "vehiculo__cliente")
                                 .filter(vida_pct__gte=50).order_by("-vida_pct")[:10]),
        "grafica": _fallas_por_semana(fallas, ahora),
        "calor": _mapa_de_calor(vehiculos, fallas.filter(fecha__gte=ahora - timedelta(days=f["dias"]))),
    }


def _fallas_por_semana(fallas, ahora):
    """Fallas confirmadas por semana (las últimas 12) y por subsistema, para la gráfica apilada."""
    hoy = timezone.localdate(ahora)
    inicio = hoy - timedelta(days=hoy.weekday()) - timedelta(weeks=SEMANAS - 1)  # lunes de hace 11 semanas
    semanas = [inicio + timedelta(weeks=i) for i in range(SEMANAS)]
    conteo = defaultdict(int)
    desde = timezone.make_aware(datetime.combine(inicio, time.min))
    for fecha, subsistema in fallas.filter(fecha__gte=desde).values_list("fecha", "componente__tipo__subsistema"):
        dia = timezone.localdate(fecha)
        conteo[(subsistema, (dia - inicio).days // 7)] += 1
    nombres = dict(SUBSISTEMAS)
    return {
        "semanas": [s.isoformat() for s in semanas],
        "series": [{"nombre": nombres[c], "codigo": c, "datos": [conteo[(c, i)] for i in range(SEMANAS)]}
                   for c, _ in SUBSISTEMAS if any(conteo[(c, i)] for i in range(SEMANAS))],
    }


def _mapa_de_calor(vehiculos, fallas):
    """Fallas por cada 100 unidades, por subsistema (filas) y operador (columnas). El nivel 0–4 da el tono."""
    unidades = {fila["cliente"]: (fila["cliente__nombre_comercial"], fila["n"])
                for fila in vehiculos.values("cliente", "cliente__nombre_comercial").annotate(n=Count("id"))}
    conteo = {(fila["componente__tipo__subsistema"], fila["componente__vehiculo__cliente"]): fila["n"]
              for fila in fallas.values("componente__tipo__subsistema", "componente__vehiculo__cliente")
              .annotate(n=Count("id"))}
    operadores = sorted(unidades.items(), key=lambda par: par[1][0].lower())
    tasas = {clave: 100 * n / unidades[clave[1]][1] for clave, n in conteo.items() if clave[1] in unidades}
    maximo = max(tasas.values(), default=0)
    filas = []
    for codigo, nombre in SUBSISTEMAS:
        celdas = []
        for cliente_id, (_, n_unidades) in operadores:
            tasa = tasas.get((codigo, cliente_id), 0)
            nivel = 0 if not tasa or not maximo else min(4, 1 + int(3.999 * tasa / maximo))
            celdas.append({"tasa": round(tasa), "fallas": conteo.get((codigo, cliente_id), 0), "nivel": nivel,
                           "operador": cliente_id})
        filas.append({"codigo": codigo, "nombre": nombre, "celdas": celdas})
    return {"operadores": [{"id": c, "nombre": n, "unidades": u} for c, (n, u) in operadores], "filas": filas}


class PanoramaView(VistaProtegida, TemplateView):
    permission_required = "telemetria.ver_tablero"
    template_name = "tablero/panorama.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        f = filtros.leer(self.request.GET, PERIODOS, 365)
        datos = calcular(f)
        contexto.update(filtros.contexto(f))
        contexto.update(
            datos=datos, grafica=datos["grafica"], periodos=PERIODOS,
            hay_filtros=filtros.hay_filtros(f) or f["dias"] != 365,
            limpiar=reverse("tablero:panorama"), q=filtros.a_url(f),
        )
        return contexto
