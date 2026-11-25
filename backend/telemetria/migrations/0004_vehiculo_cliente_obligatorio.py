import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("telemetria", "0003_clientes_iniciales"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vehiculo",
            name="cliente",
            field=models.ForeignKey(
                help_text="Empresa dueña del autobús. No se puede borrar un cliente que tenga autobuses.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="vehiculos",
                to="telemetria.cliente",
            ),
        ),
    ]
