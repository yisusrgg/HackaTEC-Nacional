"""
Unidades (P09), ficha de la unidad (P10) y dispositivos de toda la flota (P24).

Filtros de Unidades por URL (se ignoran los valores no válidos):
  ?operador=<id>  ?region=<clave>  ?modelo=<texto>  ?alertas=con|criticas|sin  ?conexion=en_linea|fuera
  ?componente=cerca  ?q=<unidad, nombre, operador o número de serie>
"""
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.generic import ListView, TemplateView

from confiabilidad.models import PORCENTAJE_CERCA_DEL_LIMITE, ComponenteInstalado, Falla
from servicio.models import OrdenServicio
from telemetria.models import Alerta, Gateway, Nodo, Severidad, Vehiculo

from . import filtros
from .acceso import AccesoPorUnidadMixin, exigir_unidad, solo_asignadas, unidades_permitidas
from .api import alertas_del_autobus
from .flota import _alertas_por_vehiculo, _estado_autobus
from .views import VistaProtegida

FILTRO_ALERTAS = [("", "Todas"), ("con", "Con alertas activas"), ("criticas", "Con alertas críticas"),
                  ("sin", "Sin alertas")]
FILTRO_CONEXION = [("", "Todas"), ("en_linea", "En línea"), ("fuera", "Fuera de línea")]
FILTRO_COMPONENTE = [("", "Todos"), ("cerca", f"Alguno arriba del {PORCENTAJE_CERCA_DEL_LIMITE} %")]


def _vehiculos_con_alertas(solo_criticas=False):
    activas = Alerta.objects.filter(estado__in=Alerta.ACTIVAS)
    if solo_criticas:
        activas = activas.filter(severidad=Severidad.CRITICA)
    ids = set(activas.filter(gateway__isnull=False).values_list("gateway__vehiculo_id", flat=True))
    ids |= set(activas.filter(nodo__isnull=False).values_list("nodo__gateway__vehiculo_id", flat=True))
    return ids


def _cerca_del_limite():
    return (ComponenteInstalado.objects.activos().con_vida().filter(vida_pct__gte=PORCENTAJE_CERCA_DEL_LIMITE)
            .values("vehiculo"))


def leer_filtros(parametros):
    f = filtros.leer(parametros)
    alertas, conexion, componente = parametros.get("alertas", ""), parametros.get("conexion", ""), parametros.get("componente", "")
    f.update(
        alertas=alertas if alertas in dict(FILTRO_ALERTAS) else "",
        conexion=conexion if conexion in dict(FILTRO_CONEXION) else "",
        componente=componente if componente in dict(FILTRO_COMPONENTE) else "",
        q=parametros.get("q", "").strip()[:40],
    )
    return f


def filtrar(consulta, f):
    consulta = filtros.vehiculos(f, consulta)
    if f["q"]:
        consulta = consulta.filter(Q(identificador__icontains=f["q"]) | Q(nombre__icontains=f["q"])
                                   | Q(cliente__nombre_comercial__icontains=f["q"])
                                   | Q(componentes__numero_serie__icontains=f["q"]))
    if f["alertas"] == "con":
        consulta = consulta.filter(pk__in=_vehiculos_con_alertas())
    elif f["alertas"] == "criticas":
        consulta = consulta.filter(pk__in=_vehiculos_con_alertas(solo_criticas=True))
    elif f["alertas"] == "sin":
        consulta = consulta.exclude(pk__in=_vehiculos_con_alertas())
    if f["conexion"]:
        limite = timezone.now() - timedelta(seconds=settings.TELEMETRIA["SEGUNDOS_EN_LINEA"])
        en_linea = Q(gateways__activo=True, gateways__ultimo_contacto__gte=limite)
        consulta = consulta.filter(en_linea) if f["conexion"] == "en_linea" else consulta.exclude(en_linea)
    if f["componente"] == "cerca":
        consulta = consulta.filter(pk__in=_cerca_del_limite())
    return consulta.distinct()


