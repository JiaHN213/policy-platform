from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("enterprises", "0011_researchsettings_workflow_cache_hours_and_more")]

    operations = [
        migrations.AddField(
            model_name="researchrun", name="quota_category",
            field=models.CharField(max_length=16, default="normal", choices=[("normal", "正常使用"), ("maintenance", "系统维护")]),
        ),
        migrations.AddField(
            model_name="researchrun", name="maintenance_reason",
            field=models.CharField(max_length=300, blank=True),
        ),
    ]
