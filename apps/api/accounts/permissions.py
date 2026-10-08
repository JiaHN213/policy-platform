from rest_framework.permissions import BasePermission


def can_manage_system(user):
    return bool(user.is_authenticated and user.is_active and user.is_staff and user.has_perm("accounts.manage_system"))


class IsSystemManager(BasePermission):
    message = "此操作仅限系统管理员。"

    def has_permission(self, request, view):
        return can_manage_system(request.user)
