import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0006_source_schedule")]

    operations = [
        migrations.AlterField(
            model_name="source",
            name="interval_minutes",
            field=models.PositiveIntegerField(
                default=1440,
                validators=[django.core.validators.MinValueValidator(30)],
            ),
        ),
    ]
