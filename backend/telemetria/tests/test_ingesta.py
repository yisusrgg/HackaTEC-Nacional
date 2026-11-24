import hashlib
import hmac
import json
import time

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from auditoria.models import EventoSeguridad
from auditoria.servicios import verificar_cadena
from telemetria import protocolo
from telemetria.catalogo import cargar_catalogo
from telemetria.models import Alerta, Gateway, Medicion, Nodo, Vehiculo

T = EventoSeguridad.Tipo
AMBIENTE = 0x10


def ambiente(temperatura, humedad=55.0):
    return int(round(temperatura * 10)).to_bytes(2, "big", signed=True) + int(round(humedad * 10)).to_bytes(2, "big")


class BaseIngesta(TestCase):
    def setUp(self):
        cache.clear()
        cargar_catalogo()
        vehiculo = Vehiculo.objects.create(identificador="BUS-T", nombre="Autobús de prueba")
        self.gateway = Gateway(vehiculo=vehiculo, identificador="GW-T")
        self.clave_gw = self.gateway.generar_clave()
        self.gateway.save()
        self.nodo = Nodo(gateway=self.gateway, numero=1, nombre="Nodo de prueba")
        self.claves = protocolo.derivar_claves(self.nodo.generar_clave())
        self.nodo.save()
        self.contador = 0
        self.lote = 0

    def trama(self, mensaje, plano, contador=None, numero_nodo=1):
        if contador is None:
            contador = self.contador
            self.contador += 1
        can_id = (numero_nodo << 8) | mensaje
        datos = protocolo.construir_trama(self.claves, can_id, contador, plano)
        return {"id": can_id, "d": datos.hex().upper(), "t": int(time.time() * 1000)}

    def cuerpo(self, tramas, lote=None, **estado):
        if lote is None:
            lote = self.lote
            self.lote += 1
        datos = {"gw": "GW-T", "lote": lote, "tramas": tramas, "estado": {"fw": "prueba", **estado}}
        return json.dumps(datos, separators=(",", ":")).encode()

    def enviar(self, cuerpo, firma=None, gateway="GW-T"):
        firma = firma or hmac.new(self.clave_gw, cuerpo, hashlib.sha256).hexdigest()
        return self.client.post(reverse("telemetria:ingesta"), data=cuerpo, content_type="application/json",
                                headers={"X-Gateway": gateway, "X-Firma": firma})

    def eventos(self, tipo):
        return EventoSeguridad.objects.filter(tipo=tipo).count()


