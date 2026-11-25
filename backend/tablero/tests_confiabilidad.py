from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from auditoria.models import EventoSeguridad
from confiabilidad import demo
from confiabilidad.models import Causa, ComponenteInstalado, Falla, TipoComponente
from servicio.models import OrdenServicio
from tablero.roles import ADMINISTRADOR, INGENIERIA, ROLES_RETIRADOS, TECNICO, crear_roles
from tablero.tests_flota import alerta, autobus
from telemetria.catalogo import cargar_catalogo
from telemetria.flota_demo import cargar_flota_demo
from telemetria.models import Alerta, Cliente, Senal, Severidad, Vehiculo

T = EventoSeguridad.Tipo


def usuario(nombre, rol):
    u = get_user_model().objects.create_user(nombre, password="x")
    u.groups.add(Group.objects.get(name=rol))
    return u


class PantallasTests(TestCase):
    """Todas las pantallas nuevas cargan con los datos de ejemplo, y cada rol entra a lo suyo."""

    @classmethod
    def setUpTestData(cls):
        crear_roles()
        cargar_catalogo()
        demo.cargar_catalogo()
        cargar_flota_demo()
        demo.cargar_demo()
        cls.ingeniero = usuario("ing", INGENIERIA)
        cls.tecnico = usuario("tec", TECNICO)
        cls.admin = usuario("adm", ADMINISTRADOR)
        cls.vehiculo = Vehiculo.objects.get(identificador="ADO-1001")
        cls.componente = ComponenteInstalado.objects.activos().filter(vehiculo=cls.vehiculo).first()
        cls.tipo = TipoComponente.objects.get(codigo="guillotina")

    def test_pantallas_de_ingenieria(self):
        self.client.force_login(self.ingeniero)
        urls = [
            reverse("tablero:panorama"), reverse("tablero:panorama") + "?region=GOLFO&dias=90",
            reverse("tablero:confiabilidad"), reverse("tablero:confiabilidad") + "?comparar=fabricante&dias=1095",
            reverse("tablero:confiabilidad") + "?comparar=operador", reverse("tablero:confiabilidad") + "?comparar=modelo",
            reverse("tablero:componente_tipo", args=[self.tipo.pk]), reverse("tablero:calidad"),
            reverse("tablero:autobuses"), reverse("tablero:autobuses") + "?componente=cerca&q=ADO",
            reverse("tablero:ficha", args=[self.vehiculo.pk]),
            *[reverse("tablero:ficha", args=[self.vehiculo.pk]) + f"?pestana={p}" for p in ("componentes", "fallas", "dispositivos")],
            reverse("tablero:monitoreo", args=[self.vehiculo.pk]),
            reverse("tablero:componente", args=[self.componente.pk]),
            reverse("tablero:catalogo"), reverse("tablero:catalogo_nuevo"), reverse("tablero:catalogo_editar", args=[self.tipo.pk]),
            reverse("tablero:senales"), reverse("tablero:senal_editar", args=[Senal.objects.first().pk]),
            reverse("tablero:alertas"), reverse("tablero:alertas") + "?region=GOLFO&subsistema=SAN",
            reverse("tablero:ordenes") + "?estado=por_validar", reverse("tablero:resumen"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_la_guillotina_sale_con_fallas_prematuras(self):
        self.client.force_login(self.ingeniero)
        filas = self.client.get(reverse("tablero:confiabilidad")).context["tipos"]
        guillotina = next(f for f in filas if f["tipo"].codigo == "guillotina")
        self.assertGreater(guillotina["tempranas"], 0)
        self.assertEqual(filas[0]["tipo"].codigo, "guillotina")  # la de más fallas prematuras va primero

    def test_ingenieria_consulta_pero_no_mueve(self):
        self.client.force_login(self.ingeniero)
        for url in (reverse("tablero:dispositivos"), reverse("tablero:orden_nueva")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        ficha = self.client.get(reverse("tablero:ficha", args=[self.vehiculo.pk]) + "?pestana=dispositivos")
        self.assertContains(ficha, "Gateway")  # ve los dispositivos…
        self.assertNotContains(ficha, "Revocar acceso")  # …sin los botones
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("tablero:dispositivos") + "?estado=clave_vieja").status_code, 200)

    def test_tecnico_volvo_no_ve_confiabilidad_ni_dispositivos(self):
        self.client.force_login(self.tecnico)
        for url in (reverse("tablero:confiabilidad"), reverse("tablero:catalogo"), reverse("tablero:dispositivos")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        ficha = self.client.get(reverse("tablero:ficha", args=[self.vehiculo.pk]))
        self.assertNotIn("dispositivos", ficha.context["pestanas"])
        self.assertEqual(self.client.get(reverse("tablero:componente", args=[self.componente.pk])).status_code, 200)

    def test_cada_rol_entra_a_su_pantalla(self):
        casos = [(self.ingeniero, reverse("tablero:panorama")), (self.admin, reverse("tablero:usuarios")),
                 (self.tecnico, reverse("tablero:ordenes") + "?estado=mis")]
        for u, destino in casos:
            with self.subTest(rol=u.username):
                self.client.force_login(u)
                self.assertRedirects(self.client.get(reverse("tablero:inicio")), destino, fetch_redirect_response=False)

    def test_corregir_contador_queda_en_la_bitacora(self):
        self.client.force_login(self.ingeniero)
        url = reverse("tablero:componente_corregir", args=[self.componente.pk])
        self.client.post(url, {"ciclos": 123, "horas": 4.5, "motivo": "pieza reacondicionada"})
        self.componente.refresh_from_db()
        self.assertEqual((self.componente.ciclos, self.componente.horas), (123, 4.5))
        evento = EventoSeguridad.objects.filter(tipo=T.CONTADOR_CORREGIDO).latest("id")
        self.assertEqual(evento.detalle["motivo"], "pieza reacondicionada")
        # Sin motivo no se corrige.
        self.client.post(url, {"ciclos": 1, "horas": 0, "motivo": ""})
        self.componente.refresh_from_db()
        self.assertEqual(self.componente.ciclos, 123)

    def test_tipo_nuevo_se_instala_en_todas_las_unidades_y_queda_en_bitacora(self):
        self.client.force_login(self.ingeniero)
        datos = {"codigo": "compresor_ac", "nombre": "Compresor del A/C", "subsistema": "AC", "unidad_vida": "HORAS",
                 "vida_garantizada": 20000, "limite_prematura": 50, "regla": "VENTILADOR", "medicion": "ESTIMADO",
                 "activo": "on"}
        self.assertRedirects(self.client.post(reverse("tablero:catalogo_nuevo"), datos), reverse("tablero:catalogo"))
        self.assertEqual(ComponenteInstalado.objects.filter(tipo__codigo="compresor_ac").count(), Vehiculo.objects.count())
        self.assertTrue(EventoSeguridad.objects.filter(tipo=T.CATALOGO_MODIFICADO, detalle__tipo="compresor_ac").exists())

    def test_cambiar_umbral_guarda_valor_anterior_y_nuevo(self):
        self.client.force_login(self.ingeniero)
        senal = Senal.objects.get(codigo="ac.temperatura")
        self.client.post(reverse("tablero:senal_editar", args=[senal.pk]),
                         {"rango_min": -20, "rango_max": 60, "umbral_alto": 32, "severidad_alerta": "ADVERTENCIA"})
        senal.refresh_from_db()
        self.assertEqual(senal.umbral_alto, 32)
        evento = EventoSeguridad.objects.filter(tipo=T.CATALOGO_MODIFICADO).latest("id")
        self.assertEqual(evento.detalle["cambios"]["umbral_alto"], ["30.0", "32.0"])


class TecnicoTests(TestCase):
    """Un solo rol de técnico (de Volvo o de convenio): repara; el Administrador valida. Quién hizo qué queda
    registrado con su usuario."""

    @classmethod
    def setUpTestData(cls):
        crear_roles()
        cargar_catalogo()
        demo.cargar_catalogo()
        cliente = Cliente.objects.create(nombre_comercial="Flota")
        cls.vehiculo, _, cls.nodo = autobus(cliente, "S-1", segundos=5)
        cls.tecnico = usuario("taller", TECNICO)
        cls.admin = usuario("adm", ADMINISTRADOR)
        cls.guillotina = ComponenteInstalado.objects.activos().get(vehiculo=cls.vehiculo, tipo__codigo="guillotina")
        cls.guillotina.ciclos = 6000
        cls.guillotina.save()
        cls.orden = OrdenServicio.objects.create(vehiculo=cls.vehiculo, titulo="Fuga", responsable=cls.tecnico,
                                                 componente=cls.guillotina, etapa=OrdenServicio.Etapa.REPARACION)
        cls.causa = Causa.objects.get(codigo="SAN-SEL")

    def test_el_tecnico_ve_la_flota_y_entra_a_sus_pendientes(self):
        self.client.force_login(self.tecnico)
        self.assertRedirects(self.client.get(reverse("tablero:inicio")), reverse("tablero:ordenes") + "?estado=mis",
                             fetch_redirect_response=False)
        for url in (reverse("tablero:autobuses"), reverse("tablero:alertas"), reverse("tablero:ficha", args=[self.vehiculo.pk]),
                    reverse("tablero:api_estado") + f"?nodo={self.nodo.pk}"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_la_falla_cuenta_cuando_el_administrador_cierra_la_orden(self):
        url = reverse("tablero:orden_accion", args=[self.orden.pk])
        self.client.force_login(self.tecnico)
        self.client.post(url, {"accion": "a_validacion", "texto": "Se cambió la guillotina", "causa": self.causa.pk,
                               "reemplazo": "1", "serie_nueva": "GV-1"})
        falla = Falla.objects.get(orden=self.orden)
        self.assertTrue(falla.prematura)  # 6000 de 30 000 = 20 %
        self.assertEqual(falla.registrada_por, self.tecnico)  # se sabe qué técnico fue por su usuario
        self.assertEqual(self.orden.avances.filter(autor=self.tecnico).count(), 1)
        self.assertFalse(falla.confirmada)
        # El técnico no valida su propio trabajo.
        self.assertEqual(self.client.post(url, {"accion": "validar", "texto": "Listo"}).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(url, {"accion": "validar", "texto": "Revisado en sitio"})
        falla.refresh_from_db()
        self.assertTrue(falla.confirmada)

    def test_los_usuarios_del_rol_anterior_pasan_a_tecnico(self):
        viejo = Group.objects.create(name=ROLES_RETIRADOS[0])
        u = get_user_model().objects.create_user("de-convenio", password="x")
        u.groups.add(viejo)
        crear_roles()
        self.assertFalse(Group.objects.filter(name=ROLES_RETIRADOS[0]).exists())
        self.assertEqual(list(u.groups.values_list("name", flat=True)), [TECNICO])


class DiagnosticoEnLaOrdenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_roles()
        cargar_catalogo()
        demo.cargar_catalogo()
        cls.ingeniero = usuario("tec", TECNICO)  # quien crea y trabaja la orden
        cliente = Cliente.objects.create(nombre_comercial="Flota")
        cls.vehiculo, _, cls.nodo = autobus(cliente, "O-1", segundos=5)
        from telemetria.alertas import GestorAlertas
        from telemetria.models import Medicion

        GestorAlertas().evaluar(cls.nodo, Senal.objects.get(codigo="san.fuga"), 1, Medicion.Calidad.OK)
        cls.alerta = Alerta.objects.get()

    def test_la_orden_desde_la_alerta_trae_componente_y_diagnostico(self):
        self.client.force_login(self.ingeniero)
        form = self.client.get(reverse("tablero:orden_nueva"), {"alerta": self.alerta.pk}).context["form"]
        self.assertEqual(form.initial["componente"].tipo.codigo, "guillotina")
        self.assertIn("Diagnóstico del sistema", form.initial["diagnostico"])

    def test_confirmar_el_diagnostico_lo_marca_confirmado_en_la_alerta(self):
        self.client.force_login(self.ingeniero)
        orden = OrdenServicio.objects.create(vehiculo=self.vehiculo, titulo="Fuga", alerta=self.alerta)
        self.client.post(reverse("tablero:orden_accion", args=[orden.pk]), {"accion": "diagnostico", "respuesta": "confirmar"})
        orden.refresh_from_db()
        self.alerta.refresh_from_db()
        self.assertTrue(orden.diagnostico_confirmado)
        self.assertEqual(self.alerta.certeza, Alerta.Certeza.CONFIRMADO)
        # Rechazar exige la causa real.
        otra = OrdenServicio.objects.create(vehiculo=self.vehiculo, titulo="Fuga 2", alerta=self.alerta)
        self.client.post(reverse("tablero:orden_accion", args=[otra.pk]), {"accion": "diagnostico", "respuesta": "rechazar"})
        otra.refresh_from_db()
        self.assertIsNone(otra.diagnostico_confirmado)
