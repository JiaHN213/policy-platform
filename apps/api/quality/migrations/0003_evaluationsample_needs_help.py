from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("quality", "0002_matchingobservation_matchingstudy_matchingcase")]

    operations = [
        migrations.AlterField(
            model_name="evaluationsample",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "待人工标注"),
                    ("labeled", "已人工标注"),
                    ("needs_help", "待协助判断"),
                    ("retired", "已停用"),
                ],
                db_index=True, default="pending", max_length=20,
            ),
        ),
    ]
