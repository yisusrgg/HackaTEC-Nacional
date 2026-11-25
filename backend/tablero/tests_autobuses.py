from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from tablero.roles import ADMINISTRADOR, TECNICO, crear_roles
from tablero.tests_flota import alerta, autobus
from telemetria.catalogo import cargar_catalogo
from telemetria.models import Cliente, Severidad, Vehiculo


class AutobusesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_roles()
        cargar_catalogo()
        Cliente.objects.all().delete()
        cls.uno = Cliente.objects.create(nombre_comercial="Flota Uno")
        cls.dos = Cliente.objects.create(nombre_comercial="Flota Dos")
        cls.bus_critico, _, nodo_critico = autobus(cls.uno, "U-1", segundos=5)
        cls.bus_aviso, _, nodo_aviso = autobus(cls.uno, "U-2", segundos=3600)
        cls.bus_tranquilo, _, cls.nodo_tranquilo = autobus(cls.dos, "D-1", segundos=5)
        cls.nodo_critico = nodo_critico
        alerta(nodo_critico, Severidad.CRITICA, "c1")
        alerta(nodo_aviso, Severidad.ADVERTENCIA, "a1")
        Usuario = get_user_model()
        cls.tecnico = Usuario.objects.create_user("tec", password="x")
        cls.tecnico.groups.add(Group.objects.get(name=TECNICO))
        cls.admin = Usuario.objects.create_user("adm", password="x")
        cls.admin.groups.add(Group.objects.get(name=ADMINISTRADOR))

    def setUp(self):
        self.client.force_login(self.tecnico)

    def identificadores(self, **filtros):
        respuesta = self.client.get(reverse("tablero:autobuses"), filtros)
        self.assertEqual(respuesta.status_code, 200)
        return [a["vehiculo"].identificador for a in respuesta.context["autobuses"]]

    def test_sin_filtros_muestra_todos(self):
        self.assertEqual(self.identificadores(), ["D-1", "U-1", "U-2"])

    def test_filtro_por_cliente(self):
        self.assertEqual(self.identificadores(cliente=self.uno.pk), ["U-1", "U-2"])

    def test_filtros_de_alertas(self):
        self.assertEqual(self.identificadores(alertas="con"), ["U-1", "U-2"])
        self.assertEqual(self.identificadores(alertas="criticas"), ["U-1"])
        self.assertEqual(self.identificadores(alertas="sin"), ["D-1"])

    def test_filtro_de_conexion_y_busqueda(self):
        self.assertEqual(self.identificadores(conexion="en_linea"), ["D-1", "U-1"])
        self.assertEqual(self.identificadores(conexion="fuera"), ["U-2"])
        self.assertEqual(self.identificadores(q="u-2"), ["U-2"])

    def test_filtros_combinados_y_valores_invalidos(self):
        self.assertEqual(self.identificadores(cliente=self.uno.pk, alertas="criticas", conexion="en_linea"), ["U-1"])
        self.assertEqual(self.identificadores(cliente="abc", alertas="xyz"), ["D-1", "U-1", "U-2"])

    def test_cada_tarjeta_tiene_boton_de_monitoreo(self):
        respuesta = self.client.get(reverse("tablero:autobuses"))
        self.assertContains(respuesta, reverse("tablero:monitoreo", args=[self.bus_critico.pk]))
        self.assertContains(respuesta, ">Monitoreo</a>", count=3)

    def test_monitoreo_es_del_autobus_indicado(self):
        respuesta = self.client.get(reverse("tablero:monitoreo", args=[self.bus_tranquilo.pk]))
        self.assertEqual(respuesta.context["nodo"], self.nodo_tranquilo)
        self.assertContains(respuesta, f"?nodo={self.nodo_tranquilo.pk}")
        self.assertEqual(list(respuesta.context["alertas"]), [])

    def test_monitoreo_ignora_nodo_de_otro_autobus(self):
        url = reverse("tablero:monitoreo", args=[self.bus_tranquilo.pk])
        respuesta = self.client.get(url, {"nodo": self.nodo_critico.pk})
        self.assertEqual(respuesta.context["nodo"], self.nodo_tranquilo)

    def test_monitoreo_de_autobus_inexistente_da_404(self):
        self.assertEqual(self.client.get(reverse("tablero:monitoreo", args=[999999])).status_code, 404)

    def test_monitoreo_de_autobus_sin_nodos(self):
        solo = Vehiculo.objects.create(identificador="S-1", nombre="Sin equipo", cliente=self.dos)
        respuesta = self.client.get(reverse("tablero:monitoreo", args=[solo.pk]))
        self.assertContains(respuesta, "aún no tiene nodos de sensores")

    def test_api_estado_cuenta_solo_alertas_del_autobus(self):
        url = reverse("tablero:api_estado")
        self.assertEqual(self.client.get(url, {"nodo": self.nodo_critico.pk}).json()["alertas_activas"], 1)
        self.assertEqual(self.client.get(url, {"nodo": self.nodo_tranquilo.pk}).json()["alertas_activas"], 0)

    def test_ver_todas_del_monitoreo_lleva_a_alertas_de_esa_unidad(self):
        respuesta = self.client.get(reverse("tablero:monitoreo", args=[self.bus_critico.pk]))
        self.assertContains(respuesta, f'{reverse("tablero:alertas")}?autobus={self.bus_critico.pk}')

    def test_alertas_filtradas_por_autobus(self):
        url = reverse("tablero:alertas")
        respuesta = self.client.get(url, {"autobus": self.bus_critico.pk})
        self.assertEqual([a.clave_dedup for a in respuesta.context["alertas"]], ["c1"])
        self.assertContains(respuesta, f"?estado=todas&amp;autobus={self.bus_critico.pk}")  # las pestañas conservan la unidad
        self.assertEqual(len(self.client.get(url, {"autobus": self.bus_tranquilo.pk}).context["alertas"]), 0)
        self.assertEqual(len(self.client.get(url, {"autobus": "abc"}).context["alertas"]), 2)  # valor inválido: todas

    def claves(self, **filtros):
        respuesta = self.client.get(reverse("tablero:alertas"), filtros)
        self.assertEqual(respuesta.status_code, 200)
        return sorted(a.clave_dedup for a in respuesta.context["alertas"])

    def test_alertas_filtradas_por_cliente_y_severidad(self):
        self.assertEqual(self.claves(cliente=self.uno.pk), ["a1", "c1"])
        self.assertEqual(self.claves(cliente=self.dos.pk), [])
        self.assertEqual(self.claves(severidad="CRITICA"), ["c1"])
        self.assertEqual(self.claves(severidad="ADVERTENCIA"), ["a1"])
        self.assertEqual(self.claves(cliente=self.uno.pk, severidad="xyz"), ["a1", "c1"])

    def test_alertas_filtradas_por_fecha(self):
        from datetime import timedelta
        from django.utils import timezone
        from telemetria.models import Alerta
        Alerta.objects.filter(clave_dedup="a1").update(creada=timezone.now() - timedelta(days=10))
        hoy = timezone.localdate()
        self.assertEqual(self.claves(desde=hoy.isoformat()), ["c1"])
        self.assertEqual(self.claves(hasta=(hoy - timedelta(days=5)).isoformat()), ["a1"])
        # Fechas al revés se acomodan solas; texto inválido se ignora.
        self.assertEqual(self.claves(desde=hoy.isoformat(), hasta=(hoy - timedelta(days=20)).isoformat()), ["a1", "c1"])
        self.assertEqual(self.claves(desde="ayer"), ["a1", "c1"])

    def test_unidad_de_otro_cliente_se_ignora(self):
        self.assertEqual(self.claves(cliente=self.dos.pk, autobus=self.bus_critico.pk), [])
        respuesta = self.client.get(reverse("tablero:alertas"), {"cliente": self.dos.pk, "autobus": self.bus_critico.pk})
        self.assertIsNone(respuesta.context["f"]["autobus"])
        self.assertEqual([v.identificador for v in respuesta.context["autobuses"]], ["D-1"])

    def test_revocar_regresa_a_la_misma_lista_filtrada(self):
        self.client.force_login(self.admin)
        siguiente = reverse("tablero:autobuses") + f"?cliente={self.dos.pk}"
        respuesta = self.client.post(reverse("tablero:cambiar_estado", args=["nodo", self.nodo_tranquilo.pk]),
                                     {"siguiente": siguiente})
        self.assertRedirects(respuesta, siguiente)

    def test_revocar_no_redirige_fuera_del_sitio(self):
        self.client.force_login(self.admin)
        respuesta = self.client.post(reverse("tablero:cambiar_estado", args=["nodo", self.nodo_tranquilo.pk]),
                                     {"siguiente": "https://ejemplo.com/"})
        self.assertRedirects(respuesta, reverse("tablero:autobuses"))
