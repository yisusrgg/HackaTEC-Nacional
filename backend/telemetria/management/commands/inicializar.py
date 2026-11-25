import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from tablero.roles import ADMINISTRADOR, INGENIERIA, TECNICO, crear_roles
from telemetria.catalogo import cargar_catalogo
from telemetria.flota_demo import cargar_flota_demo
from telemetria.models import Cliente, Gateway, Nodo, Vehiculo


class Command(BaseCommand):
    help = ("Crea los roles, carga el catálogo de señales y, si no existen, el vehículo, gateway y nodo de demostración "
            "y los autobuses de ejemplo de los clientes.")

    def add_arguments(self, parser):
        parser.add_argument("--sin-dispositivos", action="store_true", help="No crear dispositivos de demostración.")
        parser.add_argument("--usuarios-demo", action="store_true",
                            help="Crear un usuario por rol con contraseña aleatoria (se muestra una sola vez).")

    def handle(self, *args, **opciones):
        crear_roles()
        self.stdout.write(self.style.SUCCESS("✔ Roles: Administrador, Ingeniería, Técnico"))
        creadas, actualizadas = cargar_catalogo()
        self.stdout.write(self.style.SUCCESS(f"✔ Catálogo de señales: {creadas} nuevas, {actualizadas} actualizadas"))
        if not opciones["sin_dispositivos"]:
            self._dispositivos()
            creados = cargar_flota_demo()
            self.stdout.write(self.style.SUCCESS(f"✔ Autobuses de ejemplo de los clientes: {creados} nuevos")
                              if creados else "• Los autobuses de ejemplo de los clientes ya existen.")
        if opciones["usuarios_demo"]:
            self._usuarios()

    def _dispositivos(self):
        if Gateway.objects.exists():
            self.stdout.write("• Ya hay dispositivos registrados. Para obtener una clave nueva usa «Rotar clave» "
                              "en Tablero → Autobuses.")
            return
        with transaction.atomic():
            cliente, _ = Cliente.objects.get_or_create(
                nombre_comercial="Cliente de demostración",
                defaults={"origen": Cliente.Origen.DEMO, "nota": "Dueño de los autobuses de demostración y de prueba."},
            )
            vehiculo = Vehiculo.objects.create(identificador="BUS-DEMO-001", nombre="Autobús de demostración",
                                               cliente=cliente)
            gateway = Gateway(vehiculo=vehiculo, identificador="GW-01")
            clave_gw = gateway.generar_clave()
            gateway.save()
            nodo = Nodo(gateway=gateway, numero=1, nombre="Nodo de carrocería 1")
            clave_nodo = nodo.generar_clave()
            nodo.save()

        self.stdout.write(self.style.SUCCESS("✔ Dispositivos de demostración creados"))
        self.stdout.write(self.style.WARNING(
            "\nClaves de provisión (se muestran SOLO esta vez; cópialas al monitor serie de cada ESP32):\n"))
        self.stdout.write(f"  ESP32 #1 · nodo de sensores (nodo {nodo.numero}):\n    clave {clave_nodo.hex().upper()}\n")
        self.stdout.write(f"  ESP32 #2 · gateway ({gateway.identificador}):\n"
                          f"    id {gateway.identificador}\n    clave {clave_gw.hex().upper()}\n")
        self.stdout.write("  Si las pierdes, genera otras con «Rotar clave» en el tablero.\n")

    def _usuarios(self):
        Usuario = get_user_model()
        cuentas = [("admin_demo", ADMINISTRADOR, True), ("ingeniero_demo", INGENIERIA, False), ("tecnico_demo", TECNICO, False)]
        for nombre, rol, staff in cuentas:
            if Usuario.objects.filter(username=nombre).exists():
                self.stdout.write(f"• {nombre} ya existe (no se cambia su contraseña)")
                continue
            password = secrets.token_urlsafe(12)
            usuario = Usuario.objects.create_user(nombre, password=password, is_staff=staff)
            usuario.groups.add(Group.objects.get(name=rol))
            self.stdout.write(self.style.SUCCESS(f"✔ {nombre:<15} rol {rol:<14} contraseña: {password}"))