def parametros_url(f):
    return filtros.a_url(f, alertas=f["alertas"], conexion=f["conexion"], componente=f["componente"], q=f["q"])


class AutobusesView(AccesoPorUnidadMixin, VistaProtegida, ListView):
    """P09 Unidades: encontrar cualquier unidad entre miles (búsqueda y paginación en el servidor)."""

    template_name = "tablero/autobuses.html"
    context_object_name = "vehiculos"
    paginate_by = 50

    def get_queryset(self):
        self.filtros = leer_filtros(self.request.GET)
        consulta = Vehiculo.objects.select_related("cliente").prefetch_related(
            Prefetch("gateways", queryset=Gateway.objects.only("id", "vehiculo_id", "activo", "ultimo_contacto")))
        permitidas = unidades_permitidas(self.request.user)
        if permitidas is not None:
            consulta = consulta.filter(pk__in=permitidas)
        return filtrar(consulta, self.filtros).order_by("cliente__nombre_comercial", "identificador")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        vehiculos = contexto["vehiculos"]
        alertas, _ = _alertas_por_vehiculo()
        peor = {}
        for c in (ComponenteInstalado.objects.activos().con_vida().select_related("tipo")
                  .filter(vehiculo__in=[v.pk for v in vehiculos]).order_by("vehiculo", "-vida_pct")):
            peor.setdefault(c.vehiculo_id, c)
        autobuses = []
        for v in vehiculos:
            estado = _estado_autobus(v, alertas.get(v.pk, []))
            estado["componente"] = peor.get(v.pk)
            autobuses.append(estado)
        f = self.filtros
        contexto.update(filtros.contexto(f))
        contexto.update(
            autobuses=autobuses, parametros=parametros_url(f),
            hay_filtros=bool(parametros_url(f)), limpiar=reverse("tablero:autobuses"),
            filtro_alertas=FILTRO_ALERTAS, filtro_conexion=FILTRO_CONEXION, filtro_componente=FILTRO_COMPONENTE,
            restringido=solo_asignadas(self.request.user),
        )
        return contexto


class FichaView(AccesoPorUnidadMixin, VistaProtegida, TemplateView):
    """P10 Ficha de la unidad: todo lo de una unidad, en pestañas. «En vivo» es la vista de monitoreo."""

    template_name = "tablero/ficha.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        vehiculo = get_object_or_404(Vehiculo.objects.select_related("cliente"), pk=self.kwargs["pk"])
        exigir_unidad(self.request, vehiculo.pk)
        pestanas = pestanas_de(self.request.user)
        pestana = self.request.GET.get("pestana", "resumen")
        if pestana not in pestanas or pestana == "en_vivo":
            pestana = "resumen"
        componentes = list(ComponenteInstalado.objects.activos().filter(vehiculo=vehiculo).con_vida()
                           .select_related("tipo").order_by("-vida_pct"))
        contexto.update(vehiculo=vehiculo, pestana=pestana, pestanas=pestanas, componentes=componentes,
                        estado=_estado_autobus(vehiculo, list(alertas_del_autobus(vehiculo))))
        if pestana == "resumen":
            contexto.update(
                alertas=alertas_del_autobus(vehiculo).select_related("senal").distinct().order_by("-ultima_vez")[:6],
                ordenes=OrdenServicio.objects.filter(vehiculo=vehiculo, etapa__in=OrdenServicio.ABIERTAS)
                                             .select_related("responsable", "componente__tipo"),
            )
        elif pestana == "fallas":
            contexto.update(
                fallas=Falla.objects.filter(componente__vehiculo=vehiculo)
                                    .select_related("componente__tipo", "causa", "orden").order_by("-fecha"),
                ordenes=OrdenServicio.objects.filter(vehiculo=vehiculo).select_related("responsable", "componente__tipo"),
            )
        elif pestana == "dispositivos":
            gateways = Gateway.objects.filter(vehiculo=vehiculo).prefetch_related(
                Prefetch("nodos", queryset=Nodo.objects.order_by("numero")))
            contexto.update(gateways=gateways,
                            puede_gestionar=self.request.user.has_perm("telemetria.gestionar_dispositivos"))
        return contexto


