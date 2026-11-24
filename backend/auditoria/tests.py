from django.test import TestCase

from auditoria.models import EstadoCadena, EventoSeguridad
from auditoria.servicios import registrar, verificar_cadena

T = EventoSeguridad.Tipo


class BitacoraTests(TestCase):
    def setUp(self):
        self.eventos = [registrar(T.LOGIN_OK, actor=f"usuario{i}", ip="127.0.0.1", detalle={"i": i}) for i in range(4)]

    def test_cadena_integra(self):
        resultado = verificar_cadena()
        self.assertTrue(resultado["integra"])
        self.assertEqual(resultado["revisados"], 4)

    def test_la_cadena_enlaza_los_hashes(self):
        self.assertEqual(self.eventos[1].hash_anterior, self.eventos[0].hash)
        self.assertEqual(EstadoCadena.objects.get(pk=1).ultimo_hash, self.eventos[-1].hash)

    def test_detecta_modificacion_directa_en_la_bd(self):
        EventoSeguridad.objects.filter(pk=self.eventos[1].pk).update(actor="intruso")
        resultado = verificar_cadena()
        self.assertFalse(resultado["integra"])
        self.assertEqual(resultado["evento_id"], self.eventos[1].pk)

    def test_detecta_borrado_intermedio(self):
        EventoSeguridad.objects.filter(pk=self.eventos[2].pk).delete()  # QuerySet.delete no pasa por el modelo
        resultado = verificar_cadena()
        self.assertFalse(resultado["integra"])
        self.assertEqual(resultado["evento_id"], self.eventos[3].pk)

    def test_detecta_borrado_del_ultimo(self):
        EventoSeguridad.objects.filter(pk=self.eventos[3].pk).delete()
        self.assertFalse(verificar_cadena()["integra"])

    def test_no_se_puede_modificar_ni_borrar_desde_el_modelo(self):
        evento = self.eventos[0]
        evento.actor = "otro"
        with self.assertRaises(ValueError):
            evento.save()
        with self.assertRaises(ValueError):
            evento.delete()
