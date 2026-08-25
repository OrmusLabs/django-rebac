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

        # Registers the `setting_changed` receiver that invalidates the cached backend
        # client. Importing the module is what connects the signal, so this import is the
        # registration -- without it `get_rebac_client`'s lru_cache is never cleared.
        from . import signals  # noqa: F401
