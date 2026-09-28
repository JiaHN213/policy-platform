import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("policies", "0009_requeue_ai_failures_and_opportunities")]

    operations = [
        migrations.CreateModel(
            name="SearchIndexState",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "singleton_key",
                    models.CharField(default="default", editable=False, max_length=20, unique=True),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("idle", "空闲"), ("syncing", "同步中"), ("failed", "同步失败")],
                        default="idle",
                        max_length=20,
                    ),
                ),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("last_attempted_at", models.DateTimeField(blank=True, null=True)),
                ("indexed_count", models.PositiveIntegerField(default=0)),
                ("last_error", models.CharField(blank=True, max_length=500)),
            ],
        )
    ]
