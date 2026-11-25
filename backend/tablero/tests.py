from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from auditoria.models import EventoSeguridad
from tablero.roles import ADMINISTRADOR, INGENIERIA, TECNICO, crear_roles
from telemetria.catalogo import cargar_catalogo
from telemetria.models import Alerta, Cliente, Gateway, Nodo, Vehiculo

T = EventoSeguridad.Tipo
CLAVE = "Prueba-Segura-2026"


class TableroTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        crear_roles()
        cargar_catalogo()
        cliente = Cliente.objects.create(nombre_comercial="Cliente de prueba")
        vehiculo = Vehiculo.objects.create(identificador="BUS-T", nombre="Prueba", cliente=cliente)
        cls.gateway = Gateway(vehiculo=vehiculo, identificador="GW-T")
        cls.gateway.generar_clave()
        cls.gateway.save()
        cls.nodo = Nodo(gateway=cls.gateway, numero=1, nombre="Nodo", ultimo_contador=50)
        cls.nodo.generar_clave()
        cls.nodo.save()
        Usuario = get_user_model()
        cls.usuarios = {}
        for nombre, rol in [("admin", ADMINISTRADOR), ("ingeniero", INGENIERIA), ("tecnico", TECNICO)]:
            usuario = Usuario.objects.create_user(nombre, password=CLAVE)
            usuario.groups.add(Group.objects.get(name=rol))
            cls.usuarios[nombre] = usuario

    def setUp(self):
        cache.clear()

    def entrar(self, nombre):
        self.client.force_login(self.usuarios[nombre])

    def test_anonimo_va_al_login(self):
        respuesta = self.client.get(reverse("tablero:resumen"))
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(reverse("cuentas:login"), respuesta["Location"])

    def test_monitoreo_con_rol_y_cabeceras_de_seguridad(self):
        self.entrar("tecnico")
        respuesta = self.client.get(reverse("tablero:monitoreo"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'id="config-resumen"')
        self.assertIn("script-src 'self'", respuesta.headers["Content-Security-Policy"])
        self.assertEqual(respuesta.headers["X-Frame-Options"], "DENY")

    def test_paginas_principales_cargan(self):
        self.entrar("admin")
        for url in [reverse("tablero:resumen"), reverse("tablero:monitoreo"), reverse("tablero:alertas"),
                    reverse("tablero:dispositivos"),
                    reverse("tablero:seguridad"), reverse("tablero:senal", args=["ac.temperatura"])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_tecnico_no_ve_la_bitacora_y_queda_registrado(self):
        self.entrar("tecnico")
        self.assertEqual(self.client.get(reverse("tablero:seguridad")).status_code, 403)
        self.assertTrue(EventoSeguridad.objects.filter(tipo=T.ACCESO_DENEGADO, actor="tecnico").exists())

    def test_tecnico_no_exporta(self):
        self.entrar("tecnico")
        self.assertEqual(self.client.get(reverse("tablero:exportar")).status_code, 403)

    def test_ingeniero_exporta_csv(self):
        self.entrar("ingeniero")
        respuesta = self.client.get(reverse("tablero:exportar"), {"senal": "ac.temperatura"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta["Content-Type"].startswith("text/csv"))
        self.assertIn("fecha_utc", b"".join(respuesta.streaming_content).decode())
        self.assertTrue(EventoSeguridad.objects.filter(tipo=T.EXPORTACION, actor="ingeniero").exists())

    def test_admin_ve_la_bitacora_integra(self):
        self.entrar("admin")
        self.assertContains(self.client.get(reverse("tablero:seguridad")), "Bitácora íntegra")

    def test_rotar_clave_de_nodo(self):
        self.entrar("admin")
        respuesta = self.client.post(reverse("tablero:rotar_clave", args=["nodo", self.nodo.pk]))
        self.assertEqual(respuesta.status_code, 200)
        self.nodo.refresh_from_db()
        clave = self.nodo.obtener_clave().hex().upper()
        self.assertContains(respuesta, f"clave {clave}")
        self.assertIn("no-store", respuesta["Cache-Control"])
        self.assertIsNone(self.nodo.ultimo_contador)
        evento = EventoSeguridad.objects.get(tipo=T.CLAVE_ROTADA)
        self.assertNotIn(clave, str(evento.detalle))  # la clave nunca va a la bitácora

    def test_rotar_clave_del_gateway_cambia_la_epoca(self):
        self.entrar("admin")
        self.client.post(reverse("tablero:rotar_clave", args=["gateway", self.gateway.pk]))
        self.gateway.refresh_from_db()
        self.assertEqual(self.gateway.epoca, 2)
        self.assertIsNone(self.gateway.ultimo_lote)

    def test_ingeniero_no_rota_claves(self):
        self.entrar("ingeniero")
        respuesta = self.client.post(reverse("tablero:rotar_clave", args=["nodo", self.nodo.pk]))
        self.assertEqual(respuesta.status_code, 403)

    def test_revocar_nodo(self):
        self.entrar("admin")
        self.client.post(reverse("tablero:cambiar_estado", args=["nodo", self.nodo.pk]))
        self.nodo.refresh_from_db()
        self.assertFalse(self.nodo.activo)
        self.assertTrue(EventoSeguridad.objects.filter(tipo=T.DISPOSITIVO_ESTADO).exists())

    def test_tecnico_atiende_alerta(self):
        alerta = Alerta.objects.create(clave_dedup="x", tipo=Alerta.Tipo.UMBRAL_ALTO, mensaje="Temperatura alta", nodo=self.nodo)
        self.entrar("tecnico")
        self.client.post(reverse("tablero:atender_alerta", args=[alerta.pk]), {"accion": "atender", "nota": "Revisando"})
        alerta.refresh_from_db()
        self.assertEqual(alerta.estado, Alerta.Estado.ATENDIDA)
        self.assertEqual(alerta.atendida_por, self.usuarios["tecnico"])
        self.assertTrue(EventoSeguridad.objects.filter(tipo=T.ALERTA_ATENDIDA).exists())

    def test_api_estado(self):
        self.entrar("ingeniero")
        datos = self.client.get(reverse("tablero:api_estado")).json()
        self.assertEqual(datos["nodo"]["numero"], 1)
        self.assertTrue(any(s["codigo"] == "ac.temperatura" and s["nivel"] == "sin_datos" for s in datos["senales"]))

    def test_api_serie(self):
        self.entrar("ingeniero")
        respuesta = self.client.get(reverse("tablero:api_serie", args=["ac.temperatura"]), {"minutos": 60})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["puntos"], [])

    def test_api_requiere_sesion(self):
        self.assertIn(self.client.get(reverse("tablero:api_estado")).status_code, (401, 403))

    def test_login_correcto_queda_registrado(self):
        respuesta = self.client.post(reverse("cuentas:login"), {"username": "admin", "password": CLAVE})
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(EventoSeguridad.objects.filter(tipo=T.LOGIN_OK, actor="admin").exists())

    def test_bloqueo_tras_intentos_fallidos(self):
        url = reverse("cuentas:login")
        for _ in range(5):
            self.client.post(url, {"username": "admin", "password": "incorrecta"})
        respuesta = self.client.post(url, {"username": "admin", "password": CLAVE})
        self.assertContains(respuesta, "Demasiados intentos")
        self.assertEqual(EventoSeguridad.objects.filter(tipo=T.LOGIN_FALLIDO).count(), 5)
        self.assertTrue(EventoSeguridad.objects.filter(tipo=T.LOGIN_BLOQUEADO).exists())
