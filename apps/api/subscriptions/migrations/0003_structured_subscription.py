from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0002_subscription_document_type")]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="target_view",
            field=models.CharField(
                choices=[
                    ("policy", "政策文件"),
                    ("opportunity", "政策机会"),
                    ("all", "政策与机会"),
                ],
                default="policy",
                max_length=20,
            ),
        ),
        migrations.AddField(model_name="subscription", name="geographic_level", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="subscription", name="province", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="subscription", name="city", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="subscription", name="business_domain", field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name="subscription", name="direction_tag", field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name="subscription", name="validity_status", field=models.CharField(blank=True, max_length=24)),
        migrations.AddField(model_name="subscription", name="opportunity_category", field=models.CharField(blank=True, max_length=24)),
        migrations.AddField(model_name="subscription", name="opportunity_status", field=models.CharField(blank=True, max_length=24)),
        migrations.AddField(model_name="subscription", name="acquisition_method", field=models.CharField(blank=True, max_length=24)),
        migrations.AddField(model_name="subscription", name="eligible_keywords", field=models.CharField(blank=True, max_length=300)),
        migrations.AddField(model_name="subscription", name="authority_keywords", field=models.CharField(blank=True, max_length=300)),
        migrations.AddField(model_name="subscription", name="has_deadline", field=models.BooleanField(blank=True, null=True)),
        migrations.AddField(model_name="subscription", name="deadline_within_days", field=models.PositiveSmallIntegerField(blank=True, null=True)),
    ]
