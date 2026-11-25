from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from confiabilidad import demo, diagnostico
from confiabilidad.conteo import contar
from confiabilidad.models import Causa, ComponenteInstalado, EstadoConteo, Falla, Regla, TipoComponente
from confiabilidad.servicios import confirmar_falla, registrar_reparacion
from servicio.models import OrdenServicio
from tablero.tests_flota import autobus
from telemetria.alertas import GestorAlertas
from telemetria.catalogo import cargar_catalogo
from telemetria.models import Alerta, Cliente, Medicion, Senal, Vehiculo


class ConteoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cargar_catalogo()
        demo.cargar_catalogo()
        cls.cliente = Cliente.objects.create(nombre_comercial="Flota de prueba")

    def setUp(self):
        self.vehiculo, _, self.nodo = autobus(self.cliente, "C-1", segundos=5)
        self.t0 = timezone.now() - timedelta(hours=1)

    def lecturas(self, codigo, valores, *, cada_s=60, desde=None, calidad=Medicion.Calidad.OK):
        senal = Senal.objects.get(codigo=codigo)
        inicio = desde or self.t0
        return [Medicion(nodo=self.nodo, senal=senal, valor=v, calidad=calidad, contador=i, ts=inicio + timedelta(seconds=i * cada_s))
                for i, v in enumerate(valores)]

    def componente(self, codigo):
        return ComponenteInstalado.objects.activos().get(vehiculo=self.vehiculo, tipo__codigo=codigo)

    def test_unidad_nueva_recibe_un_componente_de_cada_tipo(self):
        self.assertEqual(ComponenteInstalado.objects.filter(vehiculo=self.vehiculo).count(),
                         TipoComponente.objects.filter(activo=True).count())

    def test_arranques_y_horas_del_ventilador_entre_lotes(self):
        # Lote 1: apagado, arranca y sigue encendido 2 min. Lote 2: sigue encendido, se apaga y vuelve a arrancar.
        contar(self.lecturas("ac.ventilador_rpm", [0, 1200, 1250, 1300]))
        contar(self.lecturas("ac.ventilador_rpm", [1280, 100, 900], desde=self.t0 + timedelta(minutes=4)))
        ventilador = self.componente("ventilador_ac")
        self.assertEqual(ventilador.ciclos, 2)
        self.assertAlmostEqual(ventilador.horas, 3 / 60, places=3)  # encendido del minuto 1 al 4; el 6 solo arranca

    def test_hueco_largo_no_suma_horas_y_lecturas_viejas_se_ignoran(self):
        contar(self.lecturas("ac.ventilador_rpm", [1000]))
        contar(self.lecturas("ac.ventilador_rpm", [1000], desde=self.t0 + timedelta(hours=2)))
        contar(self.lecturas("ac.ventilador_rpm", [0, 1000], desde=self.t0 - timedelta(hours=3)))  # fuera de orden
        ventilador = self.componente("ventilador_ac")
        self.assertEqual((ventilador.ciclos, ventilador.horas), (1, 0))

    def test_usos_del_sanitario_suman_diferencias_y_soportan_reinicio_del_nodo(self):
        contar(self.lecturas("san.usos", [10, 12, 15]))  # la primera lectura solo fija la referencia
        contar(self.lecturas("san.usos", [2, 3], desde=self.t0 + timedelta(minutes=10)))  # el nodo se reinició
        self.assertEqual(self.componente("guillotina").ciclos, 5 + 3)
        self.assertEqual(self.componente("bomba_descarga").ciclos, 8)

    def test_llenados_del_tanque(self):
        contar(self.lecturas("san.tanque_nivel", [90, 60, 35, 50, 85, 90, 30, 82]))
        self.assertEqual(self.componente("valvula_llenado").ciclos, 2)

    def test_lecturas_invalidas_no_cuentan(self):
        contar(self.lecturas("ac.ventilador_rpm", [0, 1500], calidad=Medicion.Calidad.FUERA_RANGO))
        self.assertEqual(self.componente("ventilador_ac").ciclos, 0)
        self.assertFalse(EstadoConteo.objects.exists())

    def test_las_piezas_retiradas_ya_no_cuentan(self):
        guillotina = self.componente("guillotina")
        guillotina.retirado_en = timezone.now()
        guillotina.save()
        contar(self.lecturas("san.usos", [1, 5]))
        guillotina.refresh_from_db()
        self.assertEqual(guillotina.ciclos, 0)


class FallasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cargar_catalogo()
        demo.cargar_catalogo()
        cls.cliente = Cliente.objects.create(nombre_comercial="Flota de prueba")
        cls.causa = Causa.objects.get(codigo="SAN-SEL")

    def setUp(self):
        self.vehiculo, _, self.nodo = autobus(self.cliente, "F-1", segundos=5)
        self.guillotina = ComponenteInstalado.objects.activos().get(vehiculo=self.vehiculo, tipo__codigo="guillotina")
        self.guillotina.ciclos = 9000  # 30 % de 30 000: prematura (límite 50 %)
        self.guillotina.save()
        self.orden = OrdenServicio.objects.create(vehiculo=self.vehiculo, titulo="Fuga", componente=self.guillotina)

    def test_cambiar_la_pieza_crea_la_falla_y_la_nueva_empieza_en_cero(self):
        falla = registrar_reparacion(self.orden, usuario=None, causa=self.causa, reemplazar=True, serie_nueva="GV-NUEVA")
        self.assertEqual((falla.uso_al_fallar, falla.porcentaje_garantia), (9000, 30.0))
        self.assertTrue(falla.prematura)
        self.assertFalse(falla.confirmada)
        self.guillotina.refresh_from_db()
        self.assertIsNotNone(self.guillotina.retirado_en)
        nueva = ComponenteInstalado.objects.activos().get(vehiculo=self.vehiculo, tipo__codigo="guillotina")
        self.assertEqual((nueva.numero_serie, nueva.ciclos), ("GV-NUEVA", 0))
        self.assertEqual(falla.reemplazo, nueva)

    def test_registrar_otra_vez_no_duplica_la_falla(self):
        registrar_reparacion(self.orden, usuario=None, causa=self.causa, reemplazar=True)
        otra = Causa.objects.get(codigo="SAN-SAR")
        registrar_reparacion(self.orden, usuario=None, causa=otra, reemplazar=True)
        self.assertEqual(Falla.objects.count(), 1)
        self.assertEqual(Falla.objects.get().causa, otra)

    def test_sin_cambio_de_pieza_no_hay_falla(self):
        self.assertIsNone(registrar_reparacion(self.orden, usuario=None, causa=self.causa, reemplazar=False))
        self.assertEqual(self.orden.causa, self.causa)

    def test_la_falla_cuenta_al_cerrar_y_si_requiere_aprobacion_hasta_aprobarla(self):
        registrar_reparacion(self.orden, usuario=None, causa=self.causa, reemplazar=True)
        self.assertEqual(confirmar_falla(self.orden), 0)  # la orden sigue abierta
        self.orden.etapa = OrdenServicio.Etapa.CERRADA
        self.orden.aprobacion = OrdenServicio.Aprobacion.PENDIENTE
        self.assertEqual(confirmar_falla(self.orden), 0)
        self.orden.aprobacion = OrdenServicio.Aprobacion.APROBADA
        self.assertEqual(confirmar_falla(self.orden), 1)

    def test_clasificacion_de_la_falla(self):
        self.assertEqual(Falla.clasificar(49.9, 50), Falla.Momento.TEMPRANA)
        self.assertEqual(Falla.clasificar(80, 50), Falla.Momento.NORMAL)
        self.assertEqual(Falla.clasificar(100.1, 50), Falla.Momento.TARDIA)

    def test_vida_calculada_en_la_base(self):
        c = ComponenteInstalado.objects.con_vida().get(pk=self.guillotina.pk)
        self.assertAlmostEqual(c.vida_pct, 30.0)
        self.assertEqual(self.guillotina.nivel, "ok")


class DiagnosticoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cargar_catalogo()
        cls.cliente = Cliente.objects.create(nombre_comercial="Flota de prueba")

    def test_la_alerta_nueva_trae_diagnostico_y_certeza(self):
        _, _, nodo = autobus(self.cliente, "D-1", segundos=5)
        gestor = GestorAlertas()
        rpm = Senal.objects.get(codigo="ac.ventilador_rpm")
        gestor.evaluar(nodo, rpm, 50, Medicion.Calidad.OK)
        alerta = Alerta.objects.get(senal=rpm)
        self.assertEqual(alerta.certeza, Alerta.Certeza.PROBABLE)
        self.assertIn("Ventilador", alerta.diagnostico)
        self.assertEqual(diagnostico.componente_sugerido(alerta), "ventilador_ac")

    def test_una_alerta_explica_a_la_otra(self):
        _, _, nodo = autobus(self.cliente, "D-2", segundos=5)
        gestor = GestorAlertas()
        gestor.evaluar(nodo, Senal.objects.get(codigo="elec.voltaje"), 3.9, Medicion.Calidad.OK)
        gestor.evaluar(nodo, Senal.objects.get(codigo="ac.ventilador_rpm"), 50, Medicion.Calidad.OK)
        alerta = Alerta.objects.get(senal__codigo="ac.ventilador_rpm")
        self.assertIn("voltaje", alerta.diagnostico)

    def test_falla_de_sensor_no_culpa_al_autobus(self):
        _, _, nodo = autobus(self.cliente, "D-3", segundos=5)
        GestorAlertas().evaluar(nodo, Senal.objects.get(codigo="ac.temperatura"), None, Medicion.Calidad.SIN_DATO)
        self.assertIn("sensor", Alerta.objects.get().diagnostico)


class DemoTests(TestCase):
    def test_demo_es_idempotente_y_cuenta_la_historia(self):
        from telemetria.flota_demo import cargar_flota_demo

        cargar_catalogo()
        demo.cargar_catalogo()
        cargar_flota_demo()
        primera = demo.cargar_demo()
        self.assertGreater(primera["fallas"], 20)
        segunda = demo.cargar_demo()
        self.assertEqual((segunda["fallas"], segunda["instalados"]), (0, 0))
        self.assertEqual(demo.cargar_catalogo(), (0, 0))
        # La guillotina falla antes de tiempo en las regiones de clima cálido húmedo.
        prematuras = Falla.objects.filter(componente__tipo__codigo="guillotina", momento=Falla.Momento.TEMPRANA)
        self.assertTrue(prematuras.exists())
        self.assertTrue(all(f.componente.vehiculo.region in demo.CALIDO_HUMEDO for f in prematuras))
        # Cada unidad tiene un componente activo de cada tipo y hay piezas cerca de su límite.
        for v in Vehiculo.objects.all():
            self.assertEqual(ComponenteInstalado.objects.activos().filter(vehiculo=v).count(), len(demo.TIPOS))
        self.assertTrue(ComponenteInstalado.objects.activos().con_vida().filter(vida_pct__gte=80).exists())
        self.assertEqual(TipoComponente.objects.filter(regla=Regla.USOS_SANITARIO).count(), 2)
