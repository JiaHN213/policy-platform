from django.contrib import admin

from .models import (
    KnowledgeBuild,
    KnowledgeLintIssue,
    KnowledgePage,
    KnowledgePageSource,
    KnowledgeRelationScan,
    KnowledgeRevision,
    RelationReviewCandidate,
)


class ReadOnlyKnowledgeAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in [
    KnowledgePage,
    KnowledgeRevision,
    KnowledgePageSource,
    KnowledgeBuild,
    KnowledgeLintIssue,
    KnowledgeRelationScan,
    RelationReviewCandidate,
]:
    admin.site.register(model, ReadOnlyKnowledgeAdmin)
