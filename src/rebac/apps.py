# rebac/apps.py
from django.apps import AppConfig


class ReBACConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rebac"  # Must match the folder name exactly
    verbose_name = "ReBAC"

    def ready(self):
        from django.core import checks

        from . import checks as rebac_checks
        from .conf import validate_settings

        # Run validation as soon as Django starts
        validate_settings()

        # T3.12: register the ReBAC system check suite so `manage.py check`
        # (and every management command) validates the configuration at startup
        # instead of failures surfacing per-request. The guard flag keeps this
        # idempotent across Django versions (the internal registry shape changed
        # in 5.2, so we track our own registration state).
        if not rebac_checks._REGISTERED:
            checks.register(rebac_checks.run_rebac_system_checks)
            rebac_checks._REGISTERED = True

        # Registers the `setting_changed` receiver that invalidates the cached backend
        # client. Importing the module is what connects the signal, so this import is the
        # registration -- without it `get_rebac_client`'s lru_cache is never cleared.
        from . import signals  # noqa: F401
