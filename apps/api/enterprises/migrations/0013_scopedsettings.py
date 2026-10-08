import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("enterprises", "0012_researchrun_quota_category"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(
        name="ScopedSettings",
        fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("overrides", models.JSONField(default=dict)),
            ("revision", models.PositiveIntegerField(default=1)),
            ("user", models.OneToOneField(to=settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=django.db.models.deletion.CASCADE)),
            ("profile", models.OneToOneField(to="enterprises.enterpriseprofile", null=True, blank=True, on_delete=django.db.models.deletion.CASCADE)),
        ],
        options={"constraints": [models.CheckConstraint(condition=(models.Q(user__isnull=False, profile__isnull=True) | models.Q(user__isnull=True, profile__isnull=False)), name="scoped_settings_exactly_one_owner")]},
    )]
