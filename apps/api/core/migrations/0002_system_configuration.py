import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="SystemConfigRelease",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.CharField(max_length=80, unique=True)),
                ("schema_version", models.CharField(default="1.0", max_length=20)),
                ("status", models.CharField(choices=[("draft", "草稿"), ("published", "已发布"), ("archived", "已归档")], default="draft", max_length=20)),
                ("checksum", models.CharField(blank=True, max_length=64)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_config_releases", to=settings.AUTH_USER_MODEL)),
                ("published_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="published_config_releases", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="SystemConfigDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("key", models.CharField(max_length=100)),
                ("content", models.JSONField(default=dict)),
                ("checksum", models.CharField(max_length=64)),
                ("release", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="documents", to="core.systemconfigrelease")),
            ],
            options={"ordering": ["key", "id"]},
        ),
        migrations.CreateModel(
            name="SystemConfigAudit",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("namespace", models.CharField(blank=True, max_length=100)),
                ("action", models.CharField(max_length=40)),
                ("before", models.JSONField(default=dict)),
                ("after", models.JSONField(default=dict)),
                ("reason", models.CharField(blank=True, max_length=500)),
                ("actor", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ("release", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audits", to="core.systemconfigrelease")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="systemconfigdocument",
            constraint=models.UniqueConstraint(fields=("release", "key"), name="unique_system_config_document"),
        ),
    ]
