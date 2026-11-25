from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from telemetria.models import Cliente, Vehiculo


class ClienteTests(TestCase):
    def test_migracion_carga_clientes_iniciales(self):
        demo = Cliente.objects.filter(origen=Cliente.Origen.DEMO)
        self.assertEqual(demo.count(), 8)  # 7 operadores + "Cliente de demostración"
        self.assertTrue(demo.filter(nombre_comercial="Mobility ADO").exists())
        self.assertFalse(demo.exclude(rfc="").exists(), "Los clientes demo no deben traer RFC inventado")

    def test_insert_directo_en_la_base_usa_valores_por_defecto(self):
        tabla = connection.ops.quote_name(Cliente._meta.db_table)
        columna = connection.ops.quote_name("nombre_comercial")
        with connection.cursor() as cursor:
            cursor.execute(f"INSERT INTO {tabla} ({columna}) VALUES (%s)", ["Autobuses Insertados a Mano"])
        cliente = Cliente.objects.get(nombre_comercial="Autobuses Insertados a Mano")
        self.assertEqual(cliente.origen, Cliente.Origen.MANUAL)
        self.assertEqual(cliente.tipo, Cliente.Tipo.OPERADOR)
        self.assertTrue(cliente.activo)
        self.assertEqual(cliente.rfc, "")
        self.assertIsNone(cliente.id_externo)
        self.assertIsNotNone(cliente.creado)

    def test_rfc_valido_e_invalido(self):
        Cliente(nombre_comercial="Con RFC", rfc="ABC010203XY9").full_clean()
        with self.assertRaises(ValidationError):
            Cliente(nombre_comercial="RFC malo", rfc="123").full_clean()

    def test_un_cliente_con_varios_autobuses(self):
        cliente = Cliente.objects.create(nombre_comercial="Flota Grande")
        for numero in range(3):
            Vehiculo.objects.create(identificador=f"BUS-F-{numero}", nombre=f"Unidad {numero}", cliente=cliente)
        self.assertEqual(cliente.vehiculos.count(), 3)

    def test_autobus_sin_cliente_no_se_permite(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Vehiculo.objects.create(identificador="BUS-SIN", nombre="Sin dueño")

    def test_no_se_borra_cliente_con_autobuses(self):
        cliente = Cliente.objects.create(nombre_comercial="Flota Uno")
        Vehiculo.objects.create(identificador="BUS-T-1", nombre="Unidad 1", cliente=cliente)
        with self.assertRaises(ProtectedError):
            cliente.delete()
