from accounts.models import Entitlement, Membership, Organization, User
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from ingestion.models import DiscoveredItem, Source, SourceCheckRun
from policies.models import DocumentSnapshot, Evidence, Policy, PublicationEvent
from subscriptions.models import Notification, Subscription

from .models import AuditRecord

admin.site.site_header = "政策观察 · 运营后台"
admin.site.site_title = "政策观察"
admin.site.register(User, UserAdmin)
admin.site.register(Organization)
admin.site.register(Membership)
admin.site.register(Entitlement)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in [
    Policy,
    DocumentSnapshot,
    Evidence,
    PublicationEvent,
    AuditRecord,
    Notification,
    Subscription,
    SourceCheckRun,
    DiscoveredItem,
    Source,
]:
    admin.site.register(model, ReadOnlyAdmin)
