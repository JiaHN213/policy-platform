from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0004_crawlthrottle")]

    operations = [
        migrations.AddField(
            model_name="discovereditem",
            name="error_detail",
            field=models.CharField(blank=True, max_length=500),
        )
    ]
