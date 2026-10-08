from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("enterprises", "0005_researchrun_agent_snapshot_researchrun_checkpoint_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="researchsettings",
            name="agent_all_organizations",
            field=models.BooleanField(default=False),
        ),
    ]
