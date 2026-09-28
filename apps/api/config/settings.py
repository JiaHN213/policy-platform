from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[3]
env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "core",
    "accounts",
    "policies",
    "subscriptions",
    "ingestion",
    "analysis",
    "knowledge",
    "quality",
]
AUTH_USER_MODEL = "accounts.User"
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.RequestIDMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": env.db("DATABASE_URL")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "core.pagination.PagePagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "core.errors.exception_handler",
    "DEFAULT_THROTTLE_RATES": {"login": "10/min", "register": "5/hour", "search": "30/min"},
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
SPECTACULAR_SETTINGS = {
    "ENUM_NAME_OVERRIDES": {
        "PolicyStatusEnum": "policies.models.Policy.Status",
        "ValidityStatusEnum": "policies.taxonomy.ValidityStatus",
        "GeographicLevelEnum": "policies.models.Policy.GeographicLevel",
        "SourceGradeEnum": "policies.models.Policy.SourceGrade",
        "FormalSourceGradeEnum": "policies.search.FORMAL_GRADE_CHOICES",
        "OpportunityStatusEnum": "policies.taxonomy.OpportunityStatus",
        "VerificationStatusEnum": "policies.taxonomy.VerificationStatus",
        "DocumentTypeEnum": "policies.models.Policy.DocumentType",
        "PublicationDocumentTypeEnum": "policies.views.PUBLICATION_DOCUMENT_TYPES",
        "KnowledgePageStatusEnum": "knowledge.models.KnowledgePage.Status",
        "KnowledgeBuildStatusEnum": "knowledge.models.KnowledgeBuild.Status",
    },
    "TITLE": "政策观察 API",
    "VERSION": "0.1.0",
    "COMPONENT_SPLIT_REQUEST": True,
}
CELERY_BROKER_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/0")
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 270
LOCAL_WORKER = env.bool("LOCAL_WORKER", default=False) and DEBUG
KNOWLEDGE_SYNC_INTERVAL_MINUTES = max(
    5, env.int("KNOWLEDGE_SYNC_INTERVAL_MINUTES", default=30)
)
CELERY_BEAT_SCHEDULE = {
    "evaluate-policy-quality": {"task": "quality.tasks.evaluate_daily", "schedule": 3600.0},
    "enrich-policy-metadata": {"task": "policies.tasks.dispatch_enrichment", "schedule": 60.0},
    "import-discovered-policies": {"task": "ingestion.tasks.dispatch_imports", "schedule": 30.0},
    "dispatch-due-sources": {"task": "ingestion.tasks.dispatch_due_sources", "schedule": 60.0},
    "process-publication-consumers": {
        "task": "policies.event_tasks.dispatch_publication_consumers", "schedule": 15.0,
    },
    "deliver-opportunity-deadline-reminders": {
        "task": "subscriptions.tasks.deliver_deadline_reminders",
        "schedule": 300.0,
    },
    "sync-policy-knowledge": {
        "task": "knowledge.tasks.dispatch_knowledge_builds",
        "schedule": KNOWLEDGE_SYNC_INTERVAL_MINUTES * 60.0,
    },
    "sync-policy-search-index": {
        "task": "policies.search_tasks.sync_opensearch",
        "schedule": 60.0,
    },
}
OPENSEARCH_URL = env("OPENSEARCH_URL", default="http://127.0.0.1:9200")
OPENSEARCH_INDEX = "policies-v1"
OPENSEARCH_ENABLED = env.bool("OPENSEARCH_ENABLED", default=False)
OPENSEARCH_USERNAME = env("OPENSEARCH_USERNAME", default="")
OPENSEARCH_PASSWORD = env("OPENSEARCH_PASSWORD", default="")
OPENSEARCH_VERIFY_CERTS = env.bool("OPENSEARCH_VERIFY_CERTS", default=True)
OPENSEARCH_TIMEOUT_SECONDS = max(1.0, env.float("OPENSEARCH_TIMEOUT_SECONDS", default=5.0))
S3_ENDPOINT = env("S3_ENDPOINT", default="http://127.0.0.1:9000")
S3_ACCESS_KEY = env("S3_ACCESS_KEY", default="")
S3_SECRET_KEY = env("S3_SECRET_KEY", default="")
S3_BUCKET = env("S3_BUCKET", default="policy-originals")
ORIGINAL_STORAGE_BACKEND = env(
    "ORIGINAL_STORAGE_BACKEND", default="local" if LOCAL_WORKER else "s3"
)
ORIGINAL_STORAGE_ROOT = BASE_DIR / ".local" / "originals"
AI_BASE_URL = env("AI_BASE_URL", default="")
AI_API_KEY = env("AI_API_KEY", default="")
AI_MODEL = env("AI_MODEL", default="")
AI_REVIEW_CONCURRENCY = max(1, min(4, env.int("AI_REVIEW_CONCURRENCY", default=3)))
KNOWLEDGE_LLM_ENABLED = env.bool("KNOWLEDGE_LLM_ENABLED", default=True)
KNOWLEDGE_RELATION_AUDIT_ENABLED = env.bool(
    "KNOWLEDGE_RELATION_AUDIT_ENABLED", default=True
)
KNOWLEDGE_RELATION_SCANS_PER_BUILD = max(
    1, env.int("KNOWLEDGE_RELATION_SCANS_PER_BUILD", default=25)
)
OBSIDIAN_EXPORT_ENABLED = env.bool("OBSIDIAN_EXPORT_ENABLED", default=True)
OBSIDIAN_EXPORT_DIR = env(
    "OBSIDIAN_EXPORT_DIR", default=str(BASE_DIR / "output" / "obsidian-vault")
)
POLICY_ALLOW_VPN_FAKE_IP = env.bool("POLICY_ALLOW_VPN_FAKE_IP", default=False)
POLICY_CRAWL_PROXY_URL = env("POLICY_CRAWL_PROXY_URL", default="")
POLICY_CRAWL_ALLOW_LEGACY_HTTP = env.bool(
    "POLICY_CRAWL_ALLOW_LEGACY_HTTP", default=False
)
POLICY_CRAWLER_CONTACT = env("POLICY_CRAWLER_CONTACT", default="")
POLICY_CRAWL_MIN_INTERVAL_SECONDS = max(
    1.0, env.float("POLICY_CRAWL_MIN_INTERVAL_SECONDS", default=5.0)
)
POLICY_CRAWL_JITTER_SECONDS = max(0.0, env.float("POLICY_CRAWL_JITTER_SECONDS", default=3.0))
POLICY_CRAWL_RATE_LIMIT_COOLDOWN_MINUTES = max(
    30, env.int("POLICY_CRAWL_RATE_LIMIT_COOLDOWN_MINUTES", default=360)
)
POLICY_CRAWL_FORBIDDEN_COOLDOWN_MINUTES = max(
    60, env.int("POLICY_CRAWL_FORBIDDEN_COOLDOWN_MINUTES", default=1440)
)
POLICY_CRAWL_INCREMENTAL_LOOKBACK_DAYS = max(
    1, env.int("POLICY_CRAWL_INCREMENTAL_LOOKBACK_DAYS", default=3)
)
POLICY_CRAWL_SOURCE_INTERVAL_MINUTES = max(
    60, env.int("POLICY_CRAWL_SOURCE_INTERVAL_MINUTES", default=1440)
)
POLICY_IMPORT_LEASE_MINUTES = max(15, env.int("POLICY_IMPORT_LEASE_MINUTES", default=60))
OPPORTUNITY_CLOSING_SOON_DAYS = max(1, env.int("OPPORTUNITY_CLOSING_SOON_DAYS", default=7))
CHECKPOINT_DATABASE_URL = env("CHECKPOINT_DATABASE_URL", default=env("DATABASE_URL"))
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "core.middleware.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
