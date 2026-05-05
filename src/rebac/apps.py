# rebac/apps.py
from django.apps import AppConfig


class ReBACConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rebac"  # Must match the folder name exactly
    verbose_name = "ReBAC"

    def ready(self):

        from .conf import validate_settings

        # Run validation as soon as Django starts
        validate_settings()
