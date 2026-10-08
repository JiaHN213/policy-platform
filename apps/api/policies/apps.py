from django.apps import AppConfig


class PoliciesConfig(AppConfig):
    name = "policies"

    def ready(self):
        from . import change_events  # noqa: F401
