from django.core.management.base import BaseCommand

from telemetria.protocolo import VECTOR_PRUEBA, trama_vector_prueba


class Command(BaseCommand):
    help = "Muestra la trama del vector de prueba; el firmware imprime la misma al arrancar si su criptografía coincide."

    def handle(self, *args, **opciones):
        v = VECTOR_PRUEBA
        self.stdout.write(f"Clave:    {v['clave'].hex().upper()}")
        self.stdout.write(f"ID CAN:   0x{v['can_id']:03X}   contador: {v['contador']}   datos: {v['plano'].hex().upper()}")
        self.stdout.write(self.style.SUCCESS(f"Trama:    {trama_vector_prueba().hex().upper()}"))
