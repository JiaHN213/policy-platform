import django.core.validators
from django.db import migrations, models


def set_review_concurrency(apps, schema_editor):
    apps.get_model("core", "AIModelProfile").objects.filter(purpose="review").update(
        concurrency=3
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0003_aimodelprofile")]

    operations = [
        migrations.AddField(
            model_name="aimodelprofile",
            name="concurrency",
            field=models.PositiveSmallIntegerField(
                default=1,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(4),
                ],
            ),
        ),
        migrations.RunPython(set_review_concurrency, migrations.RunPython.noop),
    ]
