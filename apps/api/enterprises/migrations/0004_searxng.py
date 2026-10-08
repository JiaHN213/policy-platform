from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("enterprises", "0003_enterpriseprofile_research_method_and_more")]
    operations = [
        migrations.AddField(
            model_name="researchsettings", name="searxng_url",
            field=models.CharField(default="http://searxng:8080", max_length=500),
        ),
        migrations.AlterField(
            model_name="researchsettings", name="provider",
            field=models.CharField(choices=[("tavily", "Tavily"), ("brave", "Brave Search"),
                                           ("searxng", "SearXNG（本地部署）")], default="tavily", max_length=20),
        ),
    ]
