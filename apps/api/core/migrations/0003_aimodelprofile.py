import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_system_configuration"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AIModelProfile",
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
                (
                    "purpose",
                    models.CharField(
                        choices=[
                            ("review", "政策自动审核与摘要"),
                            ("search", "自然语言检索与归纳"),
                            ("wiki_synthesis", "Wiki 知识页综合"),
                            ("wiki_relations", "政策关系发现"),
                        ],
                        max_length=40,
                        unique=True,
                    ),
                ),
                ("enabled", models.BooleanField(default=True)),
                ("base_url", models.URLField(max_length=500)),
                ("model", models.CharField(max_length=200)),
                ("api_key", models.TextField(blank=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["purpose"]},
        ),
    ]
