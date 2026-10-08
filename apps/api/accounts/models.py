from core.models import Record
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    permission_version = models.PositiveIntegerField(default=1)

    class Meta(AbstractUser.Meta):
        permissions = [("manage_system", "管理系统配置与运行监控")]


class Organization(Record):
    name = models.CharField(max_length=200)


class Membership(Record):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=[("member", "成员"), ("admin", "管理员")])
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "organization"], name="unique_membership")
        ]


class Entitlement(Record):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    capability = models.CharField(max_length=80)
    allowed = models.BooleanField(default=True)
    limit = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "capability"], name="unique_entitlement")
        ]
