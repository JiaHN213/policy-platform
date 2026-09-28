from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("policies", "0012_move_relations_to_wiki_llm")]

    operations = [
        migrations.AddField(model_name="policy", name="document_role", field=models.CharField(choices=[("POLICY_BASIS", "政策依据"), ("APPLICATION_NOTICE", "申报通知"), ("APPLICATION_GUIDE", "申报指南"), ("SUPPLEMENT_NOTICE", "补充通知"), ("EXTENSION_NOTICE", "延期通知"), ("CONSULTATION_DRAFT", "征求意见稿"), ("PUBLICITY_RESULT", "结果公示"), ("FINAL_RESULT", "正式结果"), ("FUND_ALLOCATION", "资金下达"), ("OFFICIAL_INTERPRETATION", "官方解读"), ("OTHER", "其他")], db_index=True, default="OTHER", max_length=32)),
        migrations.AddField(model_name="policy", name="opportunity_level", field=models.CharField(choices=[("NONE", "没有政策机会"), ("SUPPORT_SIGNAL", "政策支持方向"), ("FORMAL_OPPORTUNITY", "正式政策机会")], db_index=True, default="NONE", max_length=24)),
        migrations.AddField(model_name="policy", name="support_signals", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="opportunity_key", field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name="opportunity", name="acquisition_method", field=models.CharField(default="OTHER", max_length=24)),
        migrations.AddField(model_name="opportunity", name="eligible_subjects", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="eligible_projects", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="eligible_products", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="support_content", field=models.TextField(blank=True)),
        migrations.AddField(model_name="opportunity", name="support_method", field=models.TextField(blank=True)),
        migrations.AddField(model_name="opportunity", name="amount", field=models.DecimalField(blank=True, decimal_places=4, max_digits=20, null=True)),
        migrations.AddField(model_name="opportunity", name="amount_unit", field=models.CharField(blank=True, max_length=30)),
        migrations.AddField(model_name="opportunity", name="percentage", field=models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True)),
        migrations.AddField(model_name="opportunity", name="max_amount", field=models.DecimalField(blank=True, decimal_places=4, max_digits=20, null=True)),
        migrations.AddField(model_name="opportunity", name="min_amount", field=models.DecimalField(blank=True, decimal_places=4, max_digits=20, null=True)),
        migrations.AddField(model_name="opportunity", name="calculation_basis", field=models.TextField(blank=True)),
        migrations.AddField(model_name="opportunity", name="requirements", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="exclusion_conditions", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="prerequisites", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="regions", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="competent_authorities", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="acceptance_authorities", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="recommendation_authorities", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="application_channels", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="missing_information", field=models.JSONField(default=list)),
        migrations.AddField(model_name="opportunity", name="evidence_details", field=models.JSONField(default=list)),
        migrations.AddConstraint(model_name="opportunity", constraint=models.UniqueConstraint(condition=~models.Q(opportunity_key=""), fields=("policy", "opportunity_key"), name="unique_policy_opportunity_key")),
    ]
