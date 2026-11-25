"""Clientes iniciales y asignación de los autobuses que ya existían.

- Siete operadores mexicanos de autobuses foráneos que públicamente operan unidades Volvo. Solo se carga el
  nombre comercial: RFC, razón social y contactos quedan vacíos para no inventar datos. Origen DEMO.
- "Cliente de demostración": recibe los autobuses existentes (p. ej. BUS-DEMO-001), porque a partir de la
  migración 0004 todo autobús debe tener cliente.
"""
from django.db import migrations

CLIENTE_DEMO = "Cliente de demostración"
NOTA = "Operador público que usa unidades Volvo. Dato de demostración: verificar antes de usarlo en producción."

OPERADORES = [
    "Mobility ADO",
    "ETN Turistar Lujo",
    "Grupo Estrella Blanca",
    "Primera Plus",
    "Transportes y Autobuses del Pacífico (TAP)",
    "Grupo Senda",
    "Ómnibus de México",
]


def cargar(apps, schema_editor):
    Cliente = apps.get_model("telemetria", "Cliente")
    Vehiculo = apps.get_model("telemetria", "Vehiculo")
    for nombre in OPERADORES:
        Cliente.objects.get_or_create(
            nombre_comercial=nombre,
            defaults={"tipo": "OPERADOR", "segmento": "FORANEO", "origen": "DEMO", "nota": NOTA},
        )
    demo, _ = Cliente.objects.get_or_create(
        nombre_comercial=CLIENTE_DEMO,
        defaults={"tipo": "OPERADOR", "origen": "DEMO", "nota": "Dueño de los autobuses de demostración y de prueba."},
    )
    Vehiculo.objects.filter(cliente__isnull=True).update(cliente=demo)


def quitar(apps, schema_editor):
    Cliente = apps.get_model("telemetria", "Cliente")
    Vehiculo = apps.get_model("telemetria", "Vehiculo")
    Vehiculo.objects.filter(cliente__nombre_comercial=CLIENTE_DEMO).update(cliente=None)
    Cliente.objects.filter(origen="DEMO", nombre_comercial__in=[*OPERADORES, CLIENTE_DEMO],
                           vehiculos__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("telemetria", "0002_cliente"),
    ]

    operations = [
        migrations.RunPython(cargar, quitar),
    ]
