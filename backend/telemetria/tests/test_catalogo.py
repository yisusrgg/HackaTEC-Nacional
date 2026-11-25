from django.test import TestCase
from django.utils import timezone

from telemetria.catalogo import cargar_catalogo
from telemetria.models import Cliente, Gateway, Medicion, Nodo, Senal, Vehiculo

from .test_ingesta import BaseIngesta


class CatalogoTests(TestCase):
    def test_retira_las_senales_viejas_con_sus_mediciones(self):
        # Una base creada con el firmware anterior: puerta del sanitario (VL53L0X) y tres zonas de luz.
        puerta = Senal.objects.create(codigo="san.puerta_abierta", nombre="Puerta", subsistema="SAN", tipo="BOOL",
                                      mensaje=0x21, byte_inicio=0, longitud=1)
        Senal.objects.create(codigo="luz.zona2", nombre="Zona media", subsistema="LUZ", mensaje=0x30, byte_inicio=1,
                             longitud=1)
        cliente = Cliente.objects.create(nombre_comercial="Cliente de prueba")
        vehiculo = Vehiculo.objects.create(identificador="BUS-C", nombre="Autobús", cliente=cliente)
        gateway = Gateway(vehiculo=vehiculo, identificador="GW-C")
        gateway.generar_clave()
        gateway.save()
        nodo = Nodo(gateway=gateway, numero=1, nombre="Nodo")
        nodo.generar_clave()
        nodo.save()
        Medicion.objects.create(nodo=nodo, senal=puerta, valor=1, contador=1, ts=timezone.now())

        _, _, retiradas = cargar_catalogo()

        self.assertEqual(retiradas, 2)
        self.assertFalse(Senal.objects.filter(codigo__in=["san.puerta_abierta", "luz.zona2"]).exists())
        self.assertEqual(Medicion.objects.count(), 0)
        ocupado = Senal.objects.get(codigo="san.ocupado")
        self.assertEqual((ocupado.mensaje, ocupado.byte_inicio), (0x21, 0))
        self.assertEqual(list(Senal.objects.filter(subsistema="LUZ").values_list("codigo", flat=True)), ["luz.interior"])
        self.assertEqual(cargar_catalogo()[2], 0)  # la segunda vez ya no hay nada que retirar


class SanitarioYLuzTests(BaseIngesta):
    def test_decodifica_ocupado_usos_y_una_sola_luz(self):
        tramas = [self.trama(0x21, b"\x01\x00\x00\x03"), self.trama(0x30, b"\x50\x00\x00\x00")]
        respuesta = self.enviar(self.cuerpo(tramas))

        self.assertEqual(respuesta.status_code, 200)
        valores = {m.senal.codigo: m.valor for m in Medicion.objects.select_related("senal")}
        self.assertEqual(valores, {"san.ocupado": 1, "san.presencia": 0, "san.usos": 3, "luz.interior": 80})
