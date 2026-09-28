# Generated manually for the source-wide crawl throttle.

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0003_sourcecheckrun_progress_crawlpage")]

    operations = [
        migrations.CreateModel(
            name="CrawlThrottle",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("scope", models.CharField(max_length=100, unique=True)),
                ("next_request_at", models.DateTimeField(blank=True, null=True)),
                ("blocked_until", models.DateTimeField(blank=True, null=True)),
                ("last_status_code", models.PositiveIntegerField(blank=True, null=True)),
                ("rejection_count", models.PositiveIntegerField(default=0)),
            ],
        )
    ]
