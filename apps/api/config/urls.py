from accounts.management import PrivateAccountView, UserManagementViewSet
from accounts.views import CsrfView, LoginView, LogoutView, MeView, RegisterView
from core.config_views import (
    AIModelProfileViewSet,
    SystemConfigDocumentViewSet,
    SystemConfigReleaseViewSet,
)
from core.coverage import SourceCoverageView
from core.operations import AIUsageView, PipelineStatusView
from core.views import HealthView, OverviewView, TaxonomyView
from core.worker_status import WorkerStatusView
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from enterprises.monitor import TaskMonitorViewSet
from enterprises.scoped_views import EnterpriseSettingsView, ScopedSettingsView
from enterprises.views import (
    EnterpriseViewSet,
    ProjectViewSet,
    ResearchSettingsView,
    ResearchViewSet,
)
from enterprises.watch_views import (
    RecommendationSettingsView,
    RecommendationStatisticsView,
    WatchViewSet,
)
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
from policies.recovery_views import RecoveryViewSet
from policies.search import SearchSummaryView, SearchView
from policies.search_views import SearchIndexView
from policies.views import PolicyReviewViewSet, PolicyViewSet
from quality.matching_views import MatchingObservationView, MatchingStudyViewSet
from quality.views import RunViewSet, SampleViewSet
from rest_framework.routers import SimpleRouter
from subscriptions.views import NotificationViewSet, SubscriptionViewSet

router = SimpleRouter(trailing_slash=False)
router.register("admin/users", UserManagementViewSet, basename="managed-user")
router.register("admin/task-monitor", TaskMonitorViewSet, basename="task-monitor")
router.register("matching-studies", MatchingStudyViewSet, basename="matching-study")
router.register("enterprises", EnterpriseViewSet, basename="enterprise")
router.register("enterprise-projects", ProjectViewSet, basename="enterprise-project")
router.register("enterprise-research", ResearchViewSet, basename="enterprise-research")
router.register("policy-watches", WatchViewSet, basename="policy-watch")
router.register("admin/quality/samples", SampleViewSet, basename="quality-sample")
router.register("admin/quality/runs", RunViewSet, basename="quality-run")
router.register("admin/publication-consumers", PublicationConsumptionViewSet, basename="publication-consumer")
router.register("admin/policy-enrichments", EnrichmentViewSet, basename="policy-enrichment")
router.register("admin/review-recoveries", RecoveryViewSet, basename="review-recovery")
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
    path("api/v1/admin/user-settings/<int:pk>", ScopedSettingsView.as_view()),
    path("api/v1/admin/enterprise-settings/<uuid:pk>", EnterpriseSettingsView.as_view()),
    path("api/v1/account", PrivateAccountView.as_view()),
    path("api/v1/admin/ai-usage", AIUsageView.as_view()),
    path("api/v1/admin/pipeline-status", PipelineStatusView.as_view()),
    path("api/v1/admin/worker-status", WorkerStatusView.as_view()),
    path("api/v1/admin/matching-observations", MatchingObservationView.as_view()),
    path("api/v1/admin/recommendation-settings", RecommendationSettingsView.as_view()),
    path("api/v1/admin/recommendation-statistics", RecommendationStatisticsView.as_view()),
    path("api/v1/source-coverage", SourceCoverageView.as_view()),
    path("api/v1/admin/enterprise-research-settings", ResearchSettingsView.as_view()),
    path("api/v1/health", HealthView.as_view()),
    path("api/v1/search", SearchView.as_view()),
    path("api/v1/search/summary", SearchSummaryView.as_view()),
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
