from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0005_discovereditem_error_detail")]

    operations = [
        migrations.AddField(
            model_name="source",
            name="schedule_mode",
            field=models.CharField(
                choices=[("interval", "按间隔检查"), ("daily", "每天定时检查")],
                default="interval",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="source",
            name="daily_check_time",
            field=models.TimeField(blank=True, null=True),
        ),
    ]
