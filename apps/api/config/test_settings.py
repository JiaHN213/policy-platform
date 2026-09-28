"""Fast unit tests only. CI overrides the database to real PostgreSQL."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "unit-tests-only-not-for-deployment")
# Unit test fallback is isolated; TEST_DATABASE_URL opts into PostgreSQL explicitly.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite://:memory:")
os.environ.setdefault("DEBUG", "true")
os.environ["OPENSEARCH_ENABLED"] = "false"
from .settings import *  # noqa: E402,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
OBSIDIAN_EXPORT_ENABLED = False
KNOWLEDGE_LLM_ENABLED = False
