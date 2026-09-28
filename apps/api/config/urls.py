from accounts.views import CsrfView, LoginView, LogoutView, MeView, RegisterView
from core.config_views import (
    AIModelProfileViewSet,
    SystemConfigDocumentViewSet,
    SystemConfigReleaseViewSet,
)
from core.views import HealthView, OverviewView, TaxonomyView
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from ingestion.views import DiscoveredItemViewSet, SourceRunViewSet, SourceViewSet
from knowledge.views import (
    KnowledgeAdminPageViewSet,
    KnowledgeBuildViewSet,
    KnowledgePageViewSet,
    RelationReviewCandidateViewSet,
)
from policies.catalog import (
    BatchAdminViewSet,
    OpportunityAdminViewSet,
    OpportunityViewSet,
    RelationAdminViewSet,
)
from policies.enrichment_views import EnrichmentViewSet
from policies.event_views import PublicationConsumptionViewSet
from policies.search import SearchView
from policies.search_views import SearchIndexView
from policies.views import PolicyReviewViewSet, PolicyViewSet
from quality.views import RunViewSet, SampleViewSet
from rest_framework.routers import SimpleRouter
from subscriptions.views import NotificationViewSet, SubscriptionViewSet

router = SimpleRouter(trailing_slash=False)
router.register("admin/quality/samples", SampleViewSet, basename="quality-sample")
router.register("admin/quality/runs", RunViewSet, basename="quality-run")
router.register("admin/publication-consumers", PublicationConsumptionViewSet, basename="publication-consumer")
router.register("admin/policy-enrichments", EnrichmentViewSet, basename="policy-enrichment")
router.register("policies", PolicyViewSet)
router.register("opportunities", OpportunityViewSet)
router.register("admin/opportunities", OpportunityAdminViewSet, basename="admin-opportunity")
router.register("admin/opportunity-batches", BatchAdminViewSet, basename="admin-batch")
router.register("admin/policy-relations", RelationAdminViewSet, basename="admin-relation")
router.register("subscriptions", SubscriptionViewSet)
router.register("notifications", NotificationViewSet)
router.register("admin/policies", PolicyReviewViewSet, basename="review")
router.register("admin/sources", SourceViewSet)
router.register("admin/source-check-runs", SourceRunViewSet)
router.register("admin/discovered-items", DiscoveredItemViewSet)
router.register("admin/config/releases", SystemConfigReleaseViewSet, basename="config-release")
router.register("admin/config/documents", SystemConfigDocumentViewSet, basename="config-document")
router.register("admin/ai-models", AIModelProfileViewSet, basename="ai-model")
router.register("knowledge/pages", KnowledgePageViewSet, basename="knowledge-page")
router.register(
    "admin/knowledge/pages", KnowledgeAdminPageViewSet, basename="admin-knowledge-page"
)
router.register(
    "admin/knowledge/builds", KnowledgeBuildViewSet, basename="admin-knowledge-build"
)
router.register(
    "admin/relation-review-candidates",
    RelationReviewCandidateViewSet,
    basename="admin-relation-review-candidate",
)
urlpatterns = [
    path("api/v1/health", HealthView.as_view()),
    path("api/v1/search", SearchView.as_view()),
    path("api/v1/admin/search-index", SearchIndexView.as_view()),
    path("api/v1/auth/csrf", CsrfView.as_view()),
    path("api/v1/auth/login", LoginView.as_view()),
    path("api/v1/auth/register", RegisterView.as_view()),
    path("api/v1/auth/logout", LogoutView.as_view()),
    path("api/v1/me", MeView.as_view()),
    path("api/v1/overview", OverviewView.as_view()),
    path("api/v1/taxonomies", TaxonomyView.as_view()),
    path("api/v1/", include(router.urls)),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
    path("django-admin/", admin.site.urls),
]
