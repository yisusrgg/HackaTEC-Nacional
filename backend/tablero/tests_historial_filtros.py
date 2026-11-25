from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tablero.roles import INGENIERIA, crear_roles
from tablero.tests_flota import autobus
from telemetria.catalogo import cargar_catalogo
from telemetria.models import Cliente, Medicion, Nodo, Senal


def lectura(nodo, codigo, valor=1.0):
    return Medicion.objects.create(nodo=nodo, senal=Senal.objects.get(codigo=codigo), valor=valor, contador=0,
                                   ts=timezone.now())


class HistorialFiltrosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_roles()
        cargar_catalogo()
        Cliente.objects.all().delete()
        cls.uno = Cliente.objects.create(nombre_comercial="Flota Uno")
        cls.dos = Cliente.objects.create(nombre_comercial="Flota Dos")
        cls.bus_u1, cls.gw_u1, cls.nodo_u1 = autobus(cls.uno, "U-1", segundos=5)
        cls.nodo_u1_b = Nodo(gateway=cls.gw_u1, numero=2, nombre="Nodo trasero")
        cls.nodo_u1_b.generar_clave()
        cls.nodo_u1_b.save()
        cls.bus_d1, _, cls.nodo_d1 = autobus(cls.dos, "D-1", segundos=5)
        lectura(cls.nodo_u1, "ac.temperatura", 24)
        lectura(cls.nodo_u1_b, "san.tanque_nivel", 50)
        lectura(cls.nodo_d1, "ac.temperatura", 26)
        usuario = get_user_model().objects.create_user("ing", password="x")
        usuario.groups.add(Group.objects.get(name=INGENIERIA))
        cls.usuario = usuario

    def setUp(self):
        self.client.force_login(self.usuario)

    def consultar(self, **filtros):
        respuesta = self.client.get(reverse("tablero:historial"), filtros)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta

    def lecturas(self, **filtros):
        return sorted((m.nodo.gateway.vehiculo.identificador, m.senal.codigo)
                      for m in self.consultar(**filtros).context["lecturas"])

    def test_por_cliente(self):
        self.assertEqual(self.lecturas(cliente=self.dos.pk), [("D-1", "ac.temperatura")])

    def test_por_unidad_y_nodo(self):
        self.assertEqual(len(self.lecturas(autobus=self.bus_u1.pk)), 2)
        self.assertEqual(self.lecturas(autobus=self.bus_u1.pk, nodo=self.nodo_u1_b.pk), [("U-1", "san.tanque_nivel")])

    def test_por_subsistema(self):
        self.assertEqual(self.lecturas(subsistema="SAN"), [("U-1", "san.tanque_nivel")])
        self.assertEqual(len(self.lecturas(subsistema="AC")), 2)

    def test_combinaciones_incoherentes_se_descartan(self):
        # Unidad de otro cliente → se ignora la unidad; nodo de otra unidad → se ignora el nodo.
        respuesta = self.consultar(cliente=self.dos.pk, autobus=self.bus_u1.pk)
        self.assertIsNone(respuesta.context["f"]["autobus"])
        respuesta = self.consultar(autobus=self.bus_d1.pk, nodo=self.nodo_u1.pk)
        self.assertIsNone(respuesta.context["f"]["nodo"])
        # Señal de otro subsistema → se ignora la señal.
        respuesta = self.consultar(subsistema="SAN", senal="ac.temperatura")
        self.assertIsNone(respuesta.context["f"]["senal"])

    def test_enlace_anterior_solo_con_nodo_sigue_funcionando(self):
        respuesta = self.consultar(nodo=self.nodo_d1.pk)
        self.assertEqual(respuesta.context["f"]["autobus"], self.bus_d1)
        self.assertEqual(respuesta.context["f"]["nodo"], self.nodo_d1)

    def test_grafica_con_unidad_de_un_solo_nodo(self):
        self.assertIsNotNone(self.consultar(autobus=self.bus_d1.pk, senal="ac.temperatura").context["grafica"])
        # U-1 tiene dos nodos: hace falta elegir uno.
        self.assertIsNone(self.consultar(autobus=self.bus_u1.pk, senal="ac.temperatura").context["grafica"])
        self.assertIsNotNone(self.consultar(autobus=self.bus_u1.pk, nodo=self.nodo_u1.pk,
                                            senal="ac.temperatura").context["grafica"])

    def test_selector_de_nodo_solo_con_varios_nodos(self):
        self.assertNotContains(self.consultar(autobus=self.bus_d1.pk), 'id="filtro-nodo"')
        self.assertContains(self.consultar(autobus=self.bus_u1.pk), 'id="filtro-nodo"')

    def test_exportar_respeta_filtros_nuevos(self):
        respuesta = self.client.get(reverse("tablero:historial_exportar"),
                                    {"cliente": self.dos.pk, "subsistema": "AC", "motivo": "prueba"})
        contenido = b"".join(respuesta.streaming_content).decode("utf-8")
        filas = [linea for linea in contenido.splitlines()[1:] if linea]
        self.assertEqual(len(filas), 1)
        self.assertIn("D-1", filas[0])