def pestanas_de(usuario):
    """Pestañas de la ficha que ve cada rol (en orden)."""
    todas = [("resumen", "Resumen"), ("componentes", "Componentes"), ("en_vivo", "En vivo"),
             ("fallas", "Fallas y órdenes"), ("dispositivos", "Dispositivos")]
    permitidas = {"resumen", "componentes", "en_vivo"}
    if usuario.has_perm("telemetria.ver_tablero"):
        permitidas.add("fallas")
    if usuario.has_perm("telemetria.gestionar_dispositivos"):
        permitidas.add("dispositivos")
    return dict((c, n) for c, n in todas if c in permitidas)


class DispositivosView(VistaProtegida, TemplateView):
    """P24: todos los gateways y nodos de la flota, para rotar claves y revocar equipos."""

    permission_required = "telemetria.gestionar_dispositivos"
    template_name = "tablero/dispositivos.html"
    FILTROS = [("", "Todos"), ("clave_vieja", "Clave de más de 90 días"), ("sin_contacto", "Sin contacto en 24 h"),
               ("revocados", "Revocados")]
    POR_PAGINA = 50

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        p = self.request.GET
        estado = p.get("estado", "") if p.get("estado", "") in dict(self.FILTROS) else ""
        q = p.get("q", "").strip()[:40]
        f = filtros.leer(p)
        ahora = timezone.now()
        vehiculos = filtros.vehiculos(f)
        gateways = Gateway.objects.filter(vehiculo__in=vehiculos).select_related("vehiculo__cliente")
        nodos = Nodo.objects.filter(gateway__vehiculo__in=vehiculos).select_related("gateway__vehiculo__cliente")
        if q:
            gateways = gateways.filter(Q(identificador__icontains=q) | Q(vehiculo__identificador__icontains=q))
            nodos = nodos.filter(Q(gateway__identificador__icontains=q) | Q(gateway__vehiculo__identificador__icontains=q))
        condiciones = {
            "clave_vieja": Q(clave_actualizada__isnull=True) | Q(clave_actualizada__lt=ahora - timedelta(days=90)),
            "sin_contacto": Q(ultimo_contacto__isnull=True) | Q(ultimo_contacto__lt=ahora - timedelta(hours=24)),
            "revocados": Q(activo=False),
        }
        if estado:
            gateways, nodos = gateways.filter(condiciones[estado]), nodos.filter(condiciones[estado])
        filas = ([{"tipo": "gateway", "d": g, "vehiculo": g.vehiculo, "nombre": g.identificador, "firmware": g.firmware}
                  for g in gateways]
                 + [{"tipo": "nodo", "d": n, "vehiculo": n.gateway.vehiculo, "nombre": f"{n.gateway.identificador} · {n}",
                     "firmware": ""} for n in nodos])
        for fila in filas:
            cambio = fila["d"].clave_actualizada
            fila["dias_clave"] = (ahora - cambio).days if cambio else None
        filas.sort(key=lambda fila: (fila["vehiculo"].identificador, fila["tipo"] != "gateway", fila["nombre"]))
        pagina = Paginator(filas, self.POR_PAGINA).get_page(p.get("page"))
        parametros = urlencode({k: v for k, v in {"estado": estado, "q": q, "operador": f["operador"].pk if f["operador"] else "",
                                                  "region": f["region"], "modelo": f["modelo"]}.items() if v})
        contexto.update(filtros.contexto(f))
        contexto.update(filas=pagina.object_list, page_obj=pagina, paginator=pagina.paginator,
                        is_paginated=pagina.has_other_pages(), parametros=parametros,
                        estado=estado, q=q, opciones_estado=self.FILTROS, hay_filtros=bool(parametros),
                        total=len(filas))
        return contexto