class IngestaTests(BaseIngesta):
    def test_lote_valido_guarda_mediciones(self):
        respuesta = self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(23.4, 55.0))]))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["aceptadas"], 1)
        self.assertAlmostEqual(Medicion.objects.get(senal__codigo="ac.temperatura").valor, 23.4)
        self.assertAlmostEqual(Medicion.objects.get(senal__codigo="ac.humedad").valor, 55.0)
        self.gateway.refresh_from_db()
        self.nodo.refresh_from_db()
        self.assertEqual(self.gateway.ultimo_lote, 0)
        self.assertEqual(self.gateway.firmware, "prueba")
        self.assertEqual(self.nodo.ultimo_contador, 0)
        self.assertTrue(self.nodo.en_linea)

    def test_firma_invalida_se_rechaza_y_registra(self):
        respuesta = self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(20))]), firma="0" * 64)
        self.assertEqual(respuesta.status_code, 401)
        self.assertEqual(Medicion.objects.count(), 0)
        self.assertEqual(self.eventos(T.FIRMA_INVALIDA), 1)
        self.assertTrue(Alerta.objects.filter(tipo=Alerta.Tipo.SEGURIDAD, gateway=self.gateway).exists())

    def test_gateway_desconocido(self):
        self.assertEqual(self.enviar(self.cuerpo([]), gateway="GW-FALSO").status_code, 401)
        self.assertEqual(self.eventos(T.GATEWAY_DESCONOCIDO), 1)

    def test_gateway_revocado(self):
        self.gateway.activo = False
        self.gateway.save()
        self.assertEqual(self.enviar(self.cuerpo([])).status_code, 401)
        self.assertEqual(self.eventos(T.GATEWAY_REVOCADO), 1)

    def test_reenvio_identico_es_idempotente(self):
        cuerpo = self.cuerpo([self.trama(AMBIENTE, ambiente(22))])
        self.assertEqual(self.enviar(cuerpo).json()["estado"], "procesado")
        segunda = self.enviar(cuerpo)
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(segunda.json()["estado"], "duplicado")
        self.assertEqual(Medicion.objects.filter(senal__codigo="ac.temperatura").count(), 1)

    def test_lote_reutilizado_con_otro_contenido(self):
        self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(22))], lote=5))
        respuesta = self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(40))], lote=5))
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.eventos(T.LOTE_REPETIDO), 1)
        self.assertFalse(Medicion.objects.filter(senal__codigo="ac.temperatura", valor=40).exists())

    def test_lote_anterior_al_ultimo(self):
        self.enviar(self.cuerpo([], lote=10))
        self.assertEqual(self.enviar(self.cuerpo([], lote=3)).status_code, 409)

    def test_replay_de_trama(self):
        original = self.trama(AMBIENTE, ambiente(22))
        self.enviar(self.cuerpo([original]))
        respuesta = self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(23)), original]))
        self.assertEqual(respuesta.json()["rechazadas"], 1)
        self.assertEqual(self.eventos(T.REPLAY_TRAMA), 1)
        self.assertTrue(Alerta.objects.filter(clave_dedup=f"SEG_REPLAY:nodo={self.nodo.pk}").exists())

    def test_mac_invalido_y_alerta_por_rafaga(self):
        falsas = []
        for _ in range(5):
            trama = self.trama(AMBIENTE, ambiente(22))
            datos = bytearray.fromhex(trama["d"])
            datos[0] ^= 0x01
            falsas.append({**trama, "d": datos.hex()})
        respuesta = self.enviar(self.cuerpo(falsas))
        self.assertEqual(respuesta.json()["rechazadas"], 5)
        evento = EventoSeguridad.objects.get(tipo=T.MAC_INVALIDO)  # un solo evento agregado por lote
        self.assertEqual(evento.detalle["cantidad"], 5)
        self.assertTrue(Alerta.objects.filter(clave_dedup=f"SEG_MAC:nodo={self.nodo.pk}").exists())

    def test_trama_de_nodo_desconocido(self):
        respuesta = self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(22), contador=0, numero_nodo=2)]))
        self.assertEqual(respuesta.json()["rechazadas"], 1)
        self.assertEqual(self.eventos(T.NODO_DESCONOCIDO), 1)

    def test_nodo_revocado(self):
        self.nodo.activo = False
        self.nodo.save()
        respuesta = self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(22))]))
        self.assertEqual(respuesta.json()["rechazadas"], 1)
        self.assertEqual(self.eventos(T.NODO_REVOCADO), 1)

    def test_umbral_abre_y_resuelve_alerta(self):
        self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(31.5))]))
        alerta = Alerta.objects.get(tipo=Alerta.Tipo.UMBRAL_ALTO)
        self.assertEqual(alerta.estado, Alerta.Estado.ABIERTA)
        self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(24))]))
        alerta.refresh_from_db()
        self.assertEqual(alerta.estado, Alerta.Estado.RESUELTA)

    def test_alerta_no_se_duplica(self):
        self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(31)), self.trama(AMBIENTE, ambiente(32))]))
        self.assertEqual(Alerta.objects.get(tipo=Alerta.Tipo.UMBRAL_ALTO).ocurrencias, 2)

    def test_sensor_sin_dato(self):
        self.enviar(self.cuerpo([self.trama(AMBIENTE, b"\x7f\xff\xff\xff")]))
        medicion = Medicion.objects.get(senal__codigo="ac.temperatura")
        self.assertIsNone(medicion.valor)
        self.assertEqual(medicion.calidad, Medicion.Calidad.SIN_DATO)
        self.assertTrue(Alerta.objects.filter(tipo=Alerta.Tipo.SENSOR_FALLA).exists())

    def test_perdidas_y_reinicio_del_nodo(self):
        self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(22), contador=0)]))
        self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(22), contador=5)]))
        self.enviar(self.cuerpo([self.trama(AMBIENTE, ambiente(22), contador=1000)]))  # reinicio: múltiplo de 1000
        self.nodo.refresh_from_db()
        self.assertEqual(self.nodo.tramas_perdidas, 4)
        self.assertEqual(self.nodo.reinicios, 1)
        self.assertEqual(self.nodo.tramas_recibidas, 3)

    def test_lote_malformado(self):
        cuerpo = json.dumps({"gw": "GW-T", "lote": 0, "tramas": [{"id": 272, "d": "zz"}]}).encode()
        self.assertEqual(self.enviar(cuerpo).status_code, 400)
        self.assertEqual(self.eventos(T.PETICION_INVALIDA), 1)

    def test_identificador_del_cuerpo_debe_coincidir(self):
        cuerpo = json.dumps({"gw": "OTRO", "lote": 0, "tramas": []}).encode()
        self.assertEqual(self.enviar(cuerpo).status_code, 400)

    def test_estado_del_gateway_se_filtra(self):
        self.enviar(self.cuerpo([], can_err_rx=3, inyectado="<script>"))
        self.gateway.refresh_from_db()
        self.assertEqual(self.gateway.estado["can_err_rx"], 3)
        self.assertNotIn("inyectado", self.gateway.estado)

    def test_la_bitacora_sigue_integra_tras_ataques(self):
        self.enviar(self.cuerpo([]), firma="1" * 64)
        self.enviar(self.cuerpo([]), gateway="GW-X")
        self.assertTrue(verificar_cadena()["integra"])

    def test_hora_publica(self):
        respuesta = self.client.get(reverse("telemetria:hora"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertGreater(respuesta.json()["epoch_ms"], 1_600_000_000_000)
