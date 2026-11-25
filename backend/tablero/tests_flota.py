from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tablero.flota import Conexion, resumen_flota
from tablero.roles import TECNICO, crear_roles
from telemetria.catalogo import cargar_catalogo
from telemetria.flota_demo import AUTOBUSES, cargar_flota_demo
from telemetria.models import Alerta, Cliente, Gateway, Nodo, Severidad, Vehiculo


def autobus(cliente, identificador, *, segundos=None, activo=True):
    vehiculo = Vehiculo.objects.create(identificador=identificador, nombre=identificador, cliente=cliente)
    contacto = timezone.now() - timedelta(seconds=segundos) if segundos is not None else None
    gateway = Gateway(vehiculo=vehiculo, identificador=f"GW-{identificador}", ultimo_contacto=contacto, activo=activo)
    gateway.generar_clave()
    gateway.save()
    nodo = Nodo(gateway=gateway, numero=1, nombre="Nodo")
    nodo.generar_clave()
    nodo.save()
    return vehiculo, gateway, nodo


def alerta(nodo, severidad, clave):
    return Alerta.objects.create(clave_dedup=clave, tipo=Alerta.Tipo.UMBRAL_ALTO, severidad=severidad,
                                 mensaje=f"Alerta {clave}", nodo=nodo)


class ResumenFlotaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Cliente.objects.all().delete()  # sin los clientes de la migración, para contar exacto
        cls.cliente = Cliente.objects.create(nombre_comercial="Flota Uno")
        _, _, cls.nodo_alerta_sin_senal = autobus(cls.cliente, "U-1", segundos=3600)
        autobus(cls.cliente, "U-2", segundos=5)
        _, _, cls.nodo_alerta_en_linea = autobus(cls.cliente, "U-3", segundos=5)
        autobus(cls.cliente, "U-4")
        alerta(cls.nodo_alerta_sin_senal, Severidad.CRITICA, "a1")
        alerta(cls.nodo_alerta_en_linea, Severidad.ADVERTENCIA, "a2")

    def tarjeta(self, nombre="Flota Uno"):
        return next(t for t in resumen_flota()["clientes"] if t["cliente"].nombre_comercial == nombre)

    def test_prioridad_en_linea_con_alertas_primero(self):
        destacados = [a["vehiculo"].identificador for a in self.tarjeta()["destacados"]]
        # U-3 está en línea y con alerta; U-1 tiene alerta crítica pero sin señal.
        self.assertEqual(destacados, ["U-3", "U-1"])

    def test_conteos_del_cliente(self):
        t = self.tarjeta()
        self.assertEqual((t["total"], t["en_linea"], t["con_alertas"], t["criticas"], t["restantes"]), (4, 2, 2, 1, 2))
        self.assertEqual(t["severidad"], Severidad.CRITICA)

    def test_estados_de_conexion(self):
        cliente = Cliente.objects.create(nombre_comercial="Flota Dos")
        autobus(cliente, "D-1", segundos=5, activo=False)
        Vehiculo.objects.create(identificador="D-2", nombre="Sin gateway", cliente=cliente)
        tarjeta = next(t for t in resumen_flota(por_cliente=10)["clientes"] if t["cliente"] == cliente)
        estados = {a["vehiculo"].identificador: a["conexion"] for a in tarjeta["destacados"]}
        self.assertEqual(estados, {"D-1": Conexion.REVOCADO, "D-2": Conexion.SIN_DISPOSITIVO})
        uno = {a["vehiculo"].identificador: a["conexion"] for a in resumen_flota(por_cliente=10)["clientes"][0]["destacados"]}
        self.assertEqual(uno["U-1"], Conexion.SIN_SENAL)
        self.assertEqual(uno["U-2"], Conexion.EN_LINEA)
        self.assertEqual(uno["U-4"], Conexion.SIN_CONECTAR)

    def test_clientes_con_alertas_van_primero_y_vacios_al_final(self):
        Cliente.objects.create(nombre_comercial="AAA Sin autobuses")
        tranquilo = Cliente.objects.create(nombre_comercial="AAB Tranquila")
        autobus(tranquilo, "T-1", segundos=5)
        orden = [t["cliente"].nombre_comercial for t in resumen_flota()["clientes"]]
        self.assertEqual(orden, ["Flota Uno", "AAB Tranquila", "AAA Sin autobuses"])

    def test_cliente_inactivo_no_aparece(self):
        Cliente.objects.filter(pk=self.cliente.pk).update(activo=False)
        self.assertEqual(resumen_flota()["clientes"], [])

    def test_pantalla_muestra_clientes_y_autobuses(self):
        crear_roles()
        usuario = get_user_model().objects.create_user("tec", password="x")
        usuario.groups.add(Group.objects.get(name=TECNICO))
        self.client.force_login(usuario)
        respuesta = self.client.get(reverse("tablero:resumen"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Flota Uno")
        self.assertContains(respuesta, "U-3")
        self.assertContains(respuesta, "y 2 autobuses más")
        self.assertNotContains(respuesta, "U-4")


class FlotaDemoTests(TestCase):
    def test_carga_autobuses_de_ejemplo_y_es_idempotente(self):
        cargar_catalogo()
        self.assertEqual(cargar_flota_demo(), len(AUTOBUSES))
        self.assertEqual(cargar_flota_demo(), 0)
        ado = Cliente.objects.get(nombre_comercial="Mobility ADO")
        self.assertEqual(ado.vehiculos.count(), 3)
        self.assertTrue(Alerta.objects.filter(nota__startswith="Alerta de ejemplo", estado=Alerta.Estado.ABIERTA).exists())
        self.assertEqual(Nodo.objects.filter(gateway__vehiculo__cliente=ado).count(), 3)
