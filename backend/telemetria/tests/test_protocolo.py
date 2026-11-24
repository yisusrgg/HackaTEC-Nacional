from django.test import SimpleTestCase

from telemetria import protocolo
from telemetria.models import Medicion, Senal

V = protocolo.Veredicto


class ProtocoloTests(SimpleTestCase):
    def setUp(self):
        self.claves = protocolo.derivar_claves(bytes(range(32)))

    def test_vector_de_prueba_coincide_con_el_firmware(self):
        # El mismo valor está en firmware/lib/protocolo/protocolo.cpp (autoprueba al arrancar).
        self.assertEqual(protocolo.trama_vector_prueba().hex().upper(), "D98F7BEA0001F8FC")

    def test_ida_y_vuelta(self):
        trama = protocolo.construir_trama(self.claves, 0x120, 7, b"\x01\x02\x03\x04")
        resultado = protocolo.verificar_trama(self.claves, 0x120, trama, 6)
        self.assertIs(resultado.veredicto, V.NUEVA)
        self.assertEqual(resultado.contador, 7)
        self.assertEqual(resultado.plano, b"\x01\x02\x03\x04")

    def test_los_datos_viajan_cifrados(self):
        trama = protocolo.construir_trama(self.claves, 0x120, 7, b"\x00\x00\x00\x00")
        self.assertNotEqual(trama[:4], b"\x00\x00\x00\x00")

    def test_trama_alterada_es_invalida(self):
        trama = bytearray(protocolo.construir_trama(self.claves, 0x110, 3, b"\x00\xea\x02\x26"))
        trama[1] ^= 0x10
        self.assertIs(protocolo.verificar_trama(self.claves, 0x110, bytes(trama), 2).veredicto, V.INVALIDA)

    def test_trama_movida_a_otro_id_es_invalida(self):
        trama = protocolo.construir_trama(self.claves, 0x110, 3, b"\x00\xea\x02\x26")
        self.assertIs(protocolo.verificar_trama(self.claves, 0x111, trama, 2).veredicto, V.INVALIDA)

    def test_repeticion_se_detecta(self):
        trama = protocolo.construir_trama(self.claves, 0x110, 5, b"\x00\xea\x02\x26")
        self.assertIs(protocolo.verificar_trama(self.claves, 0x110, trama, 9).veredicto, V.ANTIGUA)

    def test_reconstruye_el_contador_al_dar_la_vuelta_de_16_bits(self):
        trama = protocolo.construir_trama(self.claves, 0x110, 0x20001, b"\x00\x00\x00\x00")
        resultado = protocolo.verificar_trama(self.claves, 0x110, trama, 0x1FFFE)
        self.assertIs(resultado.veredicto, V.NUEVA)
        self.assertEqual(resultado.contador, 0x20001)

    def test_nodo_recien_provisionado(self):
        trama = protocolo.construir_trama(self.claves, 0x110, 0, b"\x00\x00\x00\x00")
        self.assertEqual(protocolo.verificar_trama(self.claves, 0x110, trama, None).contador, 0)

    def test_otra_clave_no_verifica(self):
        otra = protocolo.derivar_claves(bytes(32))
        trama = protocolo.construir_trama(self.claves, 0x110, 1, b"\x00\x00\x00\x00")
        self.assertIs(protocolo.verificar_trama(otra, 0x110, trama, 0).veredicto, V.INVALIDA)


class DecodificacionTests(SimpleTestCase):
    def senal(self, **campos):
        base = dict(codigo="x", nombre="X", subsistema="AC", mensaje=0x10, byte_inicio=0, longitud=2, escala=1)
        return Senal(**{**base, **campos})

    def test_temperatura_negativa(self):
        s = self.senal(con_signo=True, escala=0.1, rango_min=-20, rango_max=60)
        valor, calidad = s.decodificar((-55).to_bytes(2, "big", signed=True) + b"\x00\x00")
        self.assertAlmostEqual(valor, -5.5)
        self.assertEqual(calidad, Medicion.Calidad.OK)

    def test_valor_sin_dato(self):
        s = self.senal(con_signo=True)
        self.assertEqual(s.decodificar(b"\x7f\xff\x00\x00"), (None, Medicion.Calidad.SIN_DATO))

    def test_fuera_de_rango(self):
        s = self.senal(byte_inicio=2, escala=0.1, rango_max=100)
        self.assertEqual(s.decodificar(b"\x00\x00\x07\xd0")[1], Medicion.Calidad.FUERA_RANGO)  # 200.0

    def test_formato_con_etiquetas(self):
        s = self.senal(tipo="BOOL", longitud=1, etiquetas={"0": "Cerrada", "1": "Abierta"})
        self.assertEqual(s.formatear(1), "Abierta")
        self.assertEqual(s.formatear(None), "—")
